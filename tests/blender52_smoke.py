"""Real Blender 5.2 smoke coverage for the pure Ogre fast path.

Run with the official ``bpy`` Python module. This intentionally exercises the
actual Blender collector/importer glue rather than only the bpy-free binary
models: shape-key pose export/import, Batch Selected export, the standalone
visual-keying skeleton bake, and the pilot-animation helper are covered.
"""

from __future__ import annotations

from pathlib import Path
import math
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bpy

from bz98tools.ogrefast import backend
from bz98tools.bzrmodelporter.ogreskeleton_serializer import SkeletonSerializer


class _Operator:
    def __init__(self):
        self.messages = []

    def report(self, levels, message):
        self.messages.append((set(levels), str(message)))
        print("REPORT", sorted(levels), message)


def _legacy_forbidden(*args, **kwargs):
    raise AssertionError("Blender 5.2 smoke test unexpectedly reached legacy XML fallback")


def _clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _triangle(name, x_offset=0.0):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(
        [
            (x_offset + 0.0, 0.0, 0.0),
            (x_offset + 1.0, 0.0, 0.0),
            (x_offset + 0.0, 1.0, 0.0),
        ],
        [],
        [(0, 1, 2)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _select_only(*objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def _export_kwargs(**overrides):
    values = dict(
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
        export_skeleton=False,
        export_poses=False,
        export_animation=False,
        renormalize_weights=True,
        batch_export=False,
    )
    values.update(overrides)
    return values


def _test_shape_key_roundtrip(temp_dir: Path):
    _clear_scene()
    obj = _triangle("pose_triangle")
    _select_only(obj)

    obj.shape_key_add(name="Basis")
    lifted = obj.shape_key_add(name="Lift")
    lifted.data[0].co.z += 1.25

    path = temp_dir / "pose_triangle.mesh"
    operator = _Operator()
    result = backend.export_mesh(
        operator,
        bpy.context,
        str(path),
        _legacy_forbidden,
        **_export_kwargs(export_poses=True),
    )
    if result != {"FINISHED"} or not path.is_file():
        raise AssertionError(f"pure pose export failed: {result} {operator.messages}")

    _clear_scene()
    operator = _Operator()
    result = backend.import_mesh(
        operator,
        bpy.context,
        str(path),
        _legacy_forbidden,
        xml_converter=None,
        keep_xml=False,
        import_normals=True,
        normal_mode="custom",
        import_shapekeys=True,
        import_animations=False,
        round_frames=True,
        use_selected_skeleton=False,
        import_materials=False,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"pure pose import failed: {result} {operator.messages}")

    imported = bpy.data.objects.get("pose_triangle")
    if imported is None:
        raise AssertionError("pure pose import did not recreate pose_triangle")
    shape_keys = getattr(imported.data, "shape_keys", None)
    if shape_keys is None or "Lift" not in shape_keys.key_blocks:
        raise AssertionError("pure pose import did not recreate Lift shape key")

    basis_z = float(shape_keys.key_blocks["Basis"].data[0].co.z)
    lifted_z = float(shape_keys.key_blocks["Lift"].data[0].co.z)
    if abs((lifted_z - basis_z) - 1.25) > 1e-5:
        raise AssertionError(
            f"shape-key delta changed across pure roundtrip: {lifted_z - basis_z}"
        )
    print("[PASS] Blender 5.2 shape-key pure roundtrip")


def _test_batch_export(temp_dir: Path):
    _clear_scene()
    alpha = _triangle("alpha")
    beta = _triangle("beta", x_offset=2.0)
    _select_only(alpha, beta)

    operator = _Operator()
    result = backend.export_mesh(
        operator,
        bpy.context,
        str(temp_dir / "chosen.mesh"),
        _legacy_forbidden,
        **_export_kwargs(batch_export=True),
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"pure batch export failed: {result} {operator.messages}")

    for filename in ("alpha.mesh", "beta.mesh"):
        if not (temp_dir / filename).is_file():
            raise AssertionError(f"batch export did not create {filename}")
    if not alpha.select_get() or not beta.select_get():
        raise AssertionError("batch export did not restore Blender selection")
    if bpy.context.view_layer.objects.active is not alpha:
        raise AssertionError("batch export did not restore active Blender object")
    print("[PASS] Blender 5.2 Batch Selected pure export")


def _animated_single_bone_rig():
    armature_data = bpy.data.armatures.new("PilotRigData")
    armature = bpy.data.objects.new("PilotRig", armature_data)
    bpy.context.scene.collection.objects.link(armature)
    _select_only(armature)

    bpy.ops.object.mode_set(mode="EDIT")
    edit_bone = armature_data.edit_bones.new("root")
    edit_bone.head = (0.0, 0.0, 0.0)
    edit_bone.tail = (0.0, 1.0, 0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    armature_data.bones["root"]["OGREID"] = 0

    pose_bone = armature.pose.bones["root"]
    pose_bone.rotation_mode = "QUATERNION"

    bpy.context.scene.render.fps = 30
    bpy.context.scene.render.fps_base = 1.0
    bpy.context.scene.frame_step = 1

    action = bpy.data.actions.new("PilotMove")
    armature.animation_data_create()
    armature.animation_data.action = action

    pose_bone.location = (0.0, 0.0, 0.0)
    pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    pose_bone.keyframe_insert(data_path="location", frame=1)
    pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=1)

    pose_bone.location = (1.0, 0.0, 0.0)
    angle = math.radians(45.0) * 0.5
    pose_bone.rotation_quaternion = (math.cos(angle), math.sin(angle), 0.0, 0.0)
    pose_bone.keyframe_insert(data_path="location", frame=31)
    pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=31)

    bpy.context.scene.frame_set(1)
    return armature


def _test_visual_keying_skeleton_bake(temp_dir: Path):
    _clear_scene()
    armature = _animated_single_bone_rig()
    _select_only(armature)

    # Import the historical collector unchanged. ogrefast package startup must
    # have installed the pure compatibility facade before this star import.
    import kenshi_blender_tool
    from bz98tools.ogrefast import ogre_exporter

    if not getattr(kenshi_blender_tool, "__bz98_pure_backend__", False):
        raise AssertionError("Blender 5.2 did not install the pure kenshi facade")
    if ogre_exporter.KenshiObjectSerializer is not kenshi_blender_tool.KenshiObjectSerializer:
        raise AssertionError("legacy Ogre exporter did not bind to the pure facade")

    path = temp_dir / "pilot_bake.skeleton"
    operator = _Operator()
    result = ogre_exporter.save_skeleton(
        operator=operator,
        context=bpy.context,
        filepath=str(path),
        apply_transform=False,
        export_animation=True,
        export_all_bones=False,
        export_version="V_1_10",
        is_visual_keying=True,
        use_scale_keyframe=False,
    )
    errors = [message for levels, message in operator.messages if "ERROR" in levels]
    if result != {"FINISHED"} or errors or not path.is_file():
        raise AssertionError(
            f"pure visual skeleton bake failed: result={result} errors={errors} reports={operator.messages}"
        )

    with path.open("rb") as stream:
        skeleton = SkeletonSerializer(stream).read()
    animations = list(skeleton.animations())
    if [animation.name for animation in animations] != ["PilotMove"]:
        raise AssertionError(
            f"unexpected baked animations: {[animation.name for animation in animations]}"
        )
    animation = animations[0]
    if abs(float(animation.duration) - 1.0) > 1e-5:
        raise AssertionError(f"baked duration changed: {animation.duration}")

    tracks = list(animation.tracks())
    if len(tracks) != 1 or tracks[0].target_bone.name != "root":
        raise AssertionError("visual bake did not preserve the root animation track")
    keyframes = tracks[0].keyframe_list
    if len(keyframes) != 31:
        raise AssertionError(f"expected 31 baked samples, got {len(keyframes)}")
    first = keyframes[0].translation
    last = keyframes[-1].translation
    first_len = math.sqrt(first.x * first.x + first.y * first.y + first.z * first.z)
    last_len = math.sqrt(last.x * last.x + last.y * last.y + last.z * last.z)
    if first_len > 1e-5 or abs(last_len - 1.0) > 1e-4:
        raise AssertionError(
            f"baked translation magnitude changed: first={first_len} last={last_len}"
        )

    print("[PASS] Blender 5.2 pure visual-keying .skeleton bake")


def _test_pilot_ui_baked_replacement(temp_dir: Path):
    _clear_scene()
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)

    armature = _animated_single_bone_rig()
    _select_only(armature)
    source_action = armature.animation_data.action
    original_selection = list(bpy.context.selected_objects)
    original_active = bpy.context.view_layer.objects.active

    from bz98tools import pilot_animation_ui

    operator = _Operator()
    replacement = pilot_animation_ui._build_baked_replacement(
        bpy.context,
        operator,
        armature,
        [("PilotMove", source_action)],
        str(temp_dir),
    )

    if set(replacement.animation_map) != {"PilotMove"}:
        raise AssertionError(
            f"pilot helper returned unexpected clips: {sorted(replacement.animation_map)}"
        )
    animation = replacement.animation_map["PilotMove"]
    if abs(float(animation.duration) - 1.0) > 1e-5:
        raise AssertionError(f"pilot helper changed clip duration: {animation.duration}")
    tracks = list(animation.tracks())
    if len(tracks) != 1 or tracks[0].target_bone.name != "root":
        raise AssertionError("pilot helper did not preserve the root animation track")
    if len(tracks[0].keyframe_list) != 31:
        raise AssertionError(
            f"pilot helper expected 31 baked samples, got {len(tracks[0].keyframe_list)}"
        )

    if list(bpy.context.selected_objects) != original_selection:
        raise AssertionError("pilot helper did not restore Blender selection")
    if bpy.context.view_layer.objects.active is not original_active:
        raise AssertionError("pilot helper did not restore active Blender object")
    if any(obj.name.startswith("__BZ_PilotBake_") for obj in bpy.data.objects):
        raise AssertionError("pilot helper leaked its temporary armature")
    if any(action.name.startswith("__BZPILOT_") for action in bpy.data.actions):
        raise AssertionError("pilot helper leaked its temporary Action")

    print("[PASS] Blender 5.2 pilot-animation bake helper through pure facade")


def main():
    print("BLENDER_VERSION", bpy.app.version_string)
    if bpy.app.version[:2] != (5, 2):
        raise AssertionError(f"expected Blender 5.2, got {bpy.app.version_string}")

    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_blender52_"))
    _test_shape_key_roundtrip(temp_dir)
    _test_batch_export(temp_dir)
    _test_visual_keying_skeleton_bake(temp_dir)
    _test_pilot_ui_baked_replacement(temp_dir)
    print("BLENDER 5.2 PURE OGRE SMOKE PASSED")


if __name__ == "__main__":
    main()
