"""Lit before/after renders of a mesh with and without a derived normal map.

  blender -b --factory-startup -P normalmap_preview.py -- <obj> <diffuse> <normal.png> <out.png> [size]

Top row: diffuse only (flat_N).  Bottom row: diffuse + the BZBase normal map
(green = DirectX, flipped back to Blender's +V here).  Three views under a
low raking sun, EEVEE.
"""

import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
obj, diffuse, normal, out = argv[:4]
size = int(argv[4]) if len(argv) > 4 else 400
VIEWS = [(35, 25), (145, 20), (250, 35)]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
    try:
        scene.render.engine = eng
        break
    except TypeError:
        continue
scene.render.resolution_x = scene.render.resolution_y = size
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w")
world.color = (0.08, 0.08, 0.09)
scene.world = world
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 4.0
sun.rotation_euler = (math.radians(70), 0, math.radians(40))
scene.collection.objects.link(sun)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
scene.camera = cam

bpy.ops.wm.obj_import(filepath=obj, forward_axis="NEGATIVE_Z", up_axis="Y")
meshes = [o for o in scene.objects if o.type == "MESH"]
for o in meshes:
    for poly in o.data.polygons:
        poly.use_smooth = True
lo = Vector((min((o.matrix_world @ Vector(v))[i] for o in meshes for v in o.bound_box) for i in range(3)))
hi = Vector((max((o.matrix_world @ Vector(v))[i] for o in meshes for v in o.bound_box) for i in range(3)))
centre, radius = (lo + hi) / 2, (hi - lo).length / 2

dimg = bpy.data.images.load(diffuse)
nimg = bpy.data.images.load(normal)
nimg.colorspace_settings.name = "Non-Color"
mat = bpy.data.materials.new("m")
mat.use_nodes = True
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
bsdf.inputs["Roughness"].default_value = 0.6
dtex = nt.nodes.new("ShaderNodeTexImage")
dtex.image = dimg
nt.links.new(dtex.outputs["Color"], bsdf.inputs["Base Color"])
ntex = nt.nodes.new("ShaderNodeTexImage")
ntex.image = nimg
sep = nt.nodes.new("ShaderNodeSeparateColor")
inv = nt.nodes.new("ShaderNodeMath")
inv.operation = "SUBTRACT"
inv.inputs[0].default_value = 1.0
comb = nt.nodes.new("ShaderNodeCombineColor")
nmap = nt.nodes.new("ShaderNodeNormalMap")
nt.links.new(ntex.outputs["Color"], sep.inputs[0])
nt.links.new(sep.outputs[0], comb.inputs[0])
nt.links.new(sep.outputs[1], inv.inputs[1])
nt.links.new(inv.outputs[0], comb.inputs[1])
nt.links.new(sep.outputs[2], comb.inputs[2])
nt.links.new(comb.outputs[0], nmap.inputs["Color"])
for o in meshes:
    o.data.materials.clear()
    o.data.materials.append(mat)

tiles = []
for tag, on in (("b", False), ("a", True)):
    if on:
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    for i, (az, el) in enumerate(VIEWS):
        az, el = math.radians(az), math.radians(el)
        d = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
        cam.location = centre + d * radius * 2.6
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        path = f"{out}.{tag}{i}.png"
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        tiles.append(path)

sheet = bpy.data.images.new("sheet", size * 3, size * 2)
import numpy as np  # noqa: E402

px = np.zeros((size * 2, size * 3, 4), np.float32)
for k, path in enumerate(tiles):
    im = bpy.data.images.load(path)
    a = np.empty(size * size * 4, np.float32)
    im.pixels.foreach_get(a)
    r, c = divmod(k, 3)
    px[(1 - r) * size:(2 - r) * size, c * size:(c + 1) * size] = a.reshape(size, size, 4)
sheet.pixels.foreach_set(px.ravel())
sheet.filepath_raw = out
sheet.file_format = "PNG"
sheet.save()
print("sheet:", out)
