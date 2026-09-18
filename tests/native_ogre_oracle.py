"""Windows/Python-3.11 oracle check for the pure Ogre serializer.

The bundled CP311 backend is used as an independent OGRE writer/reader oracle.
The same synthetic Blender-side triangle is exported through both the legacy
native path and the new pure-Python compatibility path, then both files are
loaded back through the legacy native reader and compared semantically.
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

import _bootstrap  # noqa: E402

_bootstrap.ensure_package()

if os.name != "nt":
    raise SystemExit("native Ogre oracle is Windows-only")

_dll_handle = os.add_dll_directory(str(NATIVE_ROOT))

import kenshi_blender_tool as native  # noqa: E402
from bz98tools.ogrefast.pure import kenshi_compat as pure  # noqa: E402


def check(name: str, condition: bool, detail="") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")
    if not condition:
        raise SystemExit(1)


def arrays():
    return (
        np.asarray([0, 1, 2], dtype=np.int32),
        np.asarray([0, 1, 2], dtype=np.int32),
        np.asarray(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            dtype=np.float32,
        ),
        np.asarray(
            [[0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        ),
        np.empty(3, dtype=np.float32),
        np.empty(1, dtype=np.float32),
        np.empty(3, dtype=np.float32),
        np.asarray([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        np.empty(4, dtype=np.float32),
        np.empty(4, dtype=np.float32),
    )


def make_submesh(module):
    submesh = module.SubMeshData()
    submesh.index = 0
    submesh.submesh_name = "oracle"
    submesh.material = "bz98/oracle"
    submesh.operation_type = module.OperationType.triangle_list
    out_indices = submesh.set_vertex(*arrays(), 3, True)
    submesh.set_bone_assignments([], out_indices)
    return submesh


def save_native(serializer, path: Path) -> None:
    mesh = serializer.create_mesh(path.name)
    mesh.set_submeshes([make_submesh(native)])
    serializer.save_mesh(mesh, str(path), native.MeshVersion.V_1_10)


def save_pure(path: Path) -> None:
    serializer = pure.KenshiObjectSerializer()
    mesh = serializer.create_mesh(path.name)
    mesh.set_submeshes([make_submesh(pure)])
    serializer.save_mesh(mesh, path, pure.MeshVersion.V_1_10)


def load_summary(serializer, name: str):
    mesh = serializer.load_mesh(name)
    submeshes = mesh.get_submeshes()
    check(f"{name}: one submesh loaded", len(submeshes) == 1, len(submeshes))
    submesh = submeshes[0]
    positions = np.asarray(submesh.get_positions(), dtype=np.float32).reshape(-1, 3)
    normals = np.asarray(submesh.get_normals(), dtype=np.float32).reshape(-1, 3)
    texcoords = np.asarray(submesh.get_texcoords(), dtype=np.float32)
    if texcoords.ndim == 3:
        texcoords = texcoords[0]
    texcoords = texcoords.reshape(-1, 2)
    return {
        "faces": list(submesh.faces),
        "face_count": int(submesh.face_count),
        "vertex_count": int(submesh.geometry.vertex_count),
        "has_normals": bool(submesh.geometry.has_normals),
        "has_uvs": bool(submesh.geometry.has_texture_coord),
        "positions": positions,
        "normals": normals,
        "texcoords": texcoords,
    }


def main() -> int:
    tmp_path = Path(tempfile.mkdtemp(prefix="bz98_ogre_oracle_"))
    native_path = tmp_path / "native.mesh"
    pure_path = tmp_path / "pure.mesh"

    native_serializer = native.KenshiObjectSerializer(str(tmp_path / "native_oracle.log"))
    native_serializer.add_resource_location(str(tmp_path))

    save_native(native_serializer, native_path)
    save_pure(pure_path)
    check("native reference mesh written", native_path.is_file(), native_path.stat().st_size)
    check("pure mesh written", pure_path.is_file(), pure_path.stat().st_size)

    reference = load_summary(native_serializer, native_path.name)
    candidate = load_summary(native_serializer, pure_path.name)

    for key in ("faces", "face_count", "vertex_count", "has_normals", "has_uvs"):
        check(f"native/pure {key} match", candidate[key] == reference[key], candidate[key])

    for key in ("positions", "normals", "texcoords"):
        same_shape = candidate[key].shape == reference[key].shape
        same_values = same_shape and bool(np.allclose(candidate[key], reference[key], atol=1e-6))
        check(
            f"native/pure {key} match",
            same_values,
            f"native={reference[key].tolist()} pure={candidate[key].tolist()}",
        )

    print(f"[INFO] native bytes={native_path.stat().st_size} pure bytes={pure_path.stat().st_size}")
    print("NATIVE OGRE ORACLE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
