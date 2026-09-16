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

    out_indices = submesh.set_vertex(
        nd_vert_indices,
        nd_loop_indices,
        nd_positions,
        nd_normals,
        np.empty(3, dtype=np.float32),
        np.empty(1, dtype=np.float32),
        np.empty(3, dtype=np.float32),
        nd_texcoords,
        np.empty(4, dtype=np.float32),
        np.empty(4, dtype=np.float32),
        3,
        False,
    )

    # Only source vertex 0 moves. This also verifies that append_shapekey maps
    # source-vertex deltas onto exported loop vertices correctly.
    shape_delta = np.asarray(
        [[1.0, 2.0, 3.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        dtype=np.float32,
    )
    submesh.append_shapekey("OraclePose", shape_delta, out_indices)
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

    expected = np.asarray([1.0, 3.0, -2.0], dtype=np.float32)
    for pose in poses:
        if len(pose["vertices"]) != 1:
            raise SystemExit(f"expected one sparse pose vertex, got {pose['vertices']}")
        vertex_index, offset, _ = pose["vertices"][0]
        if vertex_index != 0:
            raise SystemExit(f"unexpected pose vertex index {vertex_index}")
        if not np.allclose(offset, expected, atol=1e-6):
            raise SystemExit(f"unexpected pose offset {offset}; expected {expected.tolist()}")

    print("NATIVE POSE ORACLE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
