from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

import bpy
import importlib
import os
import bmesh
import base64
import struct
import mathutils

# For testing purposes. Always set to False if not running in script editor.
from . import sdf_classes
from . import import_geo

# Reload it just in case something changed!
importlib.reload(sdf_classes)
importlib.reload(import_geo)


def _add_import_diagnostic(scene, severity, scope, target, message):
    diagnostics = getattr(scene, "bz_import_diagnostics", None)
    if diagnostics is None:
        return
    item = diagnostics.add()
    item.severity = severity
    item.scope = scope
    item.target = target
    item.message = message


def _is_valid_legacy_geo_name(name):
    if not name:
        return False
    lowered = name.lower()
    if lowered.startswith("null"):
        return False
    if len(name) > 8:
        return False
    for char in name:
        if ord(char) < 32 or ord(char) > 126:
            return False
        if not (char.isalnum() or char == "_"):
            return False
    return True


def _is_valid_sdf_geo_slot(geo):
    if not _is_valid_legacy_geo_name(getattr(geo, "name", "")):
        return False
    parent = getattr(geo, "parent", "")
    if parent.lower() != "world" and not _is_valid_legacy_geo_name(parent):
        return False
    geo_type = int(getattr(geo, "type", -1))
    return 0 <= geo_type <= 100


def _diagnostic_name(name):
    if not name:
        return "<blank>"
    cleaned = "".join(char if 32 <= ord(char) <= 126 else "?" for char in str(name))
    return cleaned or "<blank>"


def _capture_unknown_tail(scene, fileContent, position):
    """Preserve any unrecognized chunks after the known SDF sections."""
    preserved_store = getattr(scene, "bz_preserved_chunks", None)
    if preserved_store is None:
        return
    preserved_store.clear()
    data = bytes(fileContent)
    found_tags = []
    while position + 8 <= len(data):
        tag = data[position : position + 4]
        try:
            declared = struct.unpack_from("<i", data, position + 4)[0]
        except Exception:
            break
        if declared >= 8 and position + declared <= len(data):
            payload = data[position : position + declared]
        else:
            payload = data[position:]
            declared = len(payload)
        entry = preserved_store.add()
        entry.tag = tag.decode("ascii", "ignore")
        entry.payload_b64 = base64.b64encode(payload).decode("ascii")
        found_tags.append(entry.tag)
        position += max(8, declared)
    if found_tags:
        _add_import_diagnostic(
            scene,
            "INFO",
            "Chunks",
            ", ".join(found_tags[:6]),
            f"{len(found_tags)} unrecognized chunk(s) after the SGEO/ANIM sections were preserved verbatim for round-trip safety.",
        )


