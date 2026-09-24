"""Transform helpers shared by the VDF and SDF exporters.

* Rotations are read in the object's own ``rotation_mode``. The importer
  creates parts in YZX mode, so treating ``rotation_euler`` as XYZ broke any
  compound rotation on a round trip.
* A LOD2/LOD3 part parented to its own LOD1 counterpart (the layout the
  validator asks for) is written against that counterpart's parent. Writing
  the Blender parent in LOD2 form would name the part itself, a record the
  importer cannot load. The record takes the composed transform and keys of
  the counterpart and the part, like the stock cockpits (agr21bda is a WORLD
  child with the hull's placement; age21hed sits under age21ty1).
"""

import mathutils

ROTATION_PATHS = ("rotation_euler", "rotation_quaternion", "rotation_axis_angle")


def xyz_euler(obj):
    """The object's rest rotation as an XYZ euler, whatever its rotation_mode."""
    mode = obj.rotation_mode
    if mode == "XYZ":
        return obj.rotation_euler.copy()
    if mode == "QUATERNION":
        return obj.rotation_quaternion.normalized().to_euler("XYZ")
    if mode == "AXIS_ANGLE":
        a = obj.rotation_axis_angle
        return mathutils.Quaternion((a[1], a[2], a[3]), a[0]).to_euler("XYZ")
    return mathutils.Euler(obj.rotation_euler[:], mode).to_matrix().to_euler("XYZ")


def euler_keys_to_xyz(rotanim, mode):
    """Re-express rotation_euler keys authored in another euler order as XYZ."""
    if mode in ("XYZ", "QUATERNION", "AXIS_ANGLE"):
        return
    for frame, values in rotanim.items():
        e = mathutils.Euler(values, mode).to_matrix().to_euler("XYZ")
        rotanim[frame] = [e.x, e.y, e.z]


def lod_slot(obj):
    """The LOD1 counterpart this LOD2/LOD3 part is parented to, else None."""
    parent = obj.parent
    if parent is None:
        return None
    name, pname = obj.name.lower(), parent.name.lower()
    if not (5 <= len(name) <= 8 and len(pname) == len(name)):
        return None
    if name[3] in "23" and pname[3] == "1" and name[:3] + name[5:] == pname[:3] + pname[5:]:
        return parent
    return None


def export_parent_name(obj, lod, fixgeoname):
    """Parent record name for obj, or WORLD."""
    parent = obj.parent
    if lod_slot(obj) is not None:
        parent = parent.parent
    if parent is None:
        return "WORLD"
    pname = parent.name.lower()
    if not (5 <= len(pname) <= 8) or pname[3] not in "123":
        return "WORLD"
    return fixgeoname(parent.name, lod).lower()


def rest_local(obj):
    """(XYZ euler, scale, translation) of the record's local transform."""
    slot = lod_slot(obj)
    if slot is None:
        return xyz_euler(obj), tuple(obj.scale), obj.matrix_local.to_translation()
    loc, rot, scale = (slot.matrix_local @ obj.matrix_local).decompose()
    return rot.to_euler("XYZ"), tuple(scale), loc


def _curves(obj, iter_fcurves):
    anim = obj.animation_data
    if anim is None or anim.action is None:
        return {}
    return {(fc.data_path, fc.array_index): fc for fc in iter_fcurves(anim.action)}


def _basis_at(obj, curves, frame):
    def channel(path, i, rest):
        fc = curves.get((path, i))
        return fc.evaluate(frame) if fc is not None else rest

    loc = mathutils.Vector([channel("location", i, obj.location[i]) for i in range(3)])
    scale = mathutils.Vector([channel("scale", i, obj.scale[i]) for i in range(3)])
    mode = obj.rotation_mode
    if mode == "QUATERNION":
        rot = mathutils.Quaternion(
            [channel("rotation_quaternion", i, obj.rotation_quaternion[i]) for i in range(4)]
        ).normalized()
    elif mode == "AXIS_ANGLE":
        a = [channel("rotation_axis_angle", i, obj.rotation_axis_angle[i]) for i in range(4)]
        rot = mathutils.Quaternion(a[1:], a[0])
    else:
        rot = mathutils.Euler(
            [channel("rotation_euler", i, obj.rotation_euler[i]) for i in range(3)], mode
        ).to_quaternion()
    return mathutils.Matrix.LocRotScale(loc, rot, scale)


def slot_keys(obj, iter_fcurves):
    """(rotanim, posanim) for a part written against its LOD1 slot's parent.

    Keys land on every frame either object is keyed on. posanim is the final
    local translation (no parent-inverse offset to add).
    """
    slot = lod_slot(obj)
    slot_curves, own_curves = _curves(slot, iter_fcurves), _curves(obj, iter_fcurves)
    paths = {p for p, _ in slot_curves} | {p for p, _ in own_curves}
    frames = sorted(
        {int(kp.co[0]) for c in (slot_curves, own_curves) for fc in c.values() for kp in fc.keyframe_points}
    )
    rot_keyed = any(p in ROTATION_PATHS for p in paths)
    pos_keyed = "location" in paths or (
        rot_keyed and obj.matrix_local.to_translation().length > 1e-6
    )
    rotanim, posanim = {}, {}
    for frame in frames:
        m = (slot.matrix_parent_inverse @ _basis_at(slot, slot_curves, frame)) @ (
            obj.matrix_parent_inverse @ _basis_at(obj, own_curves, frame)
        )
        loc, rot, _ = m.decompose()
        if rot_keyed:
            e = rot.to_euler("XYZ")
            rotanim[frame] = [e.x, e.y, e.z]
        if pos_keyed:
            posanim[frame] = [loc.x, loc.y, loc.z]
    return rotanim, posanim
