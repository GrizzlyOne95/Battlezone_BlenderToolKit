"""Bake rounded-edge tangent normals for one OBJ with Cycles' Bevel shader.

  blender -b --factory-startup -P bevel_bake.py -- <job.json>

job.json: {"obj": path, "radius": world units, "samples": int, "margin": px,
           "targets": {"<material>": {"npy": out path, "size": [w, h]}}}

Each target material gets Bevel -> BSDF normal and an image node as the bake
target; one NORMAL/TANGENT bake fills them all.  Results are saved top-down as
float16 .npy in Blender's OpenGL convention (green = +V, up the image);
derive_normals.py flips green for BZBase.  Materials not in "targets" bake
into a throwaway image.
"""

import json
import re
import sys

import bpy

job = json.load(open(sys.argv[sys.argv.index("--") + 1]))
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = job.get("samples", 16)
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.get_devices()
    for kind in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        if any(d.type == kind for d in prefs.devices):
            for d in prefs.devices:
                d.use = True
            scene.cycles.device = "GPU"
            break
except Exception as exc:  # CPU is fine, just slower
    print("bevel_bake: GPU setup failed:", exc)

bpy.ops.wm.obj_import(filepath=job["obj"], forward_axis="NEGATIVE_Z", up_axis="Y")
objs = [o for o in scene.objects if o.type == "MESH"]
for o in objs:
    for poly in o.data.polygons:
        poly.use_smooth = True

images = {}
scratch = None
for o in objs:
    for slot in o.material_slots:
        mat = slot.material
        if mat is None or mat.name in images:
            continue
        name = re.sub(r"\.\d{3}$", "", mat.name)  # drop Blender dup suffix
        tgt = job["targets"].get(name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
        bevel = nt.nodes.new("ShaderNodeBevel")
        bevel.samples = job.get("bevel_samples", 16)
        bevel.inputs["Radius"].default_value = job["radius"]
        nt.links.new(bevel.outputs["Normal"], bsdf.inputs["Normal"])
        nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
        tex = nt.nodes.new("ShaderNodeTexImage")
        if tgt:
            w, h = tgt["size"]
            img = bpy.data.images.new(name + "_bevel", w, h, float_buffer=True)
            img.colorspace_settings.name = "Non-Color"
            images[mat.name] = (img, tgt["npy"])
        else:
            if scratch is None:
                scratch = bpy.data.images.new("scratch", 64, 64)
            img = scratch
            images[mat.name] = (img, None)
        tex.image = img
        nt.nodes.active = tex
        tex.select = True

bpy.ops.object.select_all(action="DESELECT")
for o in objs:
    o.select_set(True)
bpy.context.view_layer.objects.active = objs[0]
bake = scene.render.bake
bake.margin = job.get("margin", 8)
bake.margin_type = "EXTEND"
bake.normal_space = "TANGENT"
bpy.ops.object.bake(type="NORMAL")

import numpy as np  # noqa: E402  (Blender ships numpy)

for img, path in images.values():
    if path:
        w, h = img.size
        px = np.empty(w * h * 4, np.float32)
        img.pixels.foreach_get(px)
        # Blender rows run bottom-up; store top-down like the DDS
        np.save(path, px.reshape(h, w, 4)[::-1, :, :3].astype(np.float16))
        print("bevel:", path)
