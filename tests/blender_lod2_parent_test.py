# Headless Blender test for LOD2 parenting and rotation modes in the VDF exporter.
# Run: blender --background --factory-startup --python tests/blender_lod2_parent_test.py
# Uses only synthetic data; no game assets.
#
# * A LOD2 part parented to its own LOD1 counterpart (the layout the validator
#   asks for) must be written against the counterpart's parent, with the
#   counterpart's placement and keys. It used to be written as its own parent,
#   which the importer could not load.
# * A file that already has such a self-parented record must still import.
# * A freshly authored (never imported) VDF must get its ANIM chunk.
# * A part's rest rotation must be read in its own rotation_mode (the importer
#   creates parts in YZX mode).

import os
import shutil
import struct
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(tempfile.gettempdir(), "bz_lod2_parent")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
sys.path.insert(0, REPO_ROOT)

import bpy  # noqa: E402
import mathutils  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")
    if not condition:
        FAILURES.append(name)


import bz98tools  # noqa: E402

bz98tools.register()
from bz98tools import export_vdf, vdf_file  # noqa: E402


def add_box(name, location=(0, 0, 0), parent=None):
    mesh = bpy.data.meshes.new(name)
    h = 0.5
    verts = [(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.location = location
    obj.GEOPropertyGroup.GEOType = 60
    return obj


def records(path):
    parsed = vdf_file.parse_vdf(open(path, "rb").read())
    out = {}
    for raw in parsed.records:
        name = raw[0:8].split(b"\0")[0].decode(errors="replace").lower()
        if name and name not in out:
            out[name] = (struct.unpack("<12f", raw[8:56]), raw[56:64].split(b"\0")[0].decode().lower())
    keys = {}
    for o in parsed.anim_orientations:
        name = o.name if isinstance(o.name, str) else o.name.decode(errors="replace")
        rot = parsed.anim_rotations[o.rotationindex:o.rotationindex + o.rotationcount]
        keys[name.split("\0")[0].lower()] = [(r.frame, tuple(r.translate)) for r in rot]
    return out, keys


def close(a, b, tol=1e-4):
    return len(a) == len(b) and all(abs(x - y) < tol for x, y in zip(a, b))


def export(name, animations=True, vdf_only=True):
    path = os.path.join(TMP, name)
    result = export_vdf.export(bpy.context, filepath=path, ExportAnimations=animations, ExportVDFOnly=vdf_only)
    check(f"{name} exports", result == {"FINISHED"})
    return path


# ---------------------------------------------------------------------------
# Scene: hull, turret, head (keyed), and the head's cockpit parented to it.
# ---------------------------------------------------------------------------
scene = bpy.context.scene
scene.SDFVDFPropertyGroup.Name = "lodtest"
hull = add_box("tst11bda")
turret = add_box("tst11ty1", location=(0, 1, 2), parent=hull)
turret.GEOPropertyGroup.GEOType = 65
head = add_box("tst11hed", location=(0.5, 0, 1), parent=turret)
head.GEOPropertyGroup.GEOType = 66
head.rotation_mode = "XYZ"
for frame, angle in ((0, 0.0), (10, 0.6), (20, -0.4)):
    head.rotation_euler = (angle, 0.0, angle * 0.5)
    head.keyframe_insert("rotation_euler", frame=frame)
head.rotation_euler = (0.0, 0.0, 0.0)
cockpit = add_box("tst21hed", parent=head)
root_cockpit = add_box("tst21bda", parent=hull)
pov = bpy.data.objects.new("tst11pov", None)
scene.collection.objects.link(pov)
pov.parent = hull
pov.location = (0, 1, 1)
pov.GEOPropertyGroup.GEOType = 40
element = scene.AnimationCollection.add()
element.Index, element.Start, element.Length, element.Loop, element.Speed = 4, 0, 21, 1, 1.0
bpy.context.view_layer.update()

path = export("lod2.vdf", vdf_only=False)
recs, keys = records(path)
plan = [kind for kind, _ in vdf_file.parse_vdf(open(path, "rb").read()).plan]
check("freshly authored VDF gets its ANIM chunk in stock order",
      plan[:6] == ["vdfc", "exit", "vgeo", "anim", "exit", "exit"], str(plan))
check("LOD2 slot child is not its own parent", recs["tst21hed"][1] != "tst21hed", recs["tst21hed"][1])
check("LOD2 slot child takes the slot's parent (LOD2 form)", recs["tst21hed"][1] == "tst21ty1", recs["tst21hed"][1])
check("LOD2 slot child takes the slot's placement", close(recs["tst21hed"][0], recs["tst11hed"][0]))
check("root cockpit is a WORLD child", recs["tst21bda"][1] == "world", recs["tst21bda"][1])
check("root cockpit takes the hull's placement", close(recs["tst21bda"][0], recs["tst11bda"][0]))
check(
    "LOD2 slot child takes the slot's keys",
    [f for f, _ in keys.get("tst21hed", [])] == [f for f, _ in keys.get("tst11hed", [])]
    and all(close(a, b, 1e-4) or close(a, [-v for v in b], 1e-4)
            for (_, a), (_, b) in zip(keys["tst21hed"], keys["tst11hed"])),
    f"{len(keys.get('tst21hed', []))} vs {len(keys.get('tst11hed', []))} keys",
)

# ---------------------------------------------------------------------------
# A file with a self-parented record (as older exports wrote) still imports.
# ---------------------------------------------------------------------------
data = bytearray(open(path, "rb").read())
at = data.find(b"tst21bda")
while at >= 0 and data[at + 56:at + 61] != b"WORLD":
    at = data.find(b"tst21bda", at + 1)
check("found the root cockpit record", at >= 0)
data[at + 56:at + 64] = b"tst21bda"
bad = os.path.join(TMP, "selfparent.vdf")
open(bad, "wb").write(data)
bpy.ops.wm.read_factory_settings(use_empty=True)
try:
    bpy.ops.import_scene.vdf(filepath=bad)
    obj = next((o for o in bpy.data.objects if o.name.lower().startswith("tst21bda")), None)
    check("self-parented record imports as a WORLD child", obj is not None and obj.parent is None)
except Exception as exc:  # noqa: BLE001
    check("self-parented record imports as a WORLD child", False, str(exc).splitlines()[-1])

# ---------------------------------------------------------------------------
# Rest rotation is read in the part's own rotation_mode.
# ---------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.SDFVDFPropertyGroup.Name = "rottest"
hull = add_box("rot11bda")
gun = add_box("rot11gun", location=(1, 2, 3), parent=hull)
pov = bpy.data.objects.new("rot11pov", None)
scene.collection.objects.link(pov)
pov.parent = hull
pov.GEOPropertyGroup.GEOType = 40
target = mathutils.Euler((0.3, -0.5, 0.9), "XYZ").to_quaternion()
gun.rotation_mode = "XYZ"
gun.rotation_euler = target.to_euler("XYZ")
reference = records(export("rot_xyz.vdf", animations=False))[0]["rot11gun"][0]
for mode in ("QUATERNION", "AXIS_ANGLE", "YZX", "ZXY"):
    gun.rotation_mode = mode
    if mode == "QUATERNION":
        gun.rotation_quaternion = target
    elif mode == "AXIS_ANGLE":
        axis, angle = target.to_axis_angle()
        gun.rotation_axis_angle = (angle, *axis)
    else:
        gun.rotation_euler = target.to_euler(mode)
    got = records(export(f"rot_{mode.lower()}.vdf", animations=False))[0]["rot11gun"][0]
    check(f"{mode} rest rotation exported as authored", close(got, reference))

print("\nALL LOD2/ROTATION CHECKS PASSED" if not FAILURES else f"\nFAILED: {FAILURES}")
sys.exit(1 if FAILURES else 0)
