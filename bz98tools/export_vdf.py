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
import base64
import struct

from . import vdf_classes
from . import vdf_file
from . import semantics
from . import export_geo

# Reload it just in case something changed!
importlib.reload(vdf_classes)
importlib.reload(vdf_file)
importlib.reload(semantics)
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
    minx, miny, minz, maxx, maxy, maxz = None, None, None, None, None, None
    maxoverall = 0.0
    for vert in obj.data.vertices:
        x, y, z = vert.co.x, vert.co.y, vert.co.z
        minx = x if minx is None or x < minx else minx
        maxx = x if maxx is None or x > maxx else maxx
        miny = y if miny is None or y < miny else miny
        maxy = y if maxy is None or y > maxy else maxy
        minz = z if minz is None or z < minz else minz
        maxz = z if maxz is None or z > maxz else maxz
        # Get maximum vertice distance to generate sphere radius.
        for value in (x, y, z):
            if abs(value) > maxoverall:
                maxoverall = abs(value)

    cx = (minx + maxx) / 2 if minx is not None else 0.0
    cy = (miny + maxy) / 2 if miny is not None else 0.0
    cz = (minz + maxz) / 2 if minz is not None else 0.0

    obj.GEOPropertyGroup.SphereRadius = maxoverall
    obj.GEOPropertyGroup.GeoCenterX = cx
    obj.GEOPropertyGroup.GeoCenterY = cy
    obj.GEOPropertyGroup.GeoCenterZ = cz

    def _half(lo, hi, center):
        if lo is None or hi is None:
            return 0.0
        return abs(lo - center) if abs(lo - center) >= abs(hi - center) else abs(hi - center)

    obj.GEOPropertyGroup.BoxHalfHeightX = _half(minx, maxx, cx)
    obj.GEOPropertyGroup.BoxHalfHeightY = _half(miny, maxy, cy)
    obj.GEOPropertyGroup.BoxHalfHeightZ = _half(minz, maxz, cz)


def _is_null_blender_object(blender_object):
    if blender_object is None:
        return True
    if getattr(blender_object, "object", None) is None:
        return True
    geo = getattr(blender_object, "geo", None)
    if geo is None:
        return True
    return str(getattr(geo, "name", "")).lower() == "null"


def _normalize_target_name(target_name):
    if not target_name:
        return None
    name = str(target_name).strip()
    if len(name) >= 5:
        try:
            return fixgeoname(name, 1).lower()
        except Exception:
            pass
    return name[:8].lower()


