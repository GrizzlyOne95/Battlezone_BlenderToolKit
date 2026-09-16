"""Manual Blender 5.2 acceptance test for real OGRE mesh/skeleton pairs.

Usage from the repository root with Blender 5.2's Python (or bpy 5.2.2):

    python tests/blender52_real_asset_smoke.py path/to/model.mesh path/to/model.skeleton

The files are never copied into the repository.  They are staged in a temporary
folder, imported through ``ogrefast.backend`` with the XML fallback forbidden,
then re-exported through the same pure fast path and parsed again.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
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
    raise AssertionError("real-asset smoke unexpectedly reached the legacy XML fallback")


def _clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)


def _material_names(obj):
    return [slot.material.name for slot in obj.material_slots if slot.material]


def _uv_count(obj):
    return len(obj.data.uv_layers)


def _stage_pair(mesh_path: Path, skeleton_path: Path, temp_dir: Path):
    serializer = KenshiObjectSerializer()
    serializer.add_resource_location(str(mesh_path.parent))
    source_mesh = serializer.load_mesh(mesh_path.name)
    linked_name = source_mesh.get_linked_skeleton_name()
    if not linked_name:
        raise AssertionError("source mesh has no linked skeleton")

    staged_mesh = temp_dir / mesh_path.name
    staged_skeleton = temp_dir / linked_name
    shutil.copy2(mesh_path, staged_mesh)
    shutil.copy2(skeleton_path, staged_skeleton)
    return staged_mesh, staged_skeleton, source_mesh


def _assert_source_contract(source_mesh):
    submeshes = source_mesh.get_submeshes()
    if len(submeshes) != 2:
        raise AssertionError(f"expected 2 source submeshes, got {len(submeshes)}")
    if source_mesh.get_linked_skeleton_name() != "aspilo.skeleton":
        raise AssertionError(
            f"unexpected skeleton link {source_mesh.get_linked_skeleton_name()!r}"
        )
    if [submesh.geometry.texcoords_size for submesh in submeshes] != [2, 2]:
        raise AssertionError(
            "source fixture is expected to contain two UV sets on both submeshes"
        )

    skeleton = source_mesh.get_linked_skeleton()
    if skeleton is None:
        raise AssertionError("pure reader did not load the linked source skeleton")
    bones = skeleton.get_bones(has_helper=True)
    animations = skeleton.get_animations()
    if len(bones) != 71:
        raise AssertionError(f"expected 71 source bones, got {len(bones)}")
    if len(animations) != 19:
        raise AssertionError(f"expected 19 source animations, got {len(animations)}")
    return {animation.name for animation in animations}


def _import_fast(staged_mesh: Path, expected_actions: set[str]):
    _clear_scene()
    operator = _Operator()
    result = backend.import_mesh(
        operator,
        bpy.context,
        str(staged_mesh),
        _fallback_forbidden,
        xml_converter=None,
        keep_xml=False,
        import_normals=True,
        normal_mode="custom",
        import_shapekeys=True,
        import_animations=True,
        round_frames=True,
        use_selected_skeleton=False,
        import_materials=True,
    )
    errors = [msg for levels, msg in operator.messages if "ERROR" in levels]
    if result != {"FINISHED"} or errors:
        raise AssertionError(f"fast import failed: {result} {errors}")

    meshes = sorted(
        (obj for obj in bpy.context.scene.objects if obj.type == "MESH"),
        key=lambda obj: obj.name,
    )
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(meshes) != 2:
        raise AssertionError(f"expected 2 imported mesh objects, got {[obj.name for obj in meshes]}")
    if len(armatures) != 1:
        raise AssertionError(f"expected 1 imported armature, got {[obj.name for obj in armatures]}")

    armature = armatures[0]
    if len(armature.data.bones) != 71:
        raise AssertionError(f"expected 71 imported bones, got {len(armature.data.bones)}")

    for obj in meshes:
        if _uv_count(obj) != 2:
            raise AssertionError(f"{obj.name}: expected 2 imported UV sets, got {_uv_count(obj)}")
        if not obj.vertex_groups:
            raise AssertionError(f"{obj.name}: expected imported bone weights")
        modifiers = [modifier for modifier in obj.modifiers if modifier.type == "ARMATURE"]
        if len(modifiers) != 1 or modifiers[0].object is not armature:
            raise AssertionError(f"{obj.name}: missing expected armature modifier")

    materials = sorted(name for obj in meshes for name in _material_names(obj))
    if materials != ["aspilo1", "aspilo2"]:
        raise AssertionError(f"unexpected imported materials: {materials}")

    action_names = {action.name for action in bpy.data.actions}
    missing_actions = expected_actions - action_names
    if missing_actions:
        raise AssertionError(f"missing imported actions: {sorted(missing_actions)}")

    print(
        "[PASS] real asset pure import:",
        len(meshes), "meshes,",
        len(armature.data.bones), "bones,",
        len(expected_actions), "animations, two UV sets",
    )
    return meshes, armature


def _export_fast(temp_dir: Path, meshes, armature, expected_actions: set[str]):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]

    output_mesh = temp_dir / "aspilo_fast52.mesh"
    operator = _Operator()
    result = backend.export_mesh(
        operator,
        bpy.context,
        str(output_mesh),
        _fallback_forbidden,
        xml_converter=None,
        keep_xml=False,
        export_tangents=True,
        export_binormals=True,
        zero_tangents_binormals=False,
        export_colour=True,
        tangent_parity=True,
        apply_transform=False,
        apply_modifiers=False,
        export_materials=False,
        overwrite_material=False,
        copy_textures=False,
        export_skeleton=True,
        export_poses=True,
        export_animation=True,
        renormalize_weights=True,
        batch_export=False,
    )
    errors = [msg for levels, msg in operator.messages if "ERROR" in levels]
    if result != {"FINISHED"} or errors or not output_mesh.is_file():
        raise AssertionError(f"fast export failed: {result} {errors}")

    output_skeleton = output_mesh.with_suffix(".skeleton")
    if not output_skeleton.is_file():
        raise AssertionError("fast export did not create linked .skeleton")

    serializer = KenshiObjectSerializer()
    serializer.add_resource_location(str(temp_dir))
    roundtrip = serializer.load_mesh(output_mesh.name)
    submeshes = roundtrip.get_submeshes()
    if len(submeshes) != 2:
        raise AssertionError(f"re-export changed submesh count: {len(submeshes)}")
    uv_counts = [submesh.geometry.texcoords_size for submesh in submeshes]
    if uv_counts != [2, 2]:
        raise AssertionError(f"re-export did not preserve both UV sets: {uv_counts}")
    if sorted(submesh.material for submesh in submeshes) != ["aspilo1", "aspilo2"]:
        raise AssertionError(
            f"re-export changed materials: {[submesh.material for submesh in submeshes]}"
        )
    if not all(submesh.get_vertex_groups() for submesh in submeshes):
        raise AssertionError("re-export lost bone assignments")

    skeleton = roundtrip.get_linked_skeleton()
    if skeleton is None:
        raise AssertionError("re-exported mesh did not resolve its skeleton")
    if len(skeleton.get_bones(has_helper=True)) != 71:
        raise AssertionError("re-export changed skeleton bone count")
    roundtrip_actions = {animation.name for animation in skeleton.get_animations()}
    missing = expected_actions - roundtrip_actions
    if missing:
        raise AssertionError(f"re-export lost animations: {sorted(missing)}")

    print(
        "[PASS] Blender 5.2 real asset fast-path roundtrip:",
        output_mesh,
        output_skeleton,
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", type=Path)
    parser.add_argument("skeleton", type=Path)
    args = parser.parse_args(argv)

    if bpy.app.version[:2] != (5, 2):
        raise AssertionError(f"expected Blender 5.2, got {bpy.app.version_string}")
    mesh_path = args.mesh.expanduser().resolve()
    skeleton_path = args.skeleton.expanduser().resolve()
    if not mesh_path.is_file() or not skeleton_path.is_file():
        raise FileNotFoundError("mesh and skeleton paths must both exist")

    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_real_ogre52_"))
    staged_mesh, _, source_mesh = _stage_pair(mesh_path, skeleton_path, temp_dir)
    expected_actions = _assert_source_contract(source_mesh)
    meshes, armature = _import_fast(staged_mesh, expected_actions)
    _export_fast(temp_dir, meshes, armature, expected_actions)
    print("BLENDER 5.2 REAL ASSET PURE OGRE FAST-PATH PASSED")


if __name__ == "__main__":
    main()
