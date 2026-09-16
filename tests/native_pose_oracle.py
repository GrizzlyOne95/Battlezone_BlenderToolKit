"""Recover the bundled native pose/shape-key binary contract on Windows/CP311.

The old CPython extension writes two controlled per-submesh poses. A small
reader subclass records the raw Ogre pose target IDs and vertex offsets so the
pure replacement can match the native implementation exactly.
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


def _make_submesh(index: int, name: str, x_offset: float):
    submesh = native.SubMeshData()
    submesh.index = index
    submesh.submesh_name = name
    submesh.material = name + "Mat"

    nd_vert_indices = np.asarray([0, 1, 2], dtype=np.int32)
    nd_loop_indices = np.asarray([0, 1, 2], dtype=np.int32)
    nd_positions = np.asarray(
        [
            [x_offset + 0.0, 0.0, 0.0],
            [x_offset + 1.0, 0.0, 0.0],
            [x_offset + 0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    nd_normals = np.asarray([[0.0, 0.0, 1.0]] * 3, dtype=np.float32)
    nd_texcoords = np.asarray([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)

    # Match the production fast exporter: mesh_optimize=True.
    out_indices = submesh.set_vertex(
        nd_vert_indices=nd_vert_indices,
        nd_loop_indices=nd_loop_indices,
        nd_positions=nd_positions,
        nd_normals=nd_normals,
        nd_tangents=np.empty(3, dtype=np.float32),
        nd_bitangent_signs=np.empty(1, dtype=np.float32),
        nd_bitangents=np.empty(3, dtype=np.float32),
        nd_texcoords=nd_texcoords,
        nd_colors=np.empty(4, dtype=np.float32),
        nd_alphas=np.empty(4, dtype=np.float32),
        tangent_dimensions=3,
        optimize=True,
    )
    print(f"OUT_INDICES_{index}", np.asarray(out_indices).tolist())

    shape_delta = np.asarray(
        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]],
        dtype=np.float32,
    )
    submesh.append_shapekey("OraclePose", shape_delta, out_indices)
    try:
        print(f"IN_MEMORY_POSES_{index}", submesh.get_shapekeys())
    except Exception as exc:
        print(f"IN_MEMORY_POSES_{index}_ERROR", repr(exc))
    return submesh


def main() -> int:
    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_pose_oracle_"))
    mesh_path = temp_dir / "pose.mesh"

    serializer = native.KenshiObjectSerializer(str(temp_dir / "pose.log"))
    mesh = serializer.create_mesh(mesh_path.name)
    mesh.set_submeshes(
        [
            _make_submesh(0, "first", 0.0),
            _make_submesh(1, "second", 10.0),
        ]
    )
    serializer.save_mesh(mesh, str(mesh_path), native.MeshVersion.V_1_10)

    raw = mesh_path.read_bytes()
    print("POSE_VERTEX_MARKERS", raw.count(b"\x11\xc1"), "SIZE", len(raw))

    with mesh_path.open("rb") as stream:
        parsed = PoseRecordingSerializer(stream).read()

    poses = getattr(parsed, "oracle_poses", [])
    print("POSE_CASE", poses)
    if len(poses) != 2:
        raise SystemExit(f"expected two poses, got {len(poses)}")
    if [pose["target"] for pose in poses] != [1, 2]:
        raise SystemExit(f"unexpected pose targets: {[pose['target'] for pose in poses]}")
    if any(pose["includes_normals"] for pose in poses):
        raise SystemExit("native append_shapekey unexpectedly wrote pose normals")
    if any(len(pose["vertices"]) != 3 for pose in poses):
        raise SystemExit(f"expected three sparse pose vertices per target, got {poses}")

    expected = np.asarray(
        [[1.0, 3.0, -2.0], [4.0, 6.0, -5.0], [7.0, 9.0, -8.0]],
        dtype=np.float32,
    )
    for pose in poses:
        got_indices = [vertex[0] for vertex in pose["vertices"]]
        got_offsets = np.asarray([vertex[1] for vertex in pose["vertices"]], dtype=np.float32)
        if got_indices != [0, 1, 2]:
            raise SystemExit(f"unexpected pose vertex indices {got_indices}")
        if not np.allclose(got_offsets, expected, atol=1e-6):
            raise SystemExit(
                f"unexpected pose offsets {got_offsets.tolist()}; expected {expected.tolist()}"
            )

    print("NATIVE POSE ORACLE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
