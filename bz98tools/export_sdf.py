from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

import bpy
import importlib
import math
import mathutils
import os

from . import sdf_classes
from . import export_geo

# Reload it just in case something changed!
importlib.reload(sdf_classes)
importlib.reload(export_geo)


# Fixes failures to go by battlezone naming conventions.
def fixgeoname(name, lod):
    geofilename = list(name)
    if len(geofilename) > 8:
        geofilename = geofilename[0:8]
    if lod in (1, 2, 3):
        geofilename[3] = str(lod)
    else:
        geofilename[3] = "3"
    geofilename[4] = "1"
    return "".join(geofilename)


def _iter_action_fcurves(action):
    fcurves = getattr(action, "fcurves", None)
    if fcurves is not None:
        yield from fcurves
        return

    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for channelbag in getattr(strip, "channelbags", []):
                yield from getattr(channelbag, "fcurves", [])


def GenerateGEOCollisions(object):
    # Get the active object.
    obj = object
    # Get ready...
    minx, miny, minz, maxx, maxy, maxz, maxoverall = [0.0] * 7
    for vert in obj.data.vertices:
        if vert.co.x < minx:
            minx = vert.co.x
        if vert.co.x > maxx:
            maxx = vert.co.x
        if vert.co.y < minz:
            minz = vert.co.y
        if vert.co.y > maxz:
            maxz = vert.co.y
        if vert.co.z < miny:
            miny = vert.co.z
        if vert.co.z > maxy:
            maxy = vert.co.z
        # Get maximum vertice distance to generate sphere radius.
        for value in vert.co:
            if abs(value) > maxoverall:
                maxoverall = abs(value)

    obj.GEOPropertyGroup.SphereRadius = maxoverall

    obj.GEOPropertyGroup.GeoCenterX = (minx + maxx) / 2
    obj.GEOPropertyGroup.GeoCenterY = (miny + maxy) / 2
    obj.GEOPropertyGroup.GeoCenterZ = (minz + maxz) / 2

    if abs(minx - obj.GEOPropertyGroup.GeoCenterX) >= abs(
        maxx - obj.GEOPropertyGroup.GeoCenterX
    ):
        obj.GEOPropertyGroup.BoxHalfHeightX = abs(
            minx - obj.GEOPropertyGroup.GeoCenterX
        )
    else:
        obj.GEOPropertyGroup.BoxHalfHeightX = abs(
            maxx - obj.GEOPropertyGroup.GeoCenterX
        )

    if abs(miny - obj.GEOPropertyGroup.GeoCenterY) >= abs(
        maxy - obj.GEOPropertyGroup.GeoCenterY
    ):
        obj.GEOPropertyGroup.BoxHalfHeightY = abs(
            miny - obj.GEOPropertyGroup.GeoCenterY
        )
    else:
        obj.GEOPropertyGroup.BoxHalfHeightY = abs(
            maxy - obj.GEOPropertyGroup.GeoCenterY
        )

    if abs(minz - obj.GEOPropertyGroup.GeoCenterZ) >= abs(
        maxz - obj.GEOPropertyGroup.GeoCenterZ
    ):
        obj.GEOPropertyGroup.BoxHalfHeightZ = abs(
            minz - obj.GEOPropertyGroup.GeoCenterZ
        )
    else:
        obj.GEOPropertyGroup.BoxHalfHeightZ = abs(
            maxz - obj.GEOPropertyGroup.GeoCenterZ
        )