def _enforce_spinner_helper_order(objects):
    """
    Ensure spinner helpers are written immediately after their target slot in LOD1.
    This preserves existing slot alignment across LOD1/2/3 by moving whole slot groups.
    """
    lod1 = objects[0]
    lod2 = objects[1]
    lod3 = objects[2]

    slot_by_name = {}
    ordered_names = []
    null_slots = []

    for idx, entry in enumerate(lod1):
        if _is_null_blender_object(entry):
            null_slots.append((lod1[idx], lod2[idx], lod3[idx]))
            continue
        name = entry.geo.name.lower()
        slot_by_name[name] = (lod1[idx], lod2[idx], lod3[idx])
        ordered_names.append(name)

    attached_helpers = {}
    helper_names = set()

    for name in ordered_names:
        slot = slot_by_name[name]
        lod1_obj = slot[0]
        obj = lod1_obj.object
        geo = lod1_obj.geo
        geo_props = getattr(obj, "GEOPropertyGroup", None)
        if geo_props is None:
            continue
        if geo.type != 15 or not getattr(geo_props, "IsSpinnerHelper", False):
            continue

        raw_target = (getattr(geo_props, "SpinnerTarget", "") or "").strip()
        if not raw_target and obj.parent is not None:
            raw_target = obj.parent.name

        target = _normalize_target_name(raw_target)
        if not target or target == name or target not in slot_by_name:
            continue

        attached_helpers.setdefault(target, []).append(name)
        helper_names.add(name)

    final_names = []
    for name in ordered_names:
        if name in helper_names:
            continue
        final_names.append(name)
        if name in attached_helpers:
            final_names.extend(attached_helpers[name])

    # Add any unresolved helper/base entries that were skipped.
    for name in ordered_names:
        if name not in final_names:
            final_names.append(name)

    reordered_slots = [slot_by_name[name] for name in final_names]
    reordered_slots.extend(null_slots)

    if len(reordered_slots) != len(lod1):
        return

    objects[0] = [slot[0] for slot in reordered_slots]
    objects[1] = [slot[1] for slot in reordered_slots]
    objects[2] = [slot[2] for slot in reordered_slots]


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
    ExportVDFOnly=False,
    face_plane_mode="CURRENT",
):
    """
    We are going to use a bunch of classes to write data and encapsulate it.
    First we'll initialize a bunch of them here.
    """
    VDFHeader = vdf_classes.VDFHeader()
    scene = context.scene
    scene_props = scene.SDFVDFPropertyGroup

    model = vdf_file.ParsedVDF()
    model.plan = list(vdf_file.new_empty_plan())

    # Imported section layout wins so untouched scenes round-trip exactly.
    plan_store = getattr(scene, "bz_vdf_section_plan", None)
    imported_plan = ""
    if plan_store is not None:
        try:
            imported_plan = str(plan_store or "")
        except Exception:
            imported_plan = ""
    if imported_plan:
        kinds = [k.strip() for k in imported_plan.split(",") if k.strip()]
        valid = {"vdfc", "exit", "vgeo", "anim", "colp", "scps", "vloc", "raw"}
        if kinds and set(kinds).issubset(valid):
            rebuilt_plan = []
            vloc_index = 0
            raw_index = 0
            for kind in kinds:
                if kind == "vloc":
                    rebuilt_plan.append(("vloc", vloc_index))
                    vloc_index += 1
                elif kind == "raw":
                    rebuilt_plan.append(("raw", raw_index))
                    raw_index += 1
                else:
                    rebuilt_plan.append((kind, None))
            model.plan = rebuilt_plan

    NULLGEO = vdf_classes.GEOData()
    NULLGEO.name = "NULL"
    NULLGEO.matrix = [0] * 12
    NULLGEO.parent = "NULL"
    NULLGEO.geocenter = [0] * 3
    NULLGEO.sphereradius = 0
    NULLGEO.boxhalfheight = [0] * 3
    NULLGEO.type = 0
    NULLGEO.geoflags = 0
    NULL_RECORD = vdf_classes.serialize_section(NULLGEO)

    """
    Variables to keep track of.
    """
    blenderobjects = {}
    collisioninner = None
    collisionouter = None
    ANIMElements = []
    ANIMOrientations = []
    ANIMRotations = []
    ANIMTranslations = []
    ANIMPositions = []
    rot_index = 0
    trans2_index = 0
    pos_index = 0
    lodcount = 0
    use_translation2 = bool(getattr(scene_props, "UseTranslation2Track", False))

    """
    Create/Load VDFC information.
    """
    model.vdfc_name = scene_props.Name
    model.vdfc_vehicletype = scene_props.VehicleType
    model.vdfc_vehiclesize = scene_props.VehicleSize
    model.vdfc_lod_dists = [
        scene_props.LOD1,
        scene_props.LOD2,
        scene_props.LOD3,
        scene_props.LOD4,
        scene_props.LOD5,
    ]
    model.vdfc_mass = scene_props.Mass
    model.vdfc_multiplyer = scene_props.CollMult
    model.vdfc_drag = scene_props.DragCoefficient
    model.vdfc_null = int(getattr(scene_props, "VDFCRawNull", 0) or 0)

    """
    Find collisions; find objects and build their GEOData records.
    """
    Matrix = mathutils.Matrix
    Vector = mathutils.Vector
    for object in _iter_export_objects(context):
        is_mesh_object = (
            getattr(object, "type", None) == "MESH"
            and getattr(object, "data", None) is not None
        )

        # --- Failsafe: fix invalid material indices on this object's mesh ---
        mesh = getattr(object, "data", None)
        if is_mesh_object and hasattr(mesh, "polygons"):
            mat_count = len(mesh.materials)
            if mat_count > 0:
                for poly in mesh.polygons:
                    idx = getattr(poly, "material_index", 0)
                    if idx is None or idx < 0 or idx >= mat_count:
                        print(
                            f"[BZ VDF Export] Warning: invalid material index {idx} on polygon {poly.index} of object {object.name}; resetting to 0."
                        )
                        poly.material_index = 0

        if object.name.lower() in [
            "inner_col",
            "innercol",
            "inner_collision",
            "innercollision",
        ]:
            collisioninner = object
            offset = Vector((0.0, 0.0, 0.0)) - collisioninner.location
            collisioninner.data.transform(mathutils.Matrix.Translation(-offset))
            collisioninner.matrix_world.translation += offset
        elif object.name.lower() in [
            "outer_col",
            "outercol",
            "outer_collision",
            "outercollision",
        ]:
            collisionouter = object
            offset = Vector((0.0, 0.0, 0.0)) - collisionouter.location
            collisionouter.data.transform(mathutils.Matrix.Translation(-offset))
            collisionouter.matrix_world.translation += offset
        else:
            GEO = vdf_classes.GEOData()

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
                    GEO.parent = "WORLD"
            else:
                GEO.parent = "WORLD"

            use_raw_matrix = bool(
                getattr(object.GEOPropertyGroup, "UseRawVDFMatrix", False)
            )

            if use_raw_matrix:
                raw_vals = getattr(object.GEOPropertyGroup, "RawVDFMatrix", None)
                if raw_vals is not None and len(raw_vals) == 12:
                    GEO.matrix = [float(v) for v in raw_vals]
                else:
                    GEO.matrix = [
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                    ]
            else:
                # --------------------------------------------------
                # Transform with SCALE baked in (right/up/front/pos)
                # --------------------------------------------------
                euler = mathutils.Euler((0.0, math.radians(45.0), 0.0), "YZX")
                euler[:] = (
                    object.rotation_euler.x,
                    object.rotation_euler.z,
                    object.rotation_euler.y,
                )
                rot_matrix = euler.to_matrix()  # 3x3

                sx, sy, sz = object.scale
                scale_mat = mathutils.Matrix(
                    (
                        (sx, 0.0, 0.0),
                        (0.0, sy, 0.0),
                        (0.0, 0.0, sz),
                    )
                )

                thematrix = rot_matrix @ scale_mat

                GEO.matrix[0:3] = thematrix[0][0:3]
                GEO.matrix[3:6] = thematrix[1][0:3]
                GEO.matrix[6:9] = thematrix[2][0:3]
                Translation = object.matrix_local.to_translation()
                GEO.matrix[9:12] = Translation.x, Translation.z, Translation.y

            GEO.type = object.GEOPropertyGroup.GEOType
            GEO.geoflags = object.GEOPropertyGroup.GEOFlags

            spinner_helper = bool(
                getattr(object.GEOPropertyGroup, "IsSpinnerHelper", False)
            )
            if GEO.type == 15 and spinner_helper and not use_raw_matrix:
                axis = getattr(object.GEOPropertyGroup, "SpinnerAxis", (1.0, 0.0, 0.0))
                speed = float(getattr(object.GEOPropertyGroup, "SpinnerSpeed", 1.0))
                GEO.matrix[0] = float(axis[0]) * speed
                GEO.matrix[1] = float(axis[1]) * speed
                GEO.matrix[2] = float(axis[2]) * speed

            bounds_mode = str(
                getattr(object.GEOPropertyGroup, "BoundsMode", "AUTO")
            )

            if bounds_mode == "RECALC" and is_mesh_object:
                GenerateGEOCollisions(object)
            elif bounds_mode == "AUTO" and object.GEOPropertyGroup.GenerateCollision and is_mesh_object:
                GenerateGEOCollisions(object)
            elif bounds_mode == "PRESERVE" and not getattr(
                object.GEOPropertyGroup, "HasAuthoredBounds", False
            ):
                # No imported values to preserve; fall back to generation so
                # freshly authored parts still get sane collision defaults.
                if is_mesh_object:
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

            # Increase the counter on how many objects are in the current LOD.
            if GEO.lod == 1:
                lodcount = lodcount + 1
            if not ExportVDFOnly and is_mesh_object and not spinner_helper:
                export_geo.geoexport(
                    context,
                    os.path.dirname(filepath) + "/" + GEO.name + ".geo",
                    object,
                    face_plane_mode=face_plane_mode,
                )
            BlenderObject = vdf_classes.BlenderObject(object, GEO)
            blenderobjects.update({GEO.name: BlenderObject})

    """
    Load all the keyframes for more specific handling below.
    """
    for object in blenderobjects.values():
        blobject = object.object
        anim = blobject.animation_data
        if anim is not None and anim.action is not None:
            quat_anim = {}
            has_euler_keys = False
            prefer_quat = getattr(blobject, "rotation_mode", "XYZ") == "QUATERNION"
            for curve in _iter_action_fcurves(anim.action):
                data_path = curve.data_path
                for akeyframe in curve.keyframe_points:
                    keyframe = int(akeyframe.co[0])
                    keyvalue = akeyframe.co[1]
                    if data_path == "rotation_euler" and not prefer_quat:
                        has_euler_keys = True
                        if keyframe not in object.rotanim:
                            object.rotanim[keyframe] = [0.0, 0.0, 0.0]
                        object.rotanim[keyframe][curve.array_index] = keyvalue
                    elif data_path == "rotation_quaternion":
                        if keyframe not in quat_anim:
                            quat_anim[keyframe] = [1.0, 0.0, 0.0, 0.0]
                        quat_anim[keyframe][curve.array_index] = keyvalue
                    elif data_path == "location":
                        if keyframe not in object.posanim:
                            object.posanim[keyframe] = [0.0, 0.0, 0.0]
                        object.posanim[keyframe][curve.array_index] = keyvalue
                    elif data_path == "scale":
                        if keyframe not in object.scaleanim:
                            object.scaleanim[keyframe] = [
                                float(v) for v in blobject.scale
                            ]
                        object.scaleanim[keyframe][curve.array_index] = keyvalue

            if quat_anim and (prefer_quat or not has_euler_keys):
                from mathutils import Quaternion

                for frame, quat_vals in quat_anim.items():
                    q = Quaternion(quat_vals)
                    eul = q.to_euler("XYZ")
                    object.rotanim[frame] = [eul.x, eul.y, eul.z]

    """
    Read the element data in blender and get it ready for writing later.
    """
    for item in scene.AnimationCollection:
        newelement = vdf_classes.ANIMElement()
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
    Create orientations for all the objects (also collects keys).
    """
    for object in blenderobjects.values():
        neworientation = vdf_classes.ANIMOrientation()
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
            newrotation = vdf_classes.ANIMRotation()
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
                ObjectInverse = object.object.matrix_parent_inverse.to_translation()
                tx = ObjectInverse.x + array[0]
                ty = ObjectInverse.z + array[2]
                tz = ObjectInverse.y + array[1]

            newposition = vdf_classes.ANIMPosition()
            newposition.frame = key
            newposition.translate = tx, ty, tz
            pos_index = pos_index + 1
            ANIMPositions.append(newposition)
        if use_translation2:
            for key, array in object.scaleanim.items():
                newtranslation = vdf_classes.ANIMTranslation2()
                newtranslation.frame = key
                newtranslation.translate = array[0], array[2], array[1]
                trans2_index = trans2_index + 1
                ANIMTranslations.append(newtranslation)

    """
    Reorder objects based on parenting and lods. If they are the wrong order,
    everything will blow up! PARENT MUST COME BEFORE CHILD!
    """
    NULLBlenderObject = vdf_classes.BlenderObject(None, NULLGEO)
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
                    if object.geo.parent in orderednames:
                        objects[0][numindex] = object
                        orderednames.append(fixgeoname(object.geo.name, 1))
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

    _enforce_spinner_helper_order(objects)

    # ------------------------------------------------------------------
    # Preserve unknown chunks captured at import.
    # ------------------------------------------------------------------
    preserved_store = getattr(scene, "bz_preserved_chunks", None)
    for entry in list(preserved_store) if preserved_store is not None else []:
        try:
            payload = base64.b64decode(entry.payload_b64)
        except Exception:
            continue
        model.raw_chunks.append(vdf_file.RawChunk(entry.tag.encode("ascii", "ignore")[:4].ljust(4, b"\0"), payload))

    # ------------------------------------------------------------------
    # VLOC injection entries authored/preserved in the scene.
    # ------------------------------------------------------------------
    vloc_store = getattr(scene, "bz_vloc_chunks", None)
    for entry in list(vloc_store) if vloc_store is not None else []:
        chunk = semantics.VLOCChunk()
        kind = str(getattr(entry, "kind", "GENERIC"))
        if kind == "HEADLIGHT":
            chunk.kind_value = semantics.VLOC_HEADLIGHT
        elif kind == "POV":
            chunk.kind_value = semantics.VLOC_POV
        elif kind == "IDSIZES":
            chunk.kind_value = semantics.VLOC_IDSIZES
        else:
            chunk.kind_value = int(getattr(entry, "class_id", 0)) & 0xFFFFFFFF
        chunk.class_id = chunk.kind_value
        chunk.matrix = [float(v) for v in entry.matrix]
        try:
            chunk.opaque_payload = base64.b64decode(entry.payload_b64)
        except Exception:
            chunk.opaque_payload = b""
        chunk.preserve_raw = bool(getattr(entry, "preserve_raw", True))
        model.vlocs.append(chunk)

    # Fresh (non-imported) plans carry no vloc/raw slots; give every stored
    # entry a plan position so newly authored injections serialize too.
    while sum(1 for kind, _ in model.plan if kind == "vloc") < len(model.vlocs):
        model.plan.append(
            ("vloc", sum(1 for kind, _ in model.plan if kind == "vloc"))
        )
    while sum(1 for kind, _ in model.plan if kind == "raw") < len(model.raw_chunks):
        model.plan.append(
            ("raw", sum(1 for kind, _ in model.plan if kind == "raw"))
        )

    # ------------------------------------------------------------------
    # Damage representation records (bands 1..3 = primary geometry states).
    # ------------------------------------------------------------------
    name_to_slot = {}
    for idx, wrapper in enumerate(objects[0]):
        if not _is_null_blender_object(wrapper):
            name_to_slot[wrapper.geo.name.lower()] = idx

    damage_table = semantics.DamageVariantTable()
    for idx, wrapper in enumerate(objects[0]):
        if _is_null_blender_object(wrapper):
            continue
        # Base records feed variant synthesis (name-only rewrites).
        damage_table.base_records[idx] = vdf_classes.serialize_section(wrapper.geo)
        geo_props = getattr(wrapper.object, "GEOPropertyGroup", None)
        if geo_props is None:
            continue
        for state in semantics.AUTHORED_DAMAGE_STATES:
            variant_name = str(getattr(geo_props, f"DamageGeo{state}", "") or "").strip().lower()
            if variant_name and variant_name != "null":
                damage_table.set_variant_name(idx, state, variant_name[:8])

    damage_store = getattr(scene, "bz_damage_band_records", None)
    for entry in list(damage_store) if damage_store is not None else []:
        try:
            payload = base64.b64decode(entry.payload_b64)[:100]
        except Exception:
            continue
        if len(payload) < 100:
            continue
        part_name = str(getattr(entry, "part_name", "") or "").strip().lower()
        band = int(getattr(entry, "band", -1))
        slot = int(getattr(entry, "slot", -1))
        if part_name and part_name in name_to_slot:
            resolved = name_to_slot[part_name]
            if slot in (-1,) or slot == resolved:
                slot = resolved
            else:
                slot = resolved
        if slot < 0 or band < 0 or band >= semantics.VGEO_BAND_COUNT:
            continue
        damage_table.variant_records[(slot, band)] = payload

    # ------------------------------------------------------------------
    # Build the 28-band record grid.
    # ------------------------------------------------------------------
    records = [None] * (semantics.VGEO_BAND_COUNT * lodcount)

    def _wrapper_record(band_list, slot):
        wrapper = band_list[slot] if slot < len(band_list) else NULLBlenderObject
        if _is_null_blender_object(wrapper):
            return NULL_RECORD
        return vdf_classes.serialize_section(wrapper.geo)

    for slot in range(lodcount):
        for band in range(semantics.VGEO_BAND_COUNT):
            lod_slot, damage_state = semantics.band_coords(band)
            if lod_slot == 0:
                records[band * lodcount + slot] = damage_table.build_band_record(
                    slot, band
                ) if damage_state != 0 else _wrapper_record(objects[0], slot)
            elif lod_slot in (1, 2):
                if damage_state == 0:
                    records[band * lodcount + slot] = _wrapper_record(
                        objects[lod_slot], slot
                    )
                else:
                    records[band * lodcount + slot] = damage_table.build_band_record(
                        slot, band
                    )
            else:
                records[band * lodcount + slot] = damage_table.build_band_record(
                    slot, band
                )

    # ------------------------------------------------------------------
    # Fill optional sections into the model.
    # ------------------------------------------------------------------
    model.geocount = lodcount
    model.records = records

    if ANIMElements and ExportAnimations:
        model.anim_present = True
        model.anim_header = vdf_classes.ANIMHeader()
        if getattr(scene_props, "UseAdvancedAnimHeader", False):
            model.anim_header.null2 = int(scene_props.AnimNull2)
            model.anim_header.unknown2 = int(scene_props.AnimUnknown2)
            model.anim_header._reserved = [
                int(v) for v in scene_props.AnimReserved
            ]
        else:
            model.anim_header.null2 = 0
            model.anim_header.unknown2 = 0
            model.anim_header._reserved = [0, 0, 0, 0, 0]
        model.anim_elements = ANIMElements
        model.anim_orientations = ANIMOrientations
        model.anim_rotations = ANIMRotations
        model.anim_translations2 = ANIMTranslations
        model.anim_positions = ANIMPositions
    elif "anim" in model.plan:
        # Drop the anim block plus its terminator when there is nothing to write.
        cleaned = []
        skip_next_exit = False
        for kind, index in model.plan:
            if skip_next_exit:
                if kind == "exit":
                    skip_next_exit = False
                    continue
                skip_next_exit = False
            if kind == "anim":
                skip_next_exit = True
                continue
            cleaned.append((kind, index))
        model.plan = cleaned

    XInMin, XInMax, YInMin, YInMax, ZInMin, ZInMax = [0.0] * 6
    XOutMin, XOutMax, YOutMin, YOutMax, ZOutMin, ZOutMax = [0.0] * 6
    if collisioninner != None:
        XInMin = XInMax = collisioninner.data.vertices[0].co.x
        YInMin = YInMax = collisioninner.data.vertices[0].co.y
        ZInMin = ZInMax = collisioninner.data.vertices[0].co.z
        for vert in collisioninner.data.vertices:
            if vert.co.x < XInMin:
                XInMin = vert.co.x
            if vert.co.x > XInMax:
                XInMax = vert.co.x
            if vert.co.y < YInMin:
                YInMin = vert.co.y
            if vert.co.y > YInMax:
                YInMax = vert.co.y
            if vert.co.z < ZInMin:
                ZInMin = vert.co.z
            if vert.co.z > ZInMax:
                ZInMax = vert.co.z
    if collisionouter != None:
        XOutMin = XOutMax = collisionouter.data.vertices[0].co.x
        YOutMin = YOutMax = collisionouter.data.vertices[0].co.y
        ZOutMin = ZOutMax = collisionouter.data.vertices[0].co.z
        for vert in collisionouter.data.vertices:
            if vert.co.x < XOutMin:
                XOutMin = vert.co.x
            if vert.co.x > XOutMax:
                XOutMax = vert.co.x
            if vert.co.y < YOutMin:
                YOutMin = vert.co.y
            if vert.co.y > YOutMax:
                YOutMax = vert.co.y
            if vert.co.z < ZOutMin:
                ZOutMin = vert.co.z
            if vert.co.z > ZOutMax:
                ZOutMax = vert.co.z

    if "colp" in [kind for kind, _ in model.plan]:
        model.colp_data = [
            YOutMax,
            YInMax,
            YInMin,
            YOutMin,
            XOutMax,
            XInMax,
            XInMin,
            XOutMin,
            ZOutMax,
            ZInMax,
            ZInMin,
            ZOutMin,
        ]

    if "scps" in [kind for kind, _ in model.plan]:
        model.scps_tag = "SPCS"
        if getattr(scene_props, "UseCustomSCPS", False):
            model.scps_data = [int(v) for v in scene_props.SCPSData]
        else:
            model.scps_data = [0, 0, 0]

    payload = vdf_file.serialize_vdf(model)
    with open(filepath, mode="wb") as file:  # b is important -> binary
        file.write(payload)

    return {"FINISHED"}
