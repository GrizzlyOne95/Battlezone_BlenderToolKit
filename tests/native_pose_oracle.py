"""Recover and cross-check the native pose/shape-key wire contract on CP311.

The CPython extension has a known quirk in the index array returned by
SubMeshData.set_vertex for this synthetic fixture (it reports [2, 2, 2]).
That return-array bug is not part of the OGRE wire format and is deliberately
not a compatibility target. Every source vertex therefore gets the same
non-zero shape delta when probing the native writer. The oracle verifies the
wire contract, then confirms the old native reader accepts a pose stream made
by the pure writer.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "bz98tools" / "ogrefast" / "vendor"
NATIVE_ROOT = VENDOR_ROOT / "kenshi_blender_tool"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(VENDOR_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import _bootstrap  # noqa: E402

_bootstrap.ensure_package()

if os.name != "nt":
    raise SystemExit("native pose oracle is Windows-only")

_dll_handle = os.add_dll_directory(str(NATIVE_ROOT))

import kenshi_blender_tool as native  # noqa: E402
from bz98tools.bzrmodelporter.ogremesh_serializer import (  # noqa: E402
    MeshChunkID,
    MeshSerializer,
)
from bz98tools.ogrefast.pure import pose_compat as pure  # noqa: E402


class PoseRecordingSerializer(MeshSerializer):
    def read_poses(self, mesh):
        mesh.oracle_poses = []
        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id != MeshChunkID.POSE:
                self.rollback_chunk_header()
                break

            name = self.read_string_nlt()
            target = self.read_ushort()
            includes_normals = self.read_bool()
            vertices = []
            while True:
                try:
                    child_id = self.read_chunk_header()
                except EOFError:
                    break
                if child_id != MeshChunkID.POSE_VERTEX:
                    self.rollback_chunk_header()
                    break
                vertex_index = self.read_uint()
                offset = [self.read_float(), self.read_float(), self.read_float()]
                normal = None
                if includes_normals:
                    normal = [self.read_float(), self.read_float(), self.read_float()]
                vertices.append((vertex_index, offset, normal))
                self.pop_chunk(MeshChunkID.POSE_VERTEX)

            mesh.oracle_poses.append(
                {
                    "name": name,
                    "target": target,
                    "includes_normals": includes_normals,
                    "vertices": vertices,
                }
            )
            self.pop_chunk(MeshChunkID.POSE)

        self.pop_chunk(MeshChunkID.POSES)


def _arrays(x_offset: float):
    return dict(
        nd_vert_indices=np.asarray([0, 1, 2], dtype=np.int32),
        nd_loop_indices=np.asarray([0, 1, 2], dtype=np.int32),
        nd_positions=np.asarray(
            [
                [x_offset + 0.0, 0.0, 0.0],
                [x_offset + 1.0, 0.0, 0.0],
                [x_offset + 0.0, 1.0, 0.0],
            ],
            dtype=np.float32,
        ),
        nd_normals=np.asarray([[0.0, 0.0, 1.0]] * 3, dtype=np.float32),
        nd_tangents=np.empty(3, dtype=np.float32),
        nd_bitangent_signs=np.empty(1, dtype=np.float32),
        nd_bitangents=np.empty(3, dtype=np.float32),
        nd_texcoords=np.asarray(
            [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32
        ),
        nd_colors=np.empty(4, dtype=np.float32),
        nd_alphas=np.empty(4, dtype=np.float32),
        tangent_dimensions=3,
        optimize=True,
    )


def _shape_delta():
    # Identical source deltas intentionally neutralize the native return-map
    # quirk so the emitted OGRE bytes reveal only the coordinate contract.
    return np.asarray(
        [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]],
        dtype=np.float32,
    )


def _make_native_submesh(index: int, name: str, x_offset: float):
    submesh = native.SubMeshData()
    submesh.index = index
    submesh.submesh_name = name
    submesh.material = name + "Mat"
    out_indices = submesh.set_vertex(**_arrays(x_offset))
    print(f"NATIVE_RETURN_MAPPING_{index}", np.asarray(out_indices).tolist())
    submesh.append_shapekey("OraclePose", _shape_delta(), out_indices)
    return submesh


def _make_pure_submesh(index: int, name: str, x_offset: float):
    submesh = pure.SubMeshData()
    submesh.index = index
    submesh.submesh_name = name
    submesh.material = name + "Mat"
    out_indices = submesh.set_vertex(**_arrays(x_offset))
    submesh.set_bone_assignments([], out_indices)
    submesh.append_shapekey("OraclePose", _shape_delta(), out_indices)
    return submesh


def _assert_pose_wire(path: Path):
    with path.open("rb") as stream:
        parsed = PoseRecordingSerializer(stream).read()

    poses = getattr(parsed, "oracle_poses", [])
    print("POSE_CASE", path.name, poses)
    if len(poses) != 2:
        raise SystemExit(f"expected two poses, got {len(poses)}")
    if [pose["target"] for pose in poses] != [1, 2]:
        raise SystemExit(f"unexpected pose targets: {[pose['target'] for pose in poses]}")
    if any(pose["includes_normals"] for pose in poses):
        raise SystemExit("pose stream unexpectedly contains normal offsets")
    if any(len(pose["vertices"]) != 3 for pose in poses):
        raise SystemExit(f"expected three pose vertices per target, got {poses}")

    expected_offset = np.asarray([1.0, 3.0, -2.0], dtype=np.float32)
    for pose in poses:
        got_indices = [vertex[0] for vertex in pose["vertices"]]
        if got_indices != [0, 1, 2]:
            raise SystemExit(f"unexpected pose vertex indices {got_indices}")
        for _, offset, _ in pose["vertices"]:
            if not np.allclose(offset, expected_offset, atol=1e-6):
                raise SystemExit(
                    f"unexpected pose offset {offset}; expected {expected_offset.tolist()}"
                )


def main() -> int:
    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_pose_oracle_"))
    native_path = temp_dir / "native_pose.mesh"
    pure_path = temp_dir / "pure_pose.mesh"

    native_serializer = native.KenshiObjectSerializer(str(temp_dir / "pose.log"))
    native_serializer.add_resource_location(str(temp_dir))
    native_mesh = native_serializer.create_mesh(native_path.name)
    native_mesh.set_submeshes(
        [
            _make_native_submesh(0, "first", 0.0),
            _make_native_submesh(1, "second", 10.0),
        ]
    )
    native_serializer.save_mesh(
        native_mesh, str(native_path), native.MeshVersion.V_1_10
    )
    _assert_pose_wire(native_path)

    pure_serializer = pure.KenshiObjectSerializer()
    pure_mesh = pure_serializer.create_mesh(pure_path.name)
    pure_mesh.set_submeshes(
        [
            _make_pure_submesh(0, "first", 0.0),
            _make_pure_submesh(1, "second", 10.0),
        ]
    )
    pure_serializer.save_mesh(pure_mesh, pure_path, pure.MeshVersion.V_1_10)
    _assert_pose_wire(pure_path)

    # Cross-implementation compatibility: the legacy native backend must be
    # able to load the pure writer's stream and surface both shape keys.
    loaded = native_serializer.load_mesh(pure_path.name)
    loaded_submeshes = loaded.get_submeshes()
    if len(loaded_submeshes) != 2:
        raise SystemExit(
            f"native reader loaded {len(loaded_submeshes)} pure submeshes, expected 2"
        )
    for index, submesh in enumerate(loaded_submeshes):
        shapes = submesh.get_shapekeys()
        print(f"NATIVE_READ_PURE_POSES_{index}", shapes)
        if len(shapes) != 1 or shapes[0][0] != "OraclePose":
            raise SystemExit(
                f"native reader did not surface OraclePose on submesh {index}: {shapes}"
            )

    print("NATIVE/PURE POSE ORACLE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
