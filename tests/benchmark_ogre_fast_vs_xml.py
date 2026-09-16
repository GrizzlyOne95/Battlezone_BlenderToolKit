"""Benchmark the pure Ogre fast path against the legacy XML converter path.

This is an explicit/manual benchmark. It is intentionally not part of the normal
unit-test suite because wall-clock performance on shared CI runners is noisy.

Example (Windows, Blender 5.2 / Python 3.13):

    python tests/benchmark_ogre_fast_vs_xml.py aspilo.mesh aspilo.skeleton.bin --iterations 5

The source pair is staged under the skeleton resource name linked by the mesh.
Import timings include Blender object/armature/action creation. Export timings
include Blender collection plus final mesh/skeleton serialization. Harness setup,
scene cleanup, validation, and source staging are outside timed regions.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import sys
import tempfile
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bpy

from bz98tools.ogrefast import backend
from bz98tools.ogrefast.pure.kenshi_facade import KenshiObjectSerializer
from bz98tools.ogrefast.pure.pose_import_compat import PoseDetectingMeshSerializer
from bz98tools.ogretools import OgreExport, OgreImport


class _Operator:
    def __init__(self):
        self.messages = []

    def report(self, levels, message):
        self.messages.append((set(levels), str(message)))


def _fallback_forbidden(*args, **kwargs):
    raise AssertionError("benchmark fast path unexpectedly reached XML fallback")


def _clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)


def _stage_pair(mesh_path: Path, skeleton_path: Path, temp_dir: Path):
    with mesh_path.open("rb") as stream:
        raw_mesh = PoseDetectingMeshSerializer(stream).read()
    linked_name = str(raw_mesh.skeleton_name or "")
    if not linked_name:
        raise AssertionError("source mesh has no linked skeleton")
    staged_mesh = temp_dir / mesh_path.name
    staged_skeleton = temp_dir / linked_name
    shutil.copy2(mesh_path, staged_mesh)
    shutil.copy2(skeleton_path, staged_skeleton)
    return staged_mesh, staged_skeleton


def _cleanup_xml_sidecars(staged_mesh: Path, staged_skeleton: Path):
    for path in (
        Path(str(staged_mesh) + ".xml"),
        Path(str(staged_skeleton) + ".xml"),
    ):
        if path.exists():
            path.unlink()


def _validate_scene(expected_bones=71, expected_actions=19):
    meshes = sorted(
        (obj for obj in bpy.context.scene.objects if obj.type == "MESH"),
        key=lambda obj: obj.name,
    )
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(meshes) != 2:
        raise AssertionError(f"expected 2 meshes, got {[obj.name for obj in meshes]}")
    if len(armatures) != 1:
        raise AssertionError(f"expected one armature, got {[obj.name for obj in armatures]}")
    if len(armatures[0].data.bones) != expected_bones:
        raise AssertionError(
            f"expected {expected_bones} bones, got {len(armatures[0].data.bones)}"
        )
    if len(bpy.data.actions) < expected_actions:
        raise AssertionError(
            f"expected at least {expected_actions} actions, got {len(bpy.data.actions)}"
        )
    return meshes, armatures[0]


def _fast_import(staged_mesh: Path):
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


def _xml_import(staged_mesh: Path, converter: Path):
    operator = _Operator()
    result = OgreImport.load(
        operator,
        bpy.context,
        str(staged_mesh),
        xml_converter=str(converter),
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
        raise AssertionError(f"XML import failed: {result} {errors}")


def _select_meshes(meshes):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]


def _fast_export(output_mesh: Path, meshes):
    _select_meshes(meshes)
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
    if result != {"FINISHED"} or errors:
        raise AssertionError(f"fast export failed: {result} {errors}")


def _xml_export(output_mesh: Path, meshes, converter: Path):
    _select_meshes(meshes)
    operator = _Operator()
    result = OgreExport.save(
        operator,
        bpy.context,
        str(output_mesh),
        xml_converter=str(converter),
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
    if result != {"FINISHED"} or errors:
        raise AssertionError(f"XML export failed: {result} {errors}")


def _remove_outputs(output_mesh: Path):
    output_skeleton = output_mesh.with_suffix(".skeleton")
    for path in (
        output_mesh,
        output_skeleton,
        Path(str(output_mesh) + ".xml"),
        Path(str(output_skeleton) + ".xml"),
    ):
        if path.exists():
            path.unlink()


def _quiet_timed(callable_):
    gc.collect()
    with open(os.devnull, "w", encoding="utf-8") as sink:
        start = time.perf_counter()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            callable_()
        return time.perf_counter() - start


def _stats(samples):
    ordered = sorted(samples)
    median = statistics.median(ordered)
    mean = statistics.fmean(ordered)
    if len(ordered) == 1:
        p95 = ordered[0]
    else:
        rank = 0.95 * (len(ordered) - 1)
        lo = math.floor(rank)
        hi = math.ceil(rank)
        p95 = ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)
    return {
        "samples_s": [round(value, 6) for value in samples],
        "min_s": round(min(ordered), 6),
        "median_s": round(median, 6),
        "mean_s": round(mean, 6),
        "p95_s": round(p95, 6),
        "max_s": round(max(ordered), 6),
    }


def _prepare_export_scene(staged_mesh: Path):
    _clear_scene()
    _fast_import(staged_mesh)
    meshes, _ = _validate_scene()
    return meshes


def _output_contract(output_mesh: Path):
    serializer = KenshiObjectSerializer()
    serializer.add_resource_location(str(output_mesh.parent))
    mesh = serializer.load_mesh(output_mesh.name)
    skeleton = mesh.get_linked_skeleton()
    return {
        "mesh_bytes": output_mesh.stat().st_size,
        "skeleton_bytes": output_mesh.with_suffix(".skeleton").stat().st_size,
        "submeshes": len(mesh.get_submeshes()),
        "uv_sets": [submesh.geometry.texcoords_size for submesh in mesh.get_submeshes()],
        "bones": len(skeleton.get_bones(has_helper=True)) if skeleton else 0,
        "animations": len(skeleton.get_animations()) if skeleton else 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", type=Path)
    parser.add_argument("skeleton", type=Path)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args(argv)

    if bpy.app.version[:2] != (5, 2):
        raise AssertionError(f"expected Blender 5.2, got {bpy.app.version_string}")
    if args.iterations < 1:
        raise ValueError("iterations must be >= 1")

    converter = REPO_ROOT / "bz98tools" / "ogretools" / "OgreXMLConverter.exe"
    if not converter.is_file():
        raise FileNotFoundError(f"bundled OgreXMLConverter.exe not found: {converter}")
    if sys.platform != "win32":
        raise RuntimeError("legacy OgreXMLConverter benchmark must run on Windows")

    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_ogre_bench_"))
    staged_mesh, staged_skeleton = _stage_pair(
        args.mesh.expanduser().resolve(),
        args.skeleton.expanduser().resolve(),
        temp_dir,
    )
    output_dir = temp_dir / "out"
    output_dir.mkdir()
    fast_output = output_dir / "fast.mesh"
    xml_output = output_dir / "xml.mesh"

    # Warm both import paths once. Warmups are not included in the reported samples.
    _clear_scene()
    _cleanup_xml_sidecars(staged_mesh, staged_skeleton)
    fast_import_warmup = _quiet_timed(lambda: _fast_import(staged_mesh))
    _validate_scene()

    _clear_scene()
    _cleanup_xml_sidecars(staged_mesh, staged_skeleton)
    xml_import_warmup = _quiet_timed(lambda: _xml_import(staged_mesh, converter))
    _validate_scene()
    _cleanup_xml_sidecars(staged_mesh, staged_skeleton)

    import_samples = {"fast": [], "xml": []}
    # Alternate ordering to reduce systematic thermal/scheduling bias.
    for iteration in range(args.iterations):
        order = ("fast", "xml") if iteration % 2 == 0 else ("xml", "fast")
        for path_name in order:
            _clear_scene()
            _cleanup_xml_sidecars(staged_mesh, staged_skeleton)
            if path_name == "fast":
                elapsed = _quiet_timed(lambda: _fast_import(staged_mesh))
            else:
                elapsed = _quiet_timed(lambda: _xml_import(staged_mesh, converter))
            _validate_scene()
            import_samples[path_name].append(elapsed)
            _cleanup_xml_sidecars(staged_mesh, staged_skeleton)

    # Warm both export paths from freshly prepared identical Blender scenes.
    meshes = _prepare_export_scene(staged_mesh)
    _remove_outputs(fast_output)
    fast_export_warmup = _quiet_timed(lambda: _fast_export(fast_output, meshes))
    if not fast_output.is_file() or not fast_output.with_suffix(".skeleton").is_file():
        raise AssertionError("fast export warmup did not produce mesh+skeleton")

    meshes = _prepare_export_scene(staged_mesh)
    _remove_outputs(xml_output)
    xml_export_warmup = _quiet_timed(lambda: _xml_export(xml_output, meshes, converter))
    if not xml_output.is_file() or not xml_output.with_suffix(".skeleton").is_file():
        raise AssertionError("XML export warmup did not produce mesh+skeleton")

    export_samples = {"fast": [], "xml": []}
    for iteration in range(args.iterations):
        order = ("fast", "xml") if iteration % 2 == 0 else ("xml", "fast")
        for path_name in order:
            meshes = _prepare_export_scene(staged_mesh)
            target = fast_output if path_name == "fast" else xml_output
            _remove_outputs(target)
            if path_name == "fast":
                elapsed = _quiet_timed(lambda: _fast_export(target, meshes))
            else:
                elapsed = _quiet_timed(lambda: _xml_export(target, meshes, converter))
            if not target.is_file() or not target.with_suffix(".skeleton").is_file():
                raise AssertionError(f"{path_name} export did not produce mesh+skeleton")
            export_samples[path_name].append(elapsed)

    # Leave one fresh output from each path for structural comparison.
    meshes = _prepare_export_scene(staged_mesh)
    _remove_outputs(fast_output)
    _fast_export(fast_output, meshes)
    meshes = _prepare_export_scene(staged_mesh)
    _remove_outputs(xml_output)
    _xml_export(xml_output, meshes, converter)

    fast_import = _stats(import_samples["fast"])
    xml_import = _stats(import_samples["xml"])
    fast_export = _stats(export_samples["fast"])
    xml_export = _stats(export_samples["xml"])

    result = {
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "python": sys.version.split()[0],
            "blender": bpy.app.version_string,
            "iterations": args.iterations,
        },
        "warmup_s": {
            "import_fast": round(fast_import_warmup, 6),
            "import_xml": round(xml_import_warmup, 6),
            "export_fast": round(fast_export_warmup, 6),
            "export_xml": round(xml_export_warmup, 6),
        },
        "import": {
            "fast": fast_import,
            "xml": xml_import,
            "speedup_x": round(xml_import["median_s"] / fast_import["median_s"], 3),
            "time_reduction_pct": round(
                (1.0 - fast_import["median_s"] / xml_import["median_s"]) * 100.0,
                2,
            ),
        },
        "export": {
            "fast": fast_export,
            "xml": xml_export,
            "speedup_x": round(xml_export["median_s"] / fast_export["median_s"], 3),
            "time_reduction_pct": round(
                (1.0 - fast_export["median_s"] / xml_export["median_s"]) * 100.0,
                2,
            ),
        },
        "outputs": {
            "fast": _output_contract(fast_output),
            "xml": _output_contract(xml_output),
        },
    }

    text = json.dumps(result, indent=2, sort_keys=True)
    print("OGRE_FAST_VS_XML_BENCHMARK")
    print(text)
    if args.output_json is not None:
        output_json = args.output_json.expanduser().resolve()
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(text + "\n", encoding="utf-8")
        print("BENCHMARK_JSON", output_json)


if __name__ == "__main__":
    main()
