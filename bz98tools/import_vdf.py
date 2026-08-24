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
import mathutils

from . import vdf_classes
from . import vdf_file
from . import semantics
from . import import_geo

# Reload it just in case something changed!
importlib.reload(vdf_classes)
importlib.reload(vdf_file)
importlib.reload(semantics)
importlib.reload(import_geo)


def _get_target_collection(context):
    collection = getattr(context, "collection", None)
    if collection is not None:
        return collection

    scene = getattr(context, "scene", None)
    if scene is not None:
        return scene.collection

    return bpy.context.collection


def _add_import_diagnostic(scene, severity, scope, target, message):
    diagnostics = getattr(scene, "bz_import_diagnostics", None)
    if diagnostics is None:
        return
    item = diagnostics.add()
    item.severity = severity
    item.scope = scope
    item.target = target
    item.message = message


def _store_raw_chunk(scene, chunk):
    """Persist an unknown chunk verbatim in the scene (base64 payload)."""
    store = getattr(scene, "bz_preserved_chunks", None)
    if store is None:
        return
    entry = store.add()
    entry.tag = chunk.tag.decode("ascii", "ignore")
    entry.payload_b64 = base64.b64encode(chunk.full_bytes).decode("ascii")


def _store_damage_band_record(scene, part_name, slot, band, raw):
    store = getattr(scene, "bz_damage_band_records", None)
    if store is None:
        return
    entry = store.add()
    entry.part_name = part_name or ""
    entry.slot = int(slot)
    entry.band = int(band)
    entry.payload_b64 = base64.b64encode(bytes(raw)).decode("ascii")


def _store_vloc_entry(scene, index, chunk):
    store = getattr(scene, "bz_vloc_chunks", None)
    if store is None:
        return
    entry = store.add()
    entry.name = f"VLOC {index + 1}"
    entry.label = chunk.describe()
    if chunk.kind_value == semantics.VLOC_HEADLIGHT:
        entry.kind = "HEADLIGHT"
    elif chunk.kind_value == semantics.VLOC_POV:
        entry.kind = "POV"
    elif chunk.kind_value == semantics.VLOC_IDSIZES:
        entry.kind = "IDSIZES"
    else:
        entry.kind = "GENERIC"
    entry.class_id = int(chunk.kind_value) & 0x7FFFFFFF
    entry.matrix = [float(v) for v in chunk.matrix]
    entry.payload_b64 = base64.b64encode(chunk.opaque_payload).decode("ascii")
    entry.preserve_raw = True


