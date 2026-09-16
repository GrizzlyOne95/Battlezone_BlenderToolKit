"""Windows/Python-3.11 oracle check for the pure Ogre serializer.

This intentionally uses the bundled legacy native backend only as a reader.
If it can load a mesh written by the pure backend, the test exercises OGRE's
real binary parser rather than merely our own chunk inspection.
"""

from __future__ import annotations

import os
from pathlib import Path
import struct
import sys
import tempfile

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "bz98tools" / "ogrefast" / "vendor"
NATIVE_ROOT = VENDOR_ROOT / "kenshi_blender_tool"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(VENDOR_ROOT))

# Running outside Blender must not execute bz98tools/__init__.py, which imports
# bpy at module scope. Reuse the same package shell as the rest of the bpy-free
# test suite so bz98tools.ogrefast.pure can be imported normally.
import _bootstrap  # noqa: E402

_bootstrap.ensure_package()

if os.name != "nt":
    raise SystemExit("native Ogre oracle is Windows-only")

_dll_handle = os.add_dll_directory(str(NATIVE_ROOT))

import kenshi_blender_tool as native  # noqa: E402

from bz98tools.ogrefast.pure.model import (  # noqa: E402
    GeometryData,
    MeshBounds,
    MeshData,
    SubMeshData,
    VertexBuffer,
    VertexElement,
)
from bz98tools.ogrefast.pure.serializer import OgreMeshSerializer  # noqa: E402


def check(name: str, condition: bool, detail="") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")
    if not condition:
        raise SystemExit(1)


def build_mesh() -> MeshData:
    vertices = [
        (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0),
        (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0),
        (0.0, 0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0),
    ]
    raw = b"".join(struct.pack("<8f", *vertex) for vertex in vertices)
    geometry = GeometryData(
        vertex_count=3,
        declaration=[
            VertexElement("POSITION", 0, 0, "FLOAT3"),
            VertexElement("NORMAL", 0, 12, "FLOAT3"),
            VertexElement("TEXCOORD", 0, 24, "FLOAT2"),
        ],
        buffers=[VertexBuffer(0, 32, raw)],
    )
    return MeshData(
        submeshes=[
            SubMeshData(
                material_name="bz98/oracle",
                indices=[0, 1, 2],
                name="oracle",
                geometry=geometry,
            )
        ],
        bounds=MeshBounds((0.0, 0.0, -1.0), (1.0, 0.0, 0.0), 1.0),
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bz98_ogre_oracle_") as tmp:
        mesh_path = Path(tmp) / "oracle.mesh"
        OgreMeshSerializer().dump(build_mesh(), mesh_path)
        check("pure mesh written", mesh_path.is_file(), f"bytes={mesh_path.stat().st_size}")

        serializer = native.KenshiObjectSerializer(str(Path(tmp) / "native_oracle.log"))
        loaded = serializer.load_mesh(str(mesh_path))
        check("native OGRE loader accepts pure mesh", loaded is not None)

        submeshes = loaded.get_submeshes()
        check("one submesh loaded", len(submeshes) == 1, f"count={len(submeshes)}")
        submesh = submeshes[0]
        check("triangle face count preserved", int(submesh.face_count) == 1, submesh.face_count)
        check("triangle indices preserved", list(submesh.faces) == [0, 1, 2], submesh.faces)
        check("vertex count preserved", int(submesh.geometry.vertex_count) == 3, submesh.geometry.vertex_count)
        check("normals recognized", bool(submesh.geometry.has_normals))
        check("UVs recognized", bool(submesh.geometry.has_texture_coord))

        positions = np.asarray(submesh.get_positions(), dtype=np.float32).reshape(-1, 3)
        check("three positions decoded", positions.shape == (3, 3), positions.shape)
        check("position data finite", bool(np.isfinite(positions).all()))

        print(f"[INFO] native-decoded positions: {positions.tolist()}")
        print("NATIVE OGRE ORACLE PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
