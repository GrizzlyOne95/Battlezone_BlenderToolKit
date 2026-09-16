"""Real Blender 5.2 smoke coverage for the pure Ogre fast path.

Run with the official ``bpy`` Python module. This intentionally exercises the
actual Blender collector/importer glue rather than only the bpy-free binary
models: shape-key pose export/import and Batch Selected export are covered.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bpy

from bz98tools.ogrefast import backend


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


def main():
    print("BLENDER_VERSION", bpy.app.version_string)
    if bpy.app.version[:2] != (5, 2):
        raise AssertionError(f"expected Blender 5.2, got {bpy.app.version_string}")

    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_blender52_"))
    _test_shape_key_roundtrip(temp_dir)
    _test_batch_export(temp_dir)
    print("BLENDER 5.2 PURE OGRE SMOKE PASSED")


if __name__ == "__main__":
    main()
