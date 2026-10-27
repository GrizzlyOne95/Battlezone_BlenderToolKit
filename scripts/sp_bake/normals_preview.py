"""Render a normals audit sheet for OBJs written by ogre_to_obj.py.

Top row: stored (custom) normals, bottom row: flat face normals, three views.
Smooth-shading gradients across panels that look flat in the bottom row, or
dark streaks/black faces, are wonky or flipped custom normals.

  blender -b --factory-startup -P normals_preview.py -- <obj_or_dir> <out_dir>
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir = Path(argv[0]), Path(argv[1])
out_dir.mkdir(parents=True, exist_ok=True)
objs = sorted(src.glob("*.obj")) if src.is_dir() else [src]

VIEWS = [(35, 30), (145, 20), (270, 60)]  # (azimuth, elevation) degrees


def setup_scene():
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
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.film_transparent = False
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def render_sheet(obj_path: Path):
    scene, cam = setup_scene()
    bpy.ops.wm.obj_import(filepath=str(obj_path), forward_axis="NEGATIVE_Z", up_axis="Y")
    meshes = [o for o in scene.objects if o.type == "MESH"]
    lo = Vector((min(v[i] for o in meshes for v in o.bound_box) for i in range(3)))
    hi = Vector((max(v[i] for o in meshes for v in o.bound_box) for i in range(3)))
    centre, radius = (lo + hi) / 2, (hi - lo).length / 2
    cam.data.lens = 50
    tiles = []
    for flat in (False, True):
        for o in meshes:
            for poly in o.data.polygons:
                poly.use_smooth = not flat
            if flat and o.data.has_custom_normals:
                o.data.normals_split_custom_set([(0, 0, 0)] * len(o.data.loops))
        for i, (az, el) in enumerate(VIEWS):
            az, el = math.radians(az), math.radians(el)
            d = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
            cam.location = centre + d * radius * 3.2
            cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
            path = out_dir / f"_{obj_path.stem}_{int(flat)}{i}.png"
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            tiles.append(path)
    # stitch 3x2
    imgs = [bpy.data.images.load(str(p)) for p in tiles]
    w, h = imgs[0].size
    sheet = bpy.data.images.new("sheet", w * 3, h * 2)
    px = [0.0] * (w * 3 * h * 2 * 4)
    for idx, img in enumerate(imgs):
        col, row = idx % 3, 1 - idx // 3  # stored normals on top
        src_px = list(img.pixels)
        for y in range(h):
            dst = ((row * h + y) * w * 3 + col * w) * 4
            px[dst:dst + w * 4] = src_px[y * w * 4:(y + 1) * w * 4]
    sheet.pixels = px
    sheet.filepath_raw = str(out_dir / f"{obj_path.stem}_normals.png")
    sheet.file_format = "PNG"
    sheet.save()
    for p in tiles:
        p.unlink()


for obj in objs:
    render_sheet(obj)
    print("sheet:", obj.stem)