def load(
    context,
    filepath,
    *,
    ImportGEOs=True,
    ImportAnimations=True,
    PreserveFaceColors=True,
    ImportMapTextures=False,
    MapTextureDirectory="",
    MapTextureZFS="",
):
    if not os.path.exists(filepath):
        raise Exception(filepath + " was not found!")
        return {"FINISHED"}

    with open(filepath, mode="rb") as file:  # b is important -> binary
        fileContent = file.read()

    parsed = vdf_file.parse_vdf(fileContent)

    scene = context.scene
    target_collection = _get_target_collection(context)
    if hasattr(scene, "bz_import_diagnostics"):
        scene.bz_import_diagnostics.clear()

    if not parsed.header_matches_canonical:
        _add_import_diagnostic(
            scene,
            "WARNING",
            "VDF",
            os.path.basename(filepath),
            "Non-canonical VDF header values; export rewrites canonical constants.",
        )

    _add_import_diagnostic(
        scene,
        "INFO",
        "VDF",
        os.path.basename(filepath),
        f"Vehicle '{parsed.vdfc_name}', type {parsed.vdfc_vehicletype}, size {parsed.vdfc_vehiclesize}.",
    )
    _add_import_diagnostic(
        scene,
        "INFO",
        "Orientation",
        "vehicle front",
        "Imported VDF vehicles are authored nose/front toward Blender +Y. Legacy + Redux export converts that setup for Redux output.",
    )
    if parsed.vdfc_null != 0:
        props = getattr(scene, "SDFVDFPropertyGroup", None)
        if props is not None and hasattr(props, "VDFCRawNull"):
            props.VDFCRawNull = int(parsed.vdfc_null)
        _add_import_diagnostic(
            scene,
            "WARNING",
            "VDFC",
            "raw null",
            f"Nonzero VDFC trailing int preserved: {parsed.vdfc_null}.",
        )

    # Take our VDF information and load it into the scene.
    props = scene.SDFVDFPropertyGroup
    props["Name"] = parsed.vdfc_name
    props["VehicleSize"] = parsed.vdfc_vehiclesize
    props["VehicleType"] = parsed.vdfc_vehicletype
    props["LOD1"] = parsed.vdfc_lod_dists[0]
    props["LOD2"] = parsed.vdfc_lod_dists[1]
    props["LOD3"] = parsed.vdfc_lod_dists[2]
    props["LOD4"] = parsed.vdfc_lod_dists[3]
    props["LOD5"] = parsed.vdfc_lod_dists[4]
    props["Mass"] = parsed.vdfc_mass
    props["CollMult"] = parsed.vdfc_multiplyer
    props["DragCoefficient"] = parsed.vdfc_drag

    _add_import_diagnostic(
        scene,
        "INFO",
        "VGEO",
        "slots",
        f"{parsed.geocount} GEO slots per band across 28 bands (7 damage states x 4 LODs).",
    )

    # ------------------------------------------------------------------
    # Preserve unknown chunks / section layout for non-destructive export.
    # ------------------------------------------------------------------
    preserved_store = getattr(scene, "bz_preserved_chunks", None)
    if preserved_store is not None:
        preserved_store.clear()
    damage_store = getattr(scene, "bz_damage_band_records", None)
    if damage_store is not None:
        damage_store.clear()
    vloc_store = getattr(scene, "bz_vloc_chunks", None)
    if vloc_store is not None:
        vloc_store.clear()
    plan_store = getattr(scene, "bz_vdf_section_plan", None)

    unknown_tags = []
    for chunk in parsed.raw_chunks:
        _store_raw_chunk(scene, chunk)
        unknown_tags.append(chunk.tag.decode("ascii", "ignore"))
    if unknown_tags:
        _add_import_diagnostic(
            scene,
            "INFO",
            "Chunks",
            ", ".join(unknown_tags[:6]),
            f"{len(unknown_tags)} unrecognized chunk(s) preserved verbatim for round-trip safety.",
        )

    for idx, chunk in enumerate(parsed.vlocs):
        _store_vloc_entry(scene, idx, chunk)
    if parsed.vlocs:
        _add_import_diagnostic(
            scene,
            "INFO",
            "VLOC",
            f"{len(parsed.vlocs)} entries",
            "VLOC part-injection chunks imported. See the Battlezone side panel 'VLOC Injection' to inspect or edit them.",
        )

    if plan_store is not None:
        plan_store.set(
            ",".join(kind for kind, _index in parsed.plan),
        )

    if parsed.trailing_garbage.strip(b"\0"):
        _add_import_diagnostic(
            scene,
            "WARNING",
            "Chunks",
            "tail bytes",
            f"{len(parsed.trailing_garbage)} unparsed trailing byte(s) preserved verbatim.",
        )

    # ------------------------------------------------------------------
    # Damage representation bands (VGEO 7 damage states x 4 LODs).
    # Bands 0/4/8 are LOD1/LOD2/LOD3. Every other populated band is a
    # damage-state variant table; content is attached to its d0l0 part or
    # preserved opaquely when no base part exists at that slot.
    # ------------------------------------------------------------------
    damage_table = semantics.DamageVariantTable()
    damage_table.capture_band_records(parsed.records, parsed.geocount)

    def _record_name(raw_bytes):
        return raw_bytes[:8].split(b"\0")[0].decode("ascii", "ignore").strip().lower()

    # ------------------------------------------------------------------
    # Recreate the inner/outer collision boxes from COLP
    # ------------------------------------------------------------------
    colp_data = parsed.colp_data if parsed.colp_data is not None else [0.0] * 12
    if parsed.colp_data is None:
        _add_import_diagnostic(
            scene,
            "WARNING",
            "COLP",
            "collision",
            "No VDF COLP collision box section was found; empty collision helpers were created.",
        )
    else:
        _add_import_diagnostic(
            scene,
            "INFO",
            "COLP",
            "collision",
            "Imported VDF inner/outer collision box values.",
        )

    innermesh = bpy.data.meshes.new("mesh")
    innerobj = bpy.data.objects.new("inner_col", innermesh)
    outermesh = bpy.data.meshes.new("mesh")
    outerobj = bpy.data.objects.new("outer_col", outermesh)
    (
        YMaxOut,
        YMaxIn,
        YMinIn,
        YMinOut,
        XMaxOut,
        XMaxIn,
        XMinIn,
        XMinOut,
        ZMaxOut,
        ZMaxIn,
        ZMinIn,
        ZMinOut,
    ) = colp_data

    target_collection.objects.link(innerobj)
    target_collection.objects.link(outerobj)

    # Create mesh for inner box.
    bminner = bmesh.new()
    for vert in [
        (XMaxIn, YMaxIn, ZMinIn),
        (XMaxIn, YMinIn, ZMinIn),
        (XMinIn, YMinIn, ZMinIn),
        (XMinIn, YMaxIn, ZMinIn),
        (XMaxIn, YMaxIn, ZMaxIn),
        (XMaxIn, YMinIn, ZMaxIn),
        (XMinIn, YMinIn, ZMaxIn),
        (XMinIn, YMaxIn, ZMaxIn),
    ]:
        bminner.verts.new(vert)

    bminner.to_mesh(innermesh)
    bminner.free()

    # Create mesh for outer box.
    bmouter = bmesh.new()
    for vert in [
        (XMaxOut, YMaxOut, ZMinOut),
        (XMaxOut, YMinOut, ZMinOut),
        (XMinOut, YMinOut, ZMinOut),
        (XMinOut, YMaxOut, ZMinOut),
        (XMaxOut, YMaxOut, ZMaxOut),
        (XMaxOut, YMinOut, ZMaxOut),
        (XMinOut, YMinOut, ZMaxOut),
        (XMinOut, YMaxOut, ZMaxOut),
    ]:
        bmouter.verts.new(vert)

    bmouter.to_mesh(outermesh)
    bmouter.free()

    # ------------------------------------------------------------------
    # Load GEOs and recreate Blender objects (base damage state only).
    # ------------------------------------------------------------------
    OBJList = {}
    slot_objects = {}  # slot index in d0l0 -> BlenderObject wrapper
    eyepoint_parts = []
    smoke_emitters = 0
    weapon_hardpoints = 0
    imported_variant_count = 0
    extra_band_samples = []

    if ImportGEOs:
        lod_bands = ((0, 1), (4, 2), (8, 3))
        for band, lod_value in lod_bands:
            for slot in range(parsed.geocount):
                record_index = band * parsed.geocount + slot
                GEO = vdf_classes.GEOData()
                GEO.Read(parsed.records[record_index], 0)
                if GEO.name[0:4].lower() == "null":
                    continue

                geofilename = os.path.dirname(filepath) + "/" + GEO.name + ".geo"

                # Case-insensitive search for GEO file if needed.
                if not os.path.exists(geofilename):
                    for root, dirs, files in os.walk(os.path.dirname(geofilename)):
                        for afile in files:
                            if (GEO.name + ".geo").lower() == afile.lower():
                                geofilename = os.path.join(
                                    os.path.dirname(geofilename), afile.lower()
                                )
                                break

                newobj = None

                # Load mesh GEO if file exists.
                if os.path.exists(geofilename):
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
                    except Exception as e:
                        print(
                            f"[BZ VDF Import] Failed to load GEO '{GEO.name}': {e}"
                        )

                # Helper parts often have no .geo by design.
                if newobj is None and GEO.type in (15, 40):
                    newobj = bpy.data.objects.new(GEO.name, None)
                    if GEO.type == 15:
                        newobj.empty_display_type = "ARROWS"
                    else:
                        newobj.empty_display_type = "SINGLE_ARROW"
                    newobj.empty_display_size = 0.25
                    target_collection.objects.link(newobj)

                if newobj is not None:
                    geo_props = newobj.GEOPropertyGroup
                    geo_props["GEOType"] = GEO.type
                    geo_props["GEOFlags"] = GEO.geoflags
                    geo_props["GeoCenterX"] = GEO.geocenter[0]
                    geo_props["GeoCenterY"] = GEO.geocenter[1]
                    geo_props["GeoCenterZ"] = GEO.geocenter[2]
                    geo_props["SphereRadius"] = GEO.sphereradius
                    geo_props["BoxHalfHeightX"] = GEO.boxhalfheight[0]
                    geo_props["BoxHalfHeightY"] = GEO.boxhalfheight[1]
                    geo_props["BoxHalfHeightZ"] = GEO.boxhalfheight[2]
                    try:
                        geo_props.RawVDFMatrix = tuple(
                            float(v) for v in GEO.matrix
                        )
                    except Exception:
                        pass

                    # Authored bounds survive round trips by default; users can
                    # switch to recalculation per object in Advanced Semantics.
                    if hasattr(geo_props, "BoundsMode"):
                        geo_props.BoundsMode = "PRESERVE"
                    if hasattr(geo_props, "HasAuthoredBounds"):
                        geo_props.HasAuthoredBounds = True

                    # Semantic diagnostics driven by the research model.
                    if not semantics.is_known_part_type(GEO.type):
                        _add_import_diagnostic(
                            scene,
                            "INFO",
                            "Part type",
                            GEO.name,
                            f"Unknown semantic part type {GEO.type} preserved exactly; it renders/collides like an ordinary part.",
                        )
                    elif GEO.type in semantics.PHANTOM_PART_TYPES:
                        _add_import_diagnostic(
                            scene,
                            "INFO",
                            "Part type",
                            GEO.name,
                            f"Type {GEO.type} ({semantics.PHANTOM_PART_TYPES[GEO.type]}) is not an engine class; value preserved.",
                        )

                    unknown_bits = semantics.unknown_flag_bits(GEO.geoflags)
                    if unknown_bits:
                        _add_import_diagnostic(
                            scene,
                            "WARNING",
                            "Object flags",
                            GEO.name,
                            f"Unknown ObjectFlags bits {semantics.format_hex(unknown_bits)} present and preserved.",
                        )
                    decoded_flags = semantics.decode_object_flags(GEO.geoflags)
                    if decoded_flags["destroyed"]:
                        _add_import_diagnostic(
                            scene,
                            "WARNING",
                            "Object flags",
                            GEO.name,
                            "ObjectFlags bit 0x200 (destroyed seed) is set on import; the engine treats this object as dead from spawn.",
                        )

                    if GEO.type == 15:
                        x, y, z = GEO.matrix[0], GEO.matrix[1], GEO.matrix[2]
                        magnitude = (x * x + y * y + z * z) ** 0.5
                        if magnitude > 1e-8:
                            axis = (x / magnitude, y / magnitude, z / magnitude)
                            speed = magnitude
                        else:
                            axis = (1.0, 0.0, 0.0)
                            speed = 0.0

                        geo_props.IsSpinnerHelper = True
                        geo_props.SpinnerAxis = axis
                        geo_props.SpinnerSpeed = speed
                        geo_props.GenerateCollision = False

                        if GEO.parent.lower() != "world":
                            geo_props.SpinnerTarget = (
                                GEO.parent.lower()
                            )

                    if GEO.type == semantics.EYEPOINT_CLASS:
                        eyepoint_parts.append(newobj)
                        if getattr(newobj, "type", None) != "MESH":
                            if hasattr(geo_props, "IsPOVHelper"):
                                geo_props.IsPOVHelper = True

                    if GEO.type == semantics.EMITTER_SMOKE_CLASS:
                        smoke_emitters += 1
                    if GEO.type == semantics.HARDPOINT_WEAPON_CLASS:
                        weapon_hardpoints += 1

                    blenobj = vdf_classes.BlenderObject(newobj, GEO)
                    blenobj.obj_index = slot
                    blenobj.obj_lod = 1 if band == 0 else (2 if band == 4 else 3)
                    OBJList.update({GEO.name.lower(): blenobj})
                    if band == 0:
                        slot_objects[slot] = blenobj

        # Damage variants: lod-slot-0 bands 1..3 carry per-part mesh swaps for
        # damage states 1..3; every other populated band is preserved verbatim.
        for (slot, band), raw in list(damage_table.variant_records.items()):
            name = _record_name(raw)
            if not name or name.startswith("null"):
                continue
            lod_slot, damage_state = semantics.band_coords(band)
            base = slot_objects.get(slot)
            if lod_slot == 0 and damage_state in semantics.AUTHORED_DAMAGE_STATES:
                if base is not None and base.object is not None:
                    geo_props = base.object.GEOPropertyGroup
                    attr = f"DamageGeo{damage_state}"
                    if hasattr(geo_props, attr):
                        current = getattr(geo_props, attr)
                        if current and current.lower() != name:
                            # Conflicting names inside one state: keep the odd
                            # record opaquely instead of guessing.
                            _store_damage_band_record(
                                scene, base.geo.name.lower(), slot, band, raw
                            )
                            continue
                        setattr(geo_props, attr, name[:8])
                        geo_props.HasDamageVariants = True
                        imported_variant_count += 1
                        continue
                _store_damage_band_record(
                    scene,
                    base.geo.name.lower() if base else "",
                    slot,
                    band,
                    raw,
                )
                continue
            # Other lod slots / extra content: opaque preservation with a note.
            _store_damage_band_record(scene, base.geo.name.lower() if base else "", slot, band, raw)
            if len(extra_band_samples) < 4:
                extra_band_samples.append(f"band {band}: {name}")

        if imported_variant_count:
            _add_import_diagnostic(
                scene,
                "INFO",
                "Damage reps",
                f"{imported_variant_count} variants",
                "Damage-state mesh swaps imported into per-part fields. Note: stock Battlezone 98 engines never select damage states above 0 until an external driver calls ObjTree_SelectRep.",
            )

        if extra_band_samples:
            _add_import_diagnostic(
                scene,
                "INFO",
                "VGEO",
                "extra bands",
                f"Non-primary VGEO band content preserved verbatim: {'; '.join(extra_band_samples)}.",
            )

        if smoke_emitters > semantics.ENGINE_FIXED_EMITTER_LIMIT:
            _add_import_diagnostic(
                scene,
                "ERROR",
                "Emitters",
                f"{smoke_emitters} smoke emitters",
                "More than 8 class-76 parts overflow the engine's fixed smokeList array (silent memory corruption risk).",
            )
        if weapon_hardpoints > semantics.ENGINE_FIXED_EMITTER_LIMIT:
            _add_import_diagnostic(
                scene,
                "WARNING",
                "Hardpoints",
                f"{weapon_hardpoints} weapon hardpoints",
                "More than 8 class-70 parts can overflow producer smoke-source arrays on producing structures.",
            )

        if len(eyepoint_parts) > 1:
            _add_import_diagnostic(
                scene,
                "WARNING",
                "Eyepoint",
                ", ".join(obj.name for obj in eyepoint_parts),
                f"Multiple class-40 (eyepoint) parts imported ({len(eyepoint_parts)}); craft code resolves one POV node.",
            )

        # ------------------------------------------------------------------
        # Parent GEO objects according to GEO.parent
        # ------------------------------------------------------------------
        for Model in OBJList.values():
            if (
                Model.geo.parent.lower() != "world"
                and Model.geo.parent.lower() in OBJList
            ):
                Parent = OBJList[Model.geo.parent.lower()]
                Model.object.parent = Parent.object
            elif (
                Model.geo.parent.lower() != "world"
                and Model.geo.parent.lower() not in OBJList
            ):
                Parent = None
                stringlist = list(Model.geo.parent.lower())
                stringlist[3] = "1"
                lowerlodparent = "".join(stringlist)
                if lowerlodparent in OBJList.keys():
                    Parent = OBJList[lowerlodparent]
                if Parent is not None:
                    Model.object.parent = Parent.object

        # ------------------------------------------------------------------
        # Apply transform (rotation + scale + position) from GEO.matrix
        # ------------------------------------------------------------------
        Matrix = mathutils.Matrix
        Vector = mathutils.Vector

        # Position childless things first.
        for Model in OBJList.values():
            if Model.object.parent is None:
                obj = Model.object
                geo = Model.geo

                # Rebuild 3x3 basis from GEO matrix (right/up/front w/ scale baked in)
                mat3 = mathutils.Matrix(
                    (
                        (geo.matrix[0], geo.matrix[1], geo.matrix[2]),
                        (geo.matrix[3], geo.matrix[4], geo.matrix[5]),
                        (geo.matrix[6], geo.matrix[7], geo.matrix[8]),
                    )
                )

                # Decompose: scale from column lengths, then normalize columns to get pure rotation
                sx = mat3.col[0].length
                sy = mat3.col[1].length
                sz = mat3.col[2].length

                rot_mat = mat3.copy()
                if sx != 0.0:
                    rot_mat.col[0] /= sx
                if sy != 0.0:
                    rot_mat.col[1] /= sy
                if sz != 0.0:
                    rot_mat.col[2] /= sz

                rotation = rot_mat.to_euler()
                rotation[:] = rotation[0], rotation[2], rotation[1]
                obj.rotation_mode = "YZX"
                obj.rotation_euler = rotation

                # Apply scale that was encoded in the basis vectors
                obj.scale = mathutils.Vector((sx, sy, sz))

                # Position (same axis remap as before)
                obj.location = mathutils.Vector(
                    (geo.matrix[9], geo.matrix[11], geo.matrix[10])
                )

        # Position children to their parents.
        for Model in OBJList.values():
            if Model.object.parent is not None:
                obj = Model.object
                geo = Model.geo

                mat3 = mathutils.Matrix(
                    (
                        (geo.matrix[0], geo.matrix[1], geo.matrix[2]),
                        (geo.matrix[3], geo.matrix[4], geo.matrix[5]),
                        (geo.matrix[6], geo.matrix[7], geo.matrix[8]),
                    )
                )

                sx = mat3.col[0].length
                sy = mat3.col[1].length
                sz = mat3.col[2].length

                rot_mat = mat3.copy()
                if sx != 0.0:
                    rot_mat.col[0] /= sx
                if sy != 0.0:
                    rot_mat.col[1] /= sy
                if sz != 0.0:
                    rot_mat.col[2] /= sz

                rotation = rot_mat.to_euler()
                rotation[:] = rotation[0], rotation[2], rotation[1]
                obj.rotation_mode = "YZX"
                obj.rotation_euler = rotation

                obj.scale = mathutils.Vector((sx, sy, sz))
                obj.location = mathutils.Vector(
                    (geo.matrix[9], geo.matrix[11], geo.matrix[10])
                )

    # ------------------------------------------------------------------
    # Load animation into the scene (if present and requested)
    # ------------------------------------------------------------------
    scene.AnimationCollection.clear()
    if ImportGEOs and ImportAnimations and parsed.anim_present:
        for element in parsed.anim_elements:
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
        for Model in OBJList.values():
            geoname = Model.geo.name
            orientation = next(
                (o for o in parsed.anim_orientations if o.name == geoname), None
            )
            if orientation is None:
                continue
            # Preserve tagANIMOBJ_MESH.flags for this part across round trips.
            if hasattr(Model.object.GEOPropertyGroup, "ANIMOrientationFlags"):
                Model.object.GEOPropertyGroup.ANIMOrientationFlags = int(
                    getattr(orientation, "unknown", 0)
                )
            if orientation.rotationcount > 0:
                for index in range(
                    orientation.rotationindex,
                    orientation.rotationindex + orientation.rotationcount,
                ):
                    if index >= len(parsed.anim_rotations):
                        break
                    RotQuaternion = mathutils.Quaternion(
                        (
                            parsed.anim_rotations[index].translate[0],
                            parsed.anim_rotations[index].translate[1],
                            parsed.anim_rotations[index].translate[3],
                            parsed.anim_rotations[index].translate[2],
                        )
                    )
                    RotEuler = RotQuaternion.to_euler("XYZ")
                    Model.object.rotation_mode = "XYZ"
                    Model.object.rotation_euler = RotEuler
                    Model.object.keyframe_insert(
                        "rotation_euler", frame=parsed.anim_rotations[index].frame
                    )
                    if parsed.anim_rotations[index].frame > EndFrame:
                        EndFrame = parsed.anim_rotations[index].frame

            if orientation.positioncount > 0:
                for index in range(
                    orientation.positionindex,
                    orientation.positionindex + orientation.positioncount,
                ):
                    if index >= len(parsed.anim_positions):
                        break
                    Model.object.location = (
                        parsed.anim_positions[index].translate[0],
                        parsed.anim_positions[index].translate[2],
                        parsed.anim_positions[index].translate[1],
                    )
                    Model.object.keyframe_insert(
                        data_path="location", frame=parsed.anim_positions[index].frame
                    )
                    if parsed.anim_positions[index].frame > EndFrame:
                        EndFrame = parsed.anim_positions[index].frame

            if orientation.translation2count > 0:
                for index in range(
                    orientation.translation2index,
                    orientation.translation2index + orientation.translation2count,
                ):
                    if index >= len(parsed.anim_translations2):
                        break
                    Model.object.scale = (
                        parsed.anim_translations2[index].translate[0],
                        parsed.anim_translations2[index].translate[2],
                        parsed.anim_translations2[index].translate[1],
                    )
                    Model.object.keyframe_insert(
                        data_path="scale", frame=parsed.anim_translations2[index].frame
                    )
                    if parsed.anim_translations2[index].frame > EndFrame:
                        EndFrame = parsed.anim_translations2[index].frame

        scene.frame_set(0)
        scene.frame_start = 0
        scene.frame_end = EndFrame

    if parsed.anim_present:
        props.UseAdvancedAnimHeader = True
        props.AnimNull2 = int(parsed.anim_header.null2)
        props.AnimUnknown2 = int(parsed.anim_header.unknown2)
        try:
            props.AnimReserved = tuple(
                int(v) for v in parsed.anim_header._reserved[:5]
            )
        except Exception:
            props.AnimReserved = (0, 0, 0, 0, 0)
        props.UseTranslation2Track = bool(len(parsed.anim_translations2) > 0)

        _add_import_diagnostic(
            scene,
            "INFO",
            "ANIM",
            parsed.anim_header.name,
            (
                f"{len(parsed.anim_elements)} elements, {len(parsed.anim_orientations)} orientations, "
                f"{len(parsed.anim_rotations)} rotations, {len(parsed.anim_translations2)} Translation2 keys, "
                f"{len(parsed.anim_positions)} position keys."
            ),
        )
        if len(parsed.anim_translations2) > 0:
            _add_import_diagnostic(
                scene,
                "INFO",
                "ANIM",
                "Translation2",
                "This file uses the niche Translation2 position track.",
            )
    else:
        props.UseAdvancedAnimHeader = False
        props.UseTranslation2Track = False

    scps_tag = parsed.scps_tag
    props.UseCustomSCPS = bool(scps_tag)
    if scps_tag:
        props.SCPSData = tuple(int(v) for v in (parsed.scps_data or [0, 0, 0])[:3])
        _add_import_diagnostic(
            scene,
            "INFO",
            scps_tag,
            "raw data",
            f"SPCS/SCPS raw ints: {', '.join(str(int(v)) for v in (parsed.scps_data or [])[:3])}.",
        )
    else:
        props.SCPSData = (0, 0, 0)

    return {"FINISHED"}


# If being run in the Script editor allow us to test load something.
if __name__ == "__main__":
    load(bpy.context, "C:/BattlezoneData/bzone.zfs/abskat.vdf")
