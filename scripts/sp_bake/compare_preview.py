"""Render before/after stored-normal tiles for two OBJ folders from ogre_to_obj.py.

  blender -b --factory-startup -P compare_preview.py -- <before_obj_dir> <after_obj_dir> <tile_dir> [size]

Writes <tile_dir>/<stem>_b<i>.png and <stem>_a<i>.png (three views each, same
camera for both); contact_sheets.py stitches them into labelled pages.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
before, after, out_dir = Path(argv[0]), Path(argv[1]), Path(argv[2])
size = int(argv[3]) if len(argv) > 3 else 320
out_dir.mkdir(parents=True, exist_ok=True)

VIEWS = [(35, 30), (145, 20), (270, 60)]  # (azimuth, elevation) degrees


def load(obj_path: Path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
    except TypeError:
        scene.render.engine = "BLENDER_WORKBENCH_NEXT"
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "SINGLE"
    shading.single_color = (0.8, 0.8, 0.8)
    shading.show_backface_culling = True
    scene.render.resolution_x = scene.render.resolution_y = size
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.lens = 50
    bpy.ops.wm.obj_import(filepath=str(obj_path), forward_axis="NEGATIVE_Z", up_axis="Y")
    meshes = [o for o in scene.objects if o.type == "MESH"]
    for o in meshes:
        for poly in o.data.polygons:
            poly.use_smooth = True
    return scene, cam, meshes


def bounds(meshes):
    lo = Vector((min((o.matrix_world @ Vector(v))[i] for o in meshes for v in o.bound_box) for i in range(3)))
    hi = Vector((max((o.matrix_world @ Vector(v))[i] for o in meshes for v in o.bound_box) for i in range(3)))
    return (lo + hi) / 2, (hi - lo).length / 2


for b in sorted(before.glob("*.obj")):
    a = after / b.name
    if not a.exists():
        continue
    frame = None
    for tag, path in (("b", b), ("a", a)):
        scene, cam, meshes = load(path)
        if not meshes:
            break
        if frame is None:
            frame = bounds(meshes)
        centre, radius = frame
        for i, (az, el) in enumerate(VIEWS):
            az, el = math.radians(az), math.radians(el)
            d = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
            cam.location = centre + d * radius * 3.2
            cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
            scene.render.filepath = str(out_dir / f"{b.stem}_{tag}{i}.png")
            bpy.ops.render.render(write_still=True)
    print("tiles:", b.stem)