def _iter_export_objects(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return list(bpy.data.objects)
    return list(scene.objects)


def export(
    context,
    *,
    filepath,
    ExportAnimations=True,
    ExportSDFOnly=False,
    face_plane_mode="CURRENT"
):
    """
    We are going to use a bunch of classes to write data and encapsulate it.
    First we'll initialize a bunch of them here.
    """
    EXIT = (
        sdf_classes.EXITSection()
    )  # We going to be using this class to read through exit sections.
    SDFHeader = sdf_classes.SDFHeader()
    SDFC = sdf_classes.SDFCHeader()
    SGEO = sdf_classes.SGEOHeader()
    ANIM = sdf_classes.ANIMHeader()

    NULLGEO = sdf_classes.GEOData()
    NULLGEO.name = "NULL"
    NULLGEO.matrix = [0] * 12
    NULLGEO.parent = "NULL"
    NULLGEO.geocenter = [0] * 3
    NULLGEO.sphereradius = 0
    NULLGEO.boxhalfheight = [0] * 3
    NULLGEO.type = 0
    NULLGEO.geoflags = 0
    NULLGEO.ddr = 50000
    NULLGEO.x = 0.0
    NULLGEO.y = 0.0
    NULLGEO.z = 0.0
    NULLGEO.time = 0.0

    """
    Variables to keep track of.
    """
    # Throw special classes designed to keep track of ALL THIS CRAP in a list!
    blenderobjects = {}
    # Inner and Outer collision objects we'll detect later.
    collisioninner = None
    collisionouter = None
    # Animation data that we will read later.
    ANIMElements = []
    ANIMOrientations = []
    ANIMRotations = []
    ANIMTranslations = []
    ANIMPositions = []
    # Counters
    rot_index = 0
    trans2_index = 0
    pos_index = 0
    # What is the amount of geo slots needed per LOD? We'll calculate this later.
    lodcount = 0
    use_translation2 = bool(
        getattr(context.scene.SDFVDFPropertyGroup, "UseTranslation2Track", False)
    )

    """
    Create/Load SDFC information.
    Will be used in the write process later.
    """
    # Set SDFC information.
    SDFC.name = context.scene.SDFVDFPropertyGroup.Name
    SDFC.structuretype = context.scene.SDFVDFPropertyGroup.StructureType
    SDFC.lod1dist = context.scene.SDFVDFPropertyGroup.LOD1
    SDFC.lod2dist = context.scene.SDFVDFPropertyGroup.LOD2
    SDFC.lod3dist = context.scene.SDFVDFPropertyGroup.LOD3
    SDFC.lod4dist = context.scene.SDFVDFPropertyGroup.LOD4
    SDFC.lod5dist = context.scene.SDFVDFPropertyGroup.LOD5
    SDFC.ddr = int(context.scene.SDFVDFPropertyGroup.Defensive)
    SDFC.defensive = SDFC.ddr
    SDFC.explosioneffect = context.scene.SDFVDFPropertyGroup.DeathExplosion
    SDFC.explosionsound = context.scene.SDFVDFPropertyGroup.DeathSound

    """
    Find collisions
    Find objects and determine/create their data that will be relevant to BZ.
    """
    Matrix = mathutils.Matrix
    Vector = mathutils.Vector
    for object in _iter_export_objects(context):
        GEO = sdf_classes.GEOData()

        # Assume GEO lod is for first lod level. Until we find out otherwise.
        GEO.lod = 1
        # Do we have greater than 5 or 5 exact characters?
        if len(object.name.lower()) >= 5:
            # Greater than 8 characters... we need to skip it.
            if len(object.name.lower()) > 8:
                continue
            if object.name.lower()[3] == "1":
                GEO.lod = 1
            elif object.name.lower()[3] == "2":
                GEO.lod = 2
            elif object.name.lower()[3] == "3":
                GEO.lod = 3
            else:
                # We don't know the lod, lets not assume it. Skipping the object.
                continue
        else:
            # Discard the object. It has less than 5 characters and is incorrectly named.
            continue

        GEO.name = fixgeoname(object.name, GEO.lod).lower()
        GEO.matrix = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        if object.parent != None:
            if (
                len(object.parent.name.lower()) >= 5
                and len(object.parent.name.lower()) <= 8
            ):
                if object.parent.name.lower()[3] in ["1", "2", "3"]:
                    GEO.parent = fixgeoname(object.parent.name, GEO.lod).lower()
                else:
                    GEO.parent = "WORLD"
            else:
                # Discard the object. It has less than 5 characters and is incorrectly named.
                GEO.parent = "WORLD"
        else:
            GEO.parent = "WORLD"
        euler = mathutils.Euler((0.0, math.radians(45.0), 0.0), "YZX")
        euler[:] = (
            object.rotation_euler.x,
            object.rotation_euler.z,
            object.rotation_euler.y,
        )
        thematrix = euler.to_matrix()
        GEO.matrix[0:3] = thematrix[0][0:3]
        GEO.matrix[3:6] = thematrix[1][0:3]
        GEO.matrix[6:9] = thematrix[2][0:3]
        Translation = object.matrix_local.to_translation()
        GEO.matrix[9:12] = Translation.x, Translation.z, Translation.y

        if object.GEOPropertyGroup.GenerateCollision:
            GenerateGEOCollisions(object)

        GEO.geocenter = [
            object.GEOPropertyGroup.GeoCenterX,
            object.GEOPropertyGroup.GeoCenterY,
            object.GEOPropertyGroup.GeoCenterZ,
        ]
        GEO.sphereradius = object.GEOPropertyGroup.SphereRadius
        GEO.boxhalfheight = [
            object.GEOPropertyGroup.BoxHalfHeightX,
            object.GEOPropertyGroup.BoxHalfHeightY,
            object.GEOPropertyGroup.BoxHalfHeightZ,
        ]
        GEO.type = object.GEOPropertyGroup.GEOType
        GEO.geoflags = object.GEOPropertyGroup.GEOFlags

        GEO.ddr = object.GEOPropertyGroup.SDFDDR
        GEO.x = object.GEOPropertyGroup.SDFX
        GEO.y = object.GEOPropertyGroup.SDFY
        GEO.z = object.GEOPropertyGroup.SDFZ
        GEO.time = object.GEOPropertyGroup.SDFTime
        # Increase the counter on how many objects are in the current LOD.
        # NOTE: Used later to determine the count of geos in SGEO. Which is used to determine the max slots per LOD.
        if GEO.lod == 1:
            lodcount = lodcount + 1
        if not ExportSDFOnly:
            # Go ahead and write the .geo.
            export_geo.geoexport(
                context,
                os.path.dirname(filepath) + "/" + GEO.name + ".geo",
                object,
                face_plane_mode=face_plane_mode,
            )
        # Special class(BlenderObject) I made for putting object and geo in one nice package to keep track of both.
        BlenderObject = sdf_classes.BlenderObject(object, GEO)
        blenderobjects.update({GEO.name: BlenderObject})

    """
    Load all the keyframes for more specific handling below.
    """
    for bo in blenderobjects.values():
        blobject = bo.object
        anim = blobject.animation_data
        # Is there animation data at all?
        if anim is None or anim.action is None:
            continue

        # If the object is in quaternion rotation mode, read quaternion curves and convert to Euler.
        if getattr(blobject, "rotation_mode", "XYZ") == "QUATERNION":
            quat_anim = {}
            for curve in _iter_action_fcurves(anim.action):
                data_path = curve.data_path
                for akeyframe in curve.keyframe_points:
                    keyframe = int(akeyframe.co[0])
                    keyvalue = akeyframe.co[1]

                    if data_path == "rotation_quaternion":
                        if keyframe not in quat_anim:
                            # Identity quaternion (w, x, y, z)
                            quat_anim[keyframe] = [1.0, 0.0, 0.0, 0.0]
                        quat_anim[keyframe][curve.array_index] = keyvalue
                    elif data_path == "location":
                        if keyframe not in bo.posanim:
                            bo.posanim[keyframe] = [0.0, 0.0, 0.0]
                        bo.posanim[keyframe][curve.array_index] = keyvalue
                    elif data_path == "scale":
                        if keyframe not in bo.scaleanim:
                            bo.scaleanim[keyframe] = [float(v) for v in blobject.scale]
                        bo.scaleanim[keyframe][curve.array_index] = keyvalue

            # Convert collected quaternions to Euler XYZ and store just like the original Euler path.
            if quat_anim:
                from mathutils import Quaternion

                for frame, quat_vals in quat_anim.items():
                    q = Quaternion(quat_vals)  # (w, x, y, z)
                    eul = q.to_euler("XYZ")
                    bo.rotanim[frame] = [eul.x, eul.y, eul.z]

        else:
            # ORIGINAL EULER-ONLY BEHAVIOR (unchanged), plus location.
            for curve in _iter_action_fcurves(anim.action):
                for akeyframe in curve.keyframe_points:
                    keyframe = int(akeyframe.co[0])
                    keyvalue = akeyframe.co[1]
                    if curve.data_path == "rotation_euler":
                        if keyframe not in bo.rotanim:
                            bo.rotanim[keyframe] = [0.0, 0.0, 0.0]
                        bo.rotanim[keyframe][curve.array_index] = keyvalue
                    if curve.data_path == "location":
                        if keyframe not in bo.posanim:
                            bo.posanim[keyframe] = [0.0, 0.0, 0.0]
                        bo.posanim[keyframe][curve.array_index] = keyvalue
                    if curve.data_path == "scale":
                        if keyframe not in bo.scaleanim:
                            bo.scaleanim[keyframe] = [float(v) for v in blobject.scale]
                        bo.scaleanim[keyframe][curve.array_index] = keyvalue

    """
    Read the element data in blender and get it ready for writing later.
    """

    # Load animation elements in blender.
    for item in context.scene.AnimationCollection:
        newelement = sdf_classes.ANIMElement()
        if getattr(item, "UseCustomUnknownGeoMask", False):
            newelement.unknowngeoflag = [int(v) for v in item.UnknownGeoMask]
        else:
            # Legacy heuristic.
            if item.Index in [0, 1]:
                newelement.unknowngeoflag = [1] * 32
            else:
                newelement.unknowngeoflag = [0] * 32
        newelement.index = item.Index
        newelement.start = item.Start
        newelement.length = item.Length
        newelement.loop = item.Loop
        newelement.speed = item.Speed
        ANIMElements.append(newelement)

    """
    We need to create orientations for all the objects.
    This will be used later in writing.
    When creating orientations it will also add all the rotation and positions.
    """

    for object in blenderobjects.values():
        neworientation = sdf_classes.ANIMOrientation()
        neworientation.name = object.geo.name
        # Per-part tagANIMOBJ_MESH.flags value captured at import (stock: 0).
        geo_props_for_anim = getattr(object.object, "GEOPropertyGroup", None)
        neworientation.unknown = int(
            getattr(geo_props_for_anim, "ANIMOrientationFlags", 0) or 0
        )
        neworientation.matrix1 = [
            1.00,
            0.0,
            0.0,
            1.00,
            0.0,
            0.0,
            1.00,
            0.0,
            0.0,
            1.00,
            0.0,
            0.0,
        ]
        neworientation.matrix2 = object.geo.matrix
        pos_count = len(object.posanim)
        scale_count = len(object.scaleanim) if use_translation2 else 0
        neworientation.positionindex = pos_index if pos_count > 0 else 0
        neworientation.positioncount = pos_count
        neworientation.translation2index = trans2_index if scale_count > 0 else 0
        neworientation.translation2count = scale_count
        if len(object.rotanim) > 0:
            neworientation.rotationindex = rot_index
        else:
            neworientation.rotationindex = 0
        neworientation.rotationcount = len(object.rotanim)
        ANIMOrientations.append(neworientation)
        for key, array in object.rotanim.items():
            newrotation = sdf_classes.ANIMRotation()
            newrotation.frame = key
            eul = mathutils.Euler((0.0, math.radians(45.0), 0.0), "YZX")
            eul[:] = array[0], array[2], array[1]
            quaternion = eul.to_quaternion()
            newrotation.translate = quaternion[:]
            rot_index = rot_index + 1
            ANIMRotations.append(newrotation)
        for key, array in object.posanim.items():
            tx, ty, tz = array[0], array[2], array[1]
            if object.object.parent != None:
                # Get the parent inverse if it exists and add it on to the animation to create an accurate offset for animations.
                ObjectInverse = object.object.matrix_parent_inverse.to_translation()
                tx = ObjectInverse.x + array[0]
                ty = ObjectInverse.z + array[2]
                tz = ObjectInverse.y + array[1]

            newposition = sdf_classes.ANIMPosition()
            newposition.frame = key
            newposition.translate = tx, ty, tz
            pos_index = pos_index + 1
            ANIMPositions.append(newposition)
        if use_translation2:
            for key, array in object.scaleanim.items():
                newtranslation = sdf_classes.ANIMTranslation2()
                newtranslation.frame = key
                newtranslation.translate = array[0], array[2], array[1]
                trans2_index = trans2_index + 1
                ANIMTranslations.append(newtranslation)

    """
    Reorder objects based on parenting and lods. If they are the wrong order, everything will blow up!
    PARENT MUST COME BEFORE CHILD!
    """
    # Make blank blender objects. Seriously this is important.
    NULLBlenderObject = sdf_classes.BlenderObject(None, NULLGEO)
    objects = [[], [], []]
    objects[0] = [NULLBlenderObject] * lodcount
    objects[1] = [NULLBlenderObject] * lodcount
    objects[2] = [NULLBlenderObject] * lodcount
    orderednames = []
    numindex = 0
    while True:
        DoBreak = True
        for objectkeyname in list(blenderobjects):
            object = blenderobjects[objectkeyname]
            if object.geo.lod == 1:
                DoBreak = False
                if object.geo.parent.lower() != "world":
                    # Note the idea is it will skip the object until we actually have its parent in the reordered list.
                    if object.geo.parent in orderednames:
                        objects[0][numindex] = object
                        orderednames.append(fixgeoname(object.geo.name, 1))
                        # Get LOD 2 and 3 version if avaliable.
                        if fixgeoname(object.geo.name, 2) in blenderobjects:
                            LOD2 = blenderobjects[fixgeoname(object.geo.name, 2)]
                            objects[1][numindex] = LOD2
                        if fixgeoname(object.geo.name, 3) in blenderobjects:
                            LOD3 = blenderobjects[fixgeoname(object.geo.name, 3)]
                            objects[2][numindex] = LOD3
                        del blenderobjects[object.geo.name]
                        numindex = numindex + 1
                else:
                    objects[0][numindex] = object
                    orderednames.append(fixgeoname(object.geo.name.lower(), 1))
                    # Get LOD 2 and 3 version if avaliable.
                    if fixgeoname(object.geo.name, 2) in blenderobjects:
                        LOD2 = blenderobjects[fixgeoname(object.geo.name, 2)]
                        objects[1][numindex] = LOD2
                    if fixgeoname(object.geo.name, 3) in blenderobjects:
                        LOD3 = blenderobjects[fixgeoname(object.geo.name, 3)]
                        objects[2][numindex] = LOD3
                    del blenderobjects[object.geo.name]
                    numindex = numindex + 1
        if DoBreak:
            break

    # Ok, lets get to writing the SDF data.
    with open(filepath, mode="wb") as file:  # b is important -> binary
        position = 0

        position = SDFHeader.Write(file, position)  # Write SDF header.

        position = SDFC.Write(file, position)  # Write SDFC section.

        # Write SGEO section.
        # Set the SGEO count to the lod with the highest amount of GEOs.
        SGEO.geocount = lodcount
        SGEO.sectionlength = ((SGEO.geocount * 120) * 6) + SGEO.binlength
        position = SGEO.Write(file, position)

        # Write geo data. We need to also fill empty slots with empty geo stuff.

        # Write LOD1
        for object in objects[0]:
            object.geo.Write(file, position)

        # Write blanks!
        for i in range(1):
            for i in range(lodcount):
                NULLGEO.Write(file, position)

        # Write LOD2
        for object in objects[1]:
            object.geo.Write(file, position)

        # Write blanks!
        for i in range(1):
            for i in range(lodcount):
                NULLGEO.Write(file, position)

        # Write LOD3
        for object in objects[2]:
            object.geo.Write(file, position)

        # Write blanks!
        for i in range(1):
            for i in range(lodcount):
                NULLGEO.Write(file, position)

        # Write ANIM header or don't...
        if len(ANIMElements) > 0 and ExportAnimations:
            ANIM.elementscount = len(ANIMElements)
            ANIM.orientationscount = len(ANIMOrientations)
            ANIM.rotationcount = len(ANIMRotations)
            ANIM.translation2count = len(ANIMTranslations)
            ANIM.positioncount = len(ANIMPositions)
            if getattr(
                context.scene.SDFVDFPropertyGroup, "UseAdvancedAnimHeader", False
            ):
                ANIM.null2 = int(context.scene.SDFVDFPropertyGroup.AnimNull2)
                ANIM.unknown2 = int(context.scene.SDFVDFPropertyGroup.AnimUnknown2)
                ANIM._reserved = [
                    int(v) for v in context.scene.SDFVDFPropertyGroup.AnimReserved
                ]
            else:
                ANIM.null2 = 0
                ANIM.unknown2 = 0
                ANIM._reserved = [0, 0, 0, 0, 0]
            ANIM.sectionlength = (
                ANIM.binlength
                + (ANIM.elementscount * 148)
                + (ANIM.orientationscount * 132)
                + (ANIM.rotationcount * 20)
                + (ANIM.translation2count * 16)
                + (ANIM.positioncount * 16)
            )
            position = ANIM.Write(file, position)

            # Write ANIM elements.
            for element in ANIMElements:
                position = element.Write(file, position)

            # Write ANIM orientations.
            for orientation in ANIMOrientations:
                position = orientation.Write(file, position)

            # Write ANIM rotations.
            for animrotation in ANIMRotations:
                position = animrotation.Write(file, position)

            # Write ANIM translation2.
            for animtranslation in ANIMTranslations:
                position = animtranslation.Write(file, position)

            # Write ANIM positions.
            for animposition in ANIMPositions:
                position = animposition.Write(file, position)

            position = EXIT.Write(file, position)  # Need an extra exit for animations.
        position = EXIT.Write(file, position)
        position = EXIT.Write(file, position)

        # Preserve unrecognized chunks captured at import (byte-for-byte).
        import base64 as _base64

        preserved_store = getattr(context.scene, "bz_preserved_chunks", None)
        for entry in getattr(preserved_store, "elements", []) or []:
            try:
                payload = _base64.b64decode(entry.payload_b64)
            except Exception:
                continue
            file.write(payload)

    return {"FINISHED"}
