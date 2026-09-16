"""Blender 5.2 smoke test for rigged meshes carrying multiple UV sets."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bpy

from bz98tools.ogrefast import backend
from bz98tools.ogrefast.pure.kenshi_facade import KenshiObjectSerializer


class _Operator:
    def __init__(self):
        self.messages = []

    def report(self, levels, message):
        self.messages.append((set(levels), str(message)))
        print("REPORT", sorted(levels), message)


def _fallback_forbidden(*args, **kwargs):
    raise AssertionError("multi-UV smoke unexpectedly reached XML fallback")


def _clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _make_rigged_two_uv_triangle():
    armature_data = bpy.data.armatures.new("MultiUvRigData")
    armature = bpy.data.objects.new("MultiUvRig", armature_data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature_data.edit_bones.new("root")
    bone.head = (0.0, 0.0, 0.0)
    bone.tail = (0.0, 1.0, 0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    armature_data.bones["root"]["OGREID"] = 0

    mesh = bpy.data.meshes.new("MultiUvMesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    mesh.update()
    obj = bpy.data.objects.new("MultiUvPilot", mesh)
    bpy.context.scene.collection.objects.link(obj)

    uv0 = mesh.uv_layers.new(name="UV0")
    uv1 = mesh.uv_layers.new(name="UV1")
    mesh.attributes[uv0.name].data.foreach_set(
        "vector", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    )
    mesh.attributes[uv1.name].data.foreach_set(
        "vector", [0.25, 0.75, 0.75, 0.75, 0.25, 0.25]
    )
    # Deliberately make UV1 active. Ogre TEXCOORD numbering must still follow
    # authored layer order (UV0 -> index 0, UV1 -> index 1), not active status.
    mesh.uv_layers.active = uv1

    material = bpy.data.materials.new("MultiUvMat")
    mesh.materials.append(material)

    group = obj.vertex_groups.new(name="root")
    group.add([0, 1, 2], 1.0, "REPLACE")
    modifier = obj.modifiers.new("OgreSkeleton", "ARMATURE")
    modifier.object = armature
    modifier.use_bone_envelopes = False
    modifier.use_vertex_groups = True

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj, armature


def main():
    print("BLENDER_VERSION", bpy.app.version_string)
    if bpy.app.version[:2] != (5, 2):
        raise AssertionError(f"expected Blender 5.2, got {bpy.app.version_string}")

    _clear_scene()
    obj, _ = _make_rigged_two_uv_triangle()
    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_multi_uv52_"))
    output = temp_dir / "multi_uv.mesh"
    operator = _Operator()
    result = backend.export_mesh(
        operator,
        bpy.context,
        str(output),
        _fallback_forbidden,
        xml_converter=None,
        keep_xml=False,
        export_tangents=False,
        export_binormals=False,
        zero_tangents_binormals=False,
        export_colour=False,
        tangent_parity=True,
        apply_transform=False,
        apply_modifiers=False,
        export_materials=False,
        overwrite_material=False,
        copy_textures=False,
        export_skeleton=True,
        export_poses=False,
        export_animation=False,
        renormalize_weights=True,
        batch_export=False,
    )
    errors = [message for levels, message in operator.messages if "ERROR" in levels]
    if result != {"FINISHED"} or errors:
        raise AssertionError(f"rigged multi-UV fast export failed: {result} {errors}")

    serializer = KenshiObjectSerializer()
    serializer.add_resource_location(str(temp_dir))
    loaded = serializer.load_mesh(output.name)
    submesh = loaded.get_submeshes()[0]
    if submesh.geometry.texcoords_size != 2:
        raise AssertionError(
            f"expected two UV sets in exported Ogre mesh, got {submesh.geometry.texcoords_size}"
        )
    uv_sets = submesh.get_texcoords().reshape(2, -1, 2)
    first0 = tuple(round(float(v), 5) for v in uv_sets[0][0])
    first1 = tuple(round(float(v), 5) for v in uv_sets[1][0])
    if first0 != (0.0, 0.0):
        raise AssertionError(f"TEXCOORD0 was reordered by active UV layer: {first0}")
    if first1 != (0.25, 0.75):
        raise AssertionError(f"TEXCOORD1 changed: {first1}")
    if not submesh.get_vertex_groups():
        raise AssertionError("rigged multi-UV export lost bone weights")
    skeleton = loaded.get_linked_skeleton()
    if skeleton is None or len(skeleton.get_bones(has_helper=True)) != 1:
        raise AssertionError("rigged multi-UV export lost linked skeleton")

    print("[PASS] Blender 5.2 rigged multi-UV pure fast-path export")


if __name__ == "__main__":
    main()