def load(
    context,
    filepath,
    *,
    ImportAnimations=True,
    PreserveFaceColors=True,
    ImportMapTextures=False,
    MapTextureDirectory="",
    MapTextureZFS="",
):
    EXIT = (
        sdf_classes.EXITSection()
    )  # We going to be using this class to read through exit sections.
    GEOList = []
    SDFHeader = sdf_classes.SDFHeader()
    SDFC = sdf_classes.SDFCHeader()
    SGEO = sdf_classes.SGEOHeader()
    ANIM = sdf_classes.ANIMHeader()
    ANIMelements = []
    ANIMorientations = {}
    ANIMrotations = []
    ANIMtranslations2 = []
    ANIMpositions = []
    anim_found = False
    # We need to act if we can't find the GEO.
    if not os.path.exists(filepath):
        raise Exception(filepath + " was not found!")
        return {"FINISHED"}

    # Open the SDF file.
    with open(filepath, mode="rb") as file:  # b is important -> binary
        # Read the file we opened.
        fileContent = file.read()
        position = 0
        scene = context.scene
        if hasattr(scene, "bz_import_diagnostics"):
            scene.bz_import_diagnostics.clear()

        # Read the VDF header information.
        position = SDFHeader.Read(fileContent, position)
        # If we don't see some basic matches, we know something is wrong.
        if SDFHeader.BWDHeader != b"BWD2" or SDFHeader.REVHeader != b"REV\0":
            raise Exception("This file is not a SDF file or is corrupted!")

        # Read SDFC header.
        position = SDFC.Read(fileContent, position)
        _add_import_diagnostic(
            scene,
            "INFO",
            "SDF",
            os.path.basename(filepath),
            f"Structure '{SDFC.name}', type {SDFC.structuretype}.",
        )

        # Read SGEO header.
        position = SGEO.Read(fileContent, position)
        _add_import_diagnostic(
            scene,
            "INFO",
            "SGEO",
            "slots",
            f"{SGEO.geocount} GEO slots per LOD band across 6 SDF bands.",
        )

        for LOD in range(6):
            for i in range(SGEO.geocount):
                GEO = sdf_classes.GEOData()
                position = GEO.Read(fileContent, position)
                GEOList.append(GEO)
        import struct

        sectionname = struct.unpack("4s", fileContent[position : position + 4])
        if sectionname[0] == b"ANIM":
            anim_found = True
            position = ANIM.Read(fileContent, position)
            for i in range(ANIM.elementscount):
                Element = sdf_classes.ANIMElement()
                position = Element.Read(fileContent, position)
                ANIMelements.append(Element)
            for i in range(ANIM.orientationscount):
                Orientation = sdf_classes.ANIMOrientation()
                position = Orientation.Read(fileContent, position)
                ANIMorientations.update({Orientation.name: Orientation})
            for i in range(ANIM.rotationcount):
                Rotation = sdf_classes.ANIMRotation()
                position = Rotation.Read(fileContent, position)
                ANIMrotations.append(Rotation)
            for i in range(ANIM.translation2count):
                Translation = sdf_classes.ANIMTranslation2()
                position = Translation.Read(fileContent, position)
                ANIMtranslations2.append(Translation)
            for i in range(ANIM.positioncount):
                Position = sdf_classes.ANIMPosition()
                position = Position.Read(fileContent, position)
                ANIMpositions.append(Position)
            _add_import_diagnostic(
                scene,
                "INFO",
                "ANIM",
                ANIM.name,
                (
                    f"{ANIM.elementscount} elements, {ANIM.orientationscount} orientations, "
                    f"{ANIM.rotationcount} rotations, {ANIM.translation2count} Translation2 keys, "
                    f"{ANIM.positioncount} position keys."
                ),
            )
            if ANIM.translation2count > 0:
                _add_import_diagnostic(
                    scene,
                    "INFO",
                    "ANIM",
                    "Translation2",
                    "This file uses the niche Translation2 position track.",
                )
            position = EXIT.Read(fileContent, position)

        # Preserve anything we do not model after the known sections.
        _capture_unknown_tail(scene, fileContent, position)

        if anim_found:
            scene.SDFVDFPropertyGroup.UseAdvancedAnimHeader = True
            scene.SDFVDFPropertyGroup.AnimNull2 = int(ANIM.null2)
            scene.SDFVDFPropertyGroup.AnimUnknown2 = int(ANIM.unknown2)
            try:
                scene.SDFVDFPropertyGroup.AnimReserved = tuple(
                    int(v) for v in ANIM._reserved[:5]
                )
            except Exception:
                scene.SDFVDFPropertyGroup.AnimReserved = (0, 0, 0, 0, 0)
            scene.SDFVDFPropertyGroup.UseTranslation2Track = bool(
                ANIM.translation2count > 0
            )
        else:
            scene.SDFVDFPropertyGroup.UseAdvancedAnimHeader = False
            scene.SDFVDFPropertyGroup.UseTranslation2Track = False

        # Load all the geos we now know about.
        OBJList = {}
        currentlod = 0
        currentgeo = 0
        skipped_slots = 0
        skipped_samples = []
        for GEO in GEOList:
            if _is_valid_sdf_geo_slot(GEO):
                geofilename = os.path.dirname(filepath) + "/" + GEO.name + ".geo"

                # This code is mostly for Linux. This will allow us to search for a file if it doesn't exist with the file's correct capitalization.
                if not os.path.exists(geofilename):
                    for root, dirs, files in os.walk(os.path.dirname(geofilename)):
                        for afile in files:
                            if (GEO.name + ".geo").lower() == afile.lower():
                                geofilename = os.path.join(
                                    os.path.dirname(geofilename), afile.lower()
                                )
                                break

                newobj = None
                try:
                    newobj = import_geo.geoload(
                        context,
                        geofilename,
                        PreserveFaceColors=PreserveFaceColors,
                        ImportMapTextures=ImportMapTextures,
                        map_base_dir=os.path.dirname(filepath),
                        MapTextureDirectory=MapTextureDirectory,
                        MapTextureZFS=MapTextureZFS,
                    )
                except Exception as exc:
                    _add_import_diagnostic(
                        scene,
                        "WARNING",
                        "SGEO",
                        GEO.name,
                        f"Could not load referenced GEO file: {exc}",
                    )

                if newobj != None:
                    geolod = currentlod
                    if currentlod == 0:
                        geolod = 1
                    elif currentlod == 4:
                        geolod = 2
                    elif currentlod == 8:
                        geolod = 3
                    else:
                        geolod = 1

                    newobj.GEOPropertyGroup["GEOType"] = GEO.type
                    newobj.GEOPropertyGroup["GEOFlags"] = GEO.geoflags
                    newobj.GEOPropertyGroup["GeoCenterX"] = GEO.geocenter[0]
                    newobj.GEOPropertyGroup["GeoCenterY"] = GEO.geocenter[1]
                    newobj.GEOPropertyGroup["GeoCenterZ"] = GEO.geocenter[2]
                    newobj.GEOPropertyGroup["SphereRadius"] = GEO.sphereradius
                    newobj.GEOPropertyGroup["BoxHalfHeightX"] = GEO.boxhalfheight[0]
                    newobj.GEOPropertyGroup["BoxHalfHeightY"] = GEO.boxhalfheight[1]
                    newobj.GEOPropertyGroup["BoxHalfHeightZ"] = GEO.boxhalfheight[2]
                    newobj.GEOPropertyGroup["SDFDDR"] = GEO.ddr
                    newobj.GEOPropertyGroup["SDFX"] = GEO.x
                    newobj.GEOPropertyGroup["SDFY"] = GEO.y
                    newobj.GEOPropertyGroup["SDFZ"] = GEO.z
                    newobj.GEOPropertyGroup["SDFTime"] = GEO.time
                    blenobj = sdf_classes.BlenderObject(newobj, GEO)
                    blenobj.obj_index = currentgeo
                    blenobj.obj_lod = geolod
                    OBJList.update({GEO.name.lower(): blenobj})
            else:
                if not str(getattr(GEO, "name", "")).lower().startswith("null"):
                    skipped_slots += 1
                    if len(skipped_samples) < 5:
                        skipped_samples.append(
                            f"{_diagnostic_name(GEO.name)} / type {getattr(GEO, 'type', '')}"
                        )
            currentgeo = currentgeo + 1
            if currentgeo == SGEO.geocount:
                currentgeo = 0
                currentlod = currentlod + 1

        if skipped_slots:
            _add_import_diagnostic(
                scene,
                "WARNING",
                "SGEO",
                "skipped slots",
                f"Skipped {skipped_slots} invalid or non-legacy GEO slots: {', '.join(skipped_samples)}.",
            )

        for Model in OBJList.values():
            # Are we not parented to the world and is there a parent that exists?
            if (
                Model.geo.parent.lower() != "world"
                and Model.geo.parent.lower() in OBJList
            ):
                # Define what our parent is for easy access.
                Parent = OBJList[Model.geo.parent.lower()]

                # Parent object to their parent if it exists.
                # print(Model.geo.name + ' parented to ' + Parent.geo.name)
                Model.object.parent = Parent.object
            # Are we not parented to the world and is there no parent that exists?
            elif (
                Model.geo.parent.lower() != "world"
                and not Model.geo.parent.lower() in OBJList
            ):
                Parent = None
                # Define who the parent is.
                stringlist = list(Model.geo.parent.lower())
                stringlist[3] = "1"
                lowerlodparent = "".join(stringlist)
                if lowerlodparent in OBJList.keys():
                    Parent = OBJList[lowerlodparent]

                # Parent object to their parent if it exists.
                # print(Model.geo.name + ' parented to ' + Parent.geo.name)
                if Parent != None:
                    Model.object.parent = Parent.object

        Matrix = mathutils.Matrix
        Vector = mathutils.Vector
        # Position childless things first.
        for Model in OBJList.values():
            if Model.object.parent == None:
                object = Model.object
                geo = Model.geo
                mat = Matrix()
                mat[0][0:3] = geo.matrix[0], geo.matrix[1], geo.matrix[2]
                mat[1][0:3] = geo.matrix[3], geo.matrix[4], geo.matrix[5]
                mat[2][0:3] = geo.matrix[6], geo.matrix[7], geo.matrix[8]
                rotation = mat.to_euler()
                rotation[:] = rotation[0], rotation[2], rotation[1]
                # Convert the rotation degrees. Battlezone is a little weird with this one. It needs to be flipped to ZYX and then Z and Y need to be flipped. Mind you we also already flipped Z and Y with the geos on importing.
                object.rotation_mode = "YZX"
                object.rotation_euler = rotation
                # Make sure you flip the position a bit. Its not XYZ, that is for sure.
                object.location = Vector(
                    (geo.matrix[9], geo.matrix[11], geo.matrix[10])
                )

        # Position children to their parents.
        for Model in OBJList.values():
            if Model.object.parent != None:
                object = Model.object
                geo = Model.geo
                mat = Matrix()
                mat[0][0:3] = geo.matrix[0], geo.matrix[1], geo.matrix[2]
                mat[1][0:3] = geo.matrix[3], geo.matrix[4], geo.matrix[5]
                mat[2][0:3] = geo.matrix[6], geo.matrix[7], geo.matrix[8]
                rotation = mat.to_euler()
                rotation[:] = rotation[0], rotation[2], rotation[1]
                # Convert the rotation degrees. Battlezone is a little weird with this one. It needs to be flipped to ZYX and then Z and Y need to be flipped. Mind you we also already flipped Z and Y with the geos on importing.
                object.rotation_mode = "YZX"
                object.rotation_euler = rotation
                # Make sure you flip the position a bit. Its not XYZ, that is for sure.
                object.location = Vector(
                    (geo.matrix[9], geo.matrix[11], geo.matrix[10])
                )

        # Take our VDF information and load it into the scene.
        scene.SDFVDFPropertyGroup["Name"] = SDFC.name
        scene.SDFVDFPropertyGroup["StructureType"] = SDFC.structuretype
        scene.SDFVDFPropertyGroup["LOD1"] = SDFC.lod1dist
        scene.SDFVDFPropertyGroup["LOD2"] = SDFC.lod2dist
        scene.SDFVDFPropertyGroup["LOD3"] = SDFC.lod3dist
        scene.SDFVDFPropertyGroup["LOD4"] = SDFC.lod4dist
        scene.SDFVDFPropertyGroup["LOD5"] = SDFC.lod5dist
        scene.SDFVDFPropertyGroup["Defensive"] = int(
            getattr(SDFC, "ddr", SDFC.defensive)
        )
        scene.SDFVDFPropertyGroup["DeathExplosion"] = SDFC.explosioneffect
        scene.SDFVDFPropertyGroup["DeathSound"] = SDFC.explosionsound

        # Clear old animation elements if they exist.
        scene.AnimationCollection.clear()
        if ImportAnimations:
            # Take our animation elements and load them into the scene.
            for element in ANIMelements:
                item = scene.AnimationCollection.add()
                item.Index = element.index
                item.Start = element.start
                item.Length = element.length
                item.Loop = element.loop
                item.Speed = element.speed
                item.UseCustomUnknownGeoMask = True
                try:
                    item.UnknownGeoMask = tuple(int(v) for v in element.unknowngeoflag)
                except Exception:
                    item.UnknownGeoMask = (0,) * 32

            EndFrame = 0

            # Load the animation data we have already read!
            for Model in OBJList.values():
                geoname = Model.geo.name
                if geoname in ANIMorientations:
                    modelanim = ANIMorientations[geoname]
                    # Preserve tagANIMOBJ_MESH.flags across round trips.
                    if hasattr(Model.object.GEOPropertyGroup, "ANIMOrientationFlags"):
                        Model.object.GEOPropertyGroup.ANIMOrientationFlags = int(
                            getattr(modelanim, "unknown", 0)
                        )
                    # Check for rotation animation data..
                    if modelanim.rotationcount > 0:
                        for index in range(
                            modelanim.rotationindex,
                            modelanim.rotationindex + modelanim.rotationcount,
                        ):
                            RotQuaternion = mathutils.Quaternion(
                                (
                                    ANIMrotations[index].translate[0],
                                    ANIMrotations[index].translate[1],
                                    ANIMrotations[index].translate[3],
                                    ANIMrotations[index].translate[2],
                                )
                            )
                            RotEuler = RotQuaternion.to_euler("XYZ")
                            Model.object.rotation_mode = "XYZ"
                            Model.object.rotation_euler = RotEuler
                            Model.object.keyframe_insert(
                                "rotation_euler", frame=ANIMrotations[index].frame
                            )
                            if ANIMrotations[index].frame > EndFrame:
                                EndFrame = ANIMrotations[index].frame
                    # Check for position animation data.
                    if modelanim.positioncount > 0:
                        for index in range(
                            modelanim.positionindex,
                            modelanim.positionindex + modelanim.positioncount,
                        ):
                            Model.object.location = (
                                ANIMpositions[index].translate[0],
                                ANIMpositions[index].translate[2],
                                ANIMpositions[index].translate[1],
                            )
                            Model.object.keyframe_insert(
                                data_path="location", frame=ANIMpositions[index].frame
                            )
                            if ANIMpositions[index].frame > EndFrame:
                                EndFrame = ANIMpositions[index].frame

                    if modelanim.translation2count > 0:
                        for index in range(
                            modelanim.translation2index,
                            modelanim.translation2index + modelanim.translation2count,
                        ):
                            Model.object.scale = (
                                ANIMtranslations2[index].translate[0],
                                ANIMtranslations2[index].translate[2],
                                ANIMtranslations2[index].translate[1],
                            )
                            Model.object.keyframe_insert(
                                data_path="scale", frame=ANIMtranslations2[index].frame
                            )
                            if ANIMtranslations2[index].frame > EndFrame:
                                EndFrame = ANIMtranslations2[index].frame

            # Set the animation to the first frame.
            scene.frame_set(0)
            # Start at 0 of course.
            scene.frame_start = 0
            # End at the last frame of the animation.
            scene.frame_end = EndFrame

    return {"FINISHED"}


# If being run in the Script editor allow us to test load something.
if __name__ == "__main__":
    load(bpy.context, "C:/BattlezoneData/bzone.zfs/abspow.sdf")
