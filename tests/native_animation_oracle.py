"""Recover the bundled native animation transform contract on Windows/CP311.

The CPython extension is treated only as an oracle: it writes controlled
single-bone animations, then the repository's pure skeleton parser reports the
actual Ogre keyframes. Both public animation input surfaces are probed:
``append_animation_track`` (ordinary F-curve collection) and
``set_animation_tracks`` (visual-keying / baked matrices).
"""

from __future__ import annotations

import math
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
    raise SystemExit("native animation oracle is Windows-only")

_dll_handle = os.add_dll_directory(str(NATIVE_ROOT))

import kenshi_blender_tool as native  # noqa: E402
from bz98tools.bzrmodelporter.ogreskeleton_serializer import SkeletonSerializer  # noqa: E402


def _matrix(rows):
    return native.Matrix3(rows)


def _new_skeleton(serializer, path):
    skeleton = serializer.create_skeleton(path.name)
    skeleton.set_bones(
        [
            native.BoneData(
                0,
                "root",
                native.Vector3(0.0, 0.0, 0.0),
                native.OgreQuaternion(1.0, 0.0, 0.0, 0.0),
                native.Vector3(1.0, 1.0, 1.0),
                "",
                [],
            )
        ]
    )
    return skeleton


def _write_case(path: Path, *, bone_matrix, locations, rotations, scales, use_scale):
    serializer = native.KenshiObjectSerializer(str(path.with_suffix(".log")))
    skeleton = _new_skeleton(serializer, path)

    animation = native.AnimationData()
    animation.name = path.stem
    animation.length = 1.0
    animation.append_animation_track(
        bone_name="root",
        bone_matrix=bone_matrix,
        nd_times=np.asarray([0.0, 1.0], dtype=np.float32),
        nd_locations=np.asarray(locations, dtype=np.float32),
        nd_rotations=np.asarray(rotations, dtype=np.float32),
        nd_scales=np.asarray(scales, dtype=np.float32),
        use_scale=use_scale,
    )
    skeleton.add_animation(animation)
    serializer.save_skeleton(skeleton, str(path), native.SkeletonVersion.V_Latest)


def _pose_matrix(translation=(0.0, 0.0, 0.0), angle_x=0.0):
    c = math.cos(angle_x)
    s = math.sin(angle_x)
    tx, ty, tz = translation
    return [
        [1.0, 0.0, 0.0, float(tx)],
        [0.0, c, -s, float(ty)],
        [0.0, s, c, float(tz)],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _write_baked_case(path: Path, *, bone_matrix):
    serializer = native.KenshiObjectSerializer(str(path.with_suffix(".log")))
    skeleton = _new_skeleton(serializer, path)

    animation = native.AnimationData()
    animation.name = path.stem
    animation.length = 1.0
    animation.set_animation_tracks(
        bone_matrix_map={"root": bone_matrix},
        pose_matrix_map={
            "root": [
                _pose_matrix(),
                _pose_matrix((1.0, 2.0, 3.0), math.pi / 4.0),
            ]
        },
        time_array=[0.0, 1.0],
        use_scale=False,
    )
    skeleton.add_animation(animation)
    serializer.save_skeleton(skeleton, str(path), native.SkeletonVersion.V_Latest)


def _read_case(path: Path):
    with path.open("rb") as stream:
        skeleton = SkeletonSerializer(stream).read()
    animation = next(iter(skeleton.animations()))
    track = next(iter(animation.tracks()))
    return [
        {
            "time": float(k.time),
            "translation": [
                float(k.translation.x),
                float(k.translation.y),
                float(k.translation.z),
            ],
            "rotation": [
                float(k.rotation.w),
                float(k.rotation.x),
                float(k.rotation.y),
                float(k.rotation.z),
            ],
            "scale": [float(k.scale.x), float(k.scale.y), float(k.scale.z)],
        }
        for k in track.keyframe_list
    ]


def main() -> int:
    temp_dir = Path(tempfile.mkdtemp(prefix="bz98_anim_oracle_"))
    identity_basis = _matrix(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    )
    rotated_basis = _matrix(
        [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    )

    # Case A: identity bone basis isolates the Blender->Ogre channel conversion.
    identity_path = temp_dir / "identity.skeleton"
    _write_case(
        identity_path,
        bone_matrix=identity_basis,
        locations=[[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]],
        rotations=[
            [1.0, 0.9238795],
            [0.0, 0.3826834],
            [0.0, 0.0],
            [0.0, 0.0],
        ],
        scales=[[1.0, 2.0], [1.0, 3.0], [1.0, 4.0]],
        use_scale=True,
    )
    identity = _read_case(identity_path)
    print("IDENTITY_CASE", identity)

    # Case B: non-trivial basis reveals how bone_matrix participates.
    basis_path = temp_dir / "basis.skeleton"
    _write_case(
        basis_path,
        bone_matrix=rotated_basis,
        locations=[[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]],
        rotations=[
            [1.0, 0.9238795],
            [0.0, 0.3826834],
            [0.0, 0.0],
            [0.0, 0.0],
        ],
        scales=[[1.0, 2.0], [1.0, 3.0], [1.0, 4.0]],
        use_scale=True,
    )
    basis = _read_case(basis_path)
    print("BASIS_CASE", basis)

    # Cases C/D: visual-keying path receives complete local pose matrices.
    baked_identity_path = temp_dir / "baked_identity.skeleton"
    _write_baked_case(baked_identity_path, bone_matrix=identity_basis)
    baked_identity = _read_case(baked_identity_path)
    print("BAKED_IDENTITY_CASE", baked_identity)

    baked_basis_path = temp_dir / "baked_basis.skeleton"
    _write_baked_case(baked_basis_path, bone_matrix=rotated_basis)
    baked_basis = _read_case(baked_basis_path)
    print("BAKED_BASIS_CASE", baked_basis)

    for label, case in (
        ("identity", identity),
        ("basis", basis),
        ("baked_identity", baked_identity),
        ("baked_basis", baked_basis),
    ):
        if len(case) != 2:
            raise SystemExit(f"{label} animation oracle did not produce two keyframes")
        if case[0]["time"] != 0.0 or case[1]["time"] != 1.0:
            raise SystemExit(f"{label} animation oracle changed input keyframe times")

    print("NATIVE ANIMATION ORACLE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
