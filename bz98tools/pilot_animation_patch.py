# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Safe animation-only patching for Battlezone 98 Redux pilot skeletons.

Redux pilots are a special case: their rendered animation is stored in the
OGRE ``.skeleton`` rather than being driven solely by the legacy VDF ANIM
section. This module deliberately keeps the stock skeleton as the authority
for bone handles, names, hierarchy, bind transforms, linked sources, and
serializer version, and copies only selected named animation tracks from a
replacement/exported skeleton.

The intended workflow is:

1. import a stock pilot mesh/skeleton into Blender;
2. author or retarget animation on that imported armature;
3. export a temporary replacement skeleton;
4. patch selected actions into the original stock skeleton with this module.

That makes it impossible for an animation-only export to accidentally replace
stock skinning handles or bind-pose data.
"""

from __future__ import annotations

import io
import math
import os
from typing import Iterable, Optional, Sequence

from .bzrmodelporter.ogreskeleton import Animation, AnimationTrack, KeyFrame, Skeleton
from .bzrmodelporter.ogreskeleton_serializer import SkeletonSerializer
from .bzrmodelporter.ogre_baseserializer import UnsupportedVersionError
from .bzrmodelporter.spacial import Quaternion, Vector3


SERIALIZER_V1_80 = "[Serializer_v1.80]"
SERIALIZER_V1_10 = "[Serializer_v1.10]"
SUPPORTED_PILOT_SKELETON_VERSIONS = (SERIALIZER_V1_80, SERIALIZER_V1_10)


class PilotAnimationPatchError(Exception):
    """Raised when a replacement skeleton is unsafe to patch into stock."""


class VersionPreservingSkeletonSerializer(SkeletonSerializer):
    """Skeleton serializer that round-trips Redux's stock pilot versions.

    The legacy serializer already reads both v1.80 and v1.10 but its write
    entry point only permits v1.80. Redux uses v1.80 for the third-person
    pilot skeletons and v1.10 for the first-person variants, so animation
    patching must preserve either source version.
    """

    def read(self, skeleton=None):
        skeleton = super().read(skeleton)
        skeleton.serializer_version = self.version

        # Older reader code stored this flag under a private/misspelled name.
        # Normalize it here so a read -> patch -> write preserves base info.
        for animation in skeleton.animations():
            if getattr(animation, "_use_base_keyframe", False):
                animation.use_base_keyframe = True
        return skeleton

    def write(self, skeleton, version=None):
        version = version or getattr(skeleton, "serializer_version", None)
        if version is None:
            version = SERIALIZER_V1_80
        if version not in SUPPORTED_PILOT_SKELETON_VERSIONS:
            raise UnsupportedVersionError(
                f"Version {version} does not currently have pilot patch write support"
            )

        self.version = version
        self.write_file_header()

        # v1.10 stock first-person skeletons predate the blend-mode chunk.
        if self.version == SERIALIZER_V1_80:
            self.write_blendmode(skeleton)

        for bone in skeleton.bones():
            self.write_bone(bone)
        for bone in skeleton.bones():
            if bone.parent is not None:
                self.write_bone_parent(bone)

        for animation in skeleton.animations():
            self.write_animation(animation)

        for linked_source in skeleton.sources():
            self.write_skeleton_animation_link(linked_source)

        if self.validate_chunk_sizes and self.are_chunks_remaining():
            raise PilotAnimationPatchError(
                f"Skeleton write left {len(self.chunk_stack)} unclosed chunks"
            )


def read_skeleton_stream(stream, *, validate_chunk_sizes=False) -> Skeleton:
    serializer = VersionPreservingSkeletonSerializer(
        stream, validate_chunk_sizes=validate_chunk_sizes
    )
    return serializer.read()


def write_skeleton_stream(
    stream,
    skeleton: Skeleton,
    *,
    version: Optional[str] = None,
    validate_chunk_sizes=False,
):
    serializer = VersionPreservingSkeletonSerializer(
        stream, validate_chunk_sizes=validate_chunk_sizes
    )
    serializer.write(skeleton, version=version)


def load_skeleton(path: os.PathLike | str, *, validate_chunk_sizes=False) -> Skeleton:
    with open(path, "rb") as stream:
        return read_skeleton_stream(stream, validate_chunk_sizes=validate_chunk_sizes)


def dump_skeleton(
    skeleton: Skeleton, *, version: Optional[str] = None, validate_chunk_sizes=False
) -> bytes:
    stream = io.BytesIO()
    write_skeleton_stream(
        stream,
        skeleton,
        version=version,
        validate_chunk_sizes=validate_chunk_sizes,
    )
    return stream.getvalue()


def save_skeleton(
    path: os.PathLike | str,
    skeleton: Skeleton,
    *,
    version: Optional[str] = None,
    validate_chunk_sizes=False,
):
    payload = dump_skeleton(
        skeleton,
        version=version,
        validate_chunk_sizes=validate_chunk_sizes,
    )
    with open(path, "wb") as stream:
        stream.write(payload)


def _vec3_tuple(value: Optional[Vector3], default=(0.0, 0.0, 0.0)):
    if value is None:
        return default
    return (float(value.x), float(value.y), float(value.z))


def _scale_tuple(value: Optional[Vector3]):
    return _vec3_tuple(value, default=(1.0, 1.0, 1.0))


def _quat_tuple(value: Optional[Quaternion]):
    if value is None:
        return (1.0, 0.0, 0.0, 0.0)
    return (float(value.w), float(value.x), float(value.y), float(value.z))


def _tuple_close(left, right, tolerance):
    return all(
        math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance)
        for a, b in zip(left, right)
    )


def _quat_close(left, right, tolerance):
    """Compare quaternion rotations while accepting q == -q equivalence."""

    if _tuple_close(left, right, tolerance):
        return True
    return _tuple_close(left, tuple(-value for value in right), tolerance)


def _parent_handle(bone):
    return None if bone.parent is None else bone.parent.handle


def validate_skeleton_compatibility(
    stock: Skeleton,
    replacement: Skeleton,
    *,
    bind_tolerance: float = 1.0e-4,
    require_exact_bone_set: bool = True,
):
    """Validate that replacement animations were authored for the stock rig.

    Bone handles are the primary skinning contract. Names and hierarchy must
    also match. Bind transforms are compared with a small tolerance because a
    Blender import/export cycle may introduce harmless floating-point noise.
    Quaternion signs are allowed to flip because q and -q represent the same
    rotation.
    """

    stock_handles = set(stock.bone_map)
    replacement_handles = set(replacement.bone_map)

    if require_exact_bone_set and stock_handles != replacement_handles:
        missing = sorted(stock_handles - replacement_handles)
        extra = sorted(replacement_handles - stock_handles)
        raise PilotAnimationPatchError(
            f"Bone handle set differs from stock (missing={missing}, extra={extra})"
        )

    missing = sorted(stock_handles - replacement_handles)
    if missing:
        raise PilotAnimationPatchError(
            f"Replacement skeleton is missing stock bone handles {missing}"
        )

    for handle in sorted(stock_handles):
        stock_bone = stock.get_bone(handle)
        replacement_bone = replacement.get_bone(handle)

        if stock_bone.name != replacement_bone.name:
            raise PilotAnimationPatchError(
                f"Bone handle {handle} name mismatch: stock={stock_bone.name!r}, "
                f"replacement={replacement_bone.name!r}"
            )

        stock_parent = _parent_handle(stock_bone)
        replacement_parent = _parent_handle(replacement_bone)
        if stock_parent != replacement_parent:
            raise PilotAnimationPatchError(
                f"Bone {stock_bone.name!r} parent mismatch: stock={stock_parent}, "
                f"replacement={replacement_parent}"
            )

        comparisons = (
            (
                "position",
                _vec3_tuple(stock_bone.position),
                _vec3_tuple(replacement_bone.position),
            ),
            (
                "scale",
                _scale_tuple(stock_bone.scale),
                _scale_tuple(replacement_bone.scale),
            ),
        )
        for label, stock_value, replacement_value in comparisons:
            if not _tuple_close(stock_value, replacement_value, bind_tolerance):
                raise PilotAnimationPatchError(
                    f"Bone {stock_bone.name!r} {label} differs from stock beyond "
                    f"tolerance {bind_tolerance}: stock={stock_value}, "
                    f"replacement={replacement_value}"
                )

        stock_orientation = _quat_tuple(stock_bone.orientation)
        replacement_orientation = _quat_tuple(replacement_bone.orientation)
        if not _quat_close(
            stock_orientation, replacement_orientation, bind_tolerance
        ):
            raise PilotAnimationPatchError(
                f"Bone {stock_bone.name!r} orientation differs from stock beyond "
                f"tolerance {bind_tolerance}: stock={stock_orientation}, "
                f"replacement={replacement_orientation}"
            )

    return True


def _copy_vector(value: Optional[Vector3], default=(0.0, 0.0, 0.0)):
    if value is None:
        return Vector3(*default)
    return Vector3(value.x, value.y, value.z)


def _copy_quaternion(value: Optional[Quaternion]):
    if value is None:
        return Quaternion()
    return Quaternion(value.w, value.x, value.y, value.z)


def clone_animation_for_skeleton(
    animation: Animation, target_skeleton: Skeleton
) -> Animation:
    """Clone one animation while rebinding every track to target_skeleton bones."""

    cloned = Animation()
    cloned.name = animation.name
    cloned.duration = animation.duration
    cloned.use_base_keyframe = bool(
        getattr(animation, "use_base_keyframe", False)
    )
    cloned.base_keyframe_animation_name = animation.base_keyframe_animation_name
    cloned.base_keyframe_time = animation.base_keyframe_time

    for source_track in animation.tracks():
        handle = source_track.target_bone.handle
        if handle not in target_skeleton.bone_map:
            raise PilotAnimationPatchError(
                f"Animation {animation.name!r} targets unknown stock bone handle {handle}"
            )

        target_bone = target_skeleton.get_bone(handle)
        if target_bone.name != source_track.target_bone.name:
            raise PilotAnimationPatchError(
                f"Animation {animation.name!r} track handle {handle} targets "
                f"{source_track.target_bone.name!r}, but stock handle {handle} is "
                f"{target_bone.name!r}"
            )

        target_track = AnimationTrack(target_bone)
        for source_keyframe in source_track.keyframe_list:
            target_track.keyframe_list.append(
                KeyFrame(
                    source_keyframe.time,
                    rot=_copy_quaternion(source_keyframe.rotation),
                    trans=_copy_vector(source_keyframe.translation),
                    scale=_copy_vector(
                        source_keyframe.scale, default=(1.0, 1.0, 1.0)
                    ),
                )
            )
        cloned.track_map[handle] = target_track

    return cloned


def patch_animations(
    stock: Skeleton,
    replacement: Skeleton,
    animation_names: Sequence[str] | Iterable[str],
    *,
    validate_bind_pose: bool = True,
    bind_tolerance: float = 1.0e-4,
) -> list[str]:
    """Replace selected named clips in ``stock`` and return patched names.

    All selected animations are validated and cloned first. The stock animation
    map is only mutated after every selected clip is ready, preventing a failed
    multi-clip request from leaving a partially patched in-memory skeleton.
    """

    names = list(dict.fromkeys(animation_names))
    if not names:
        raise PilotAnimationPatchError("No animation names were selected for patching")

    if validate_bind_pose:
        validate_skeleton_compatibility(
            stock, replacement, bind_tolerance=bind_tolerance
        )

    missing = [name for name in names if name not in replacement.animation_map]
    if missing:
        raise PilotAnimationPatchError(
            f"Replacement skeleton does not contain animation(s): {', '.join(missing)}"
        )

    cloned_animations = {
        name: clone_animation_for_skeleton(replacement.animation_map[name], stock)
        for name in names
    }

    for name in names:
        # Assignment to an existing dict key preserves the stock animation order.
        # New custom clips intentionally append after the stock set.
        stock.animation_map[name] = cloned_animations[name]

    return names


def patch_skeleton_files(
    stock_path: os.PathLike | str,
    replacement_path: os.PathLike | str,
    output_path: os.PathLike | str,
    animation_names: Sequence[str] | Iterable[str],
    *,
    validate_bind_pose: bool = True,
    bind_tolerance: float = 1.0e-4,
    validate_chunk_sizes: bool = False,
) -> list[str]:
    """Patch selected clips into a stock skeleton and write a new file.

    The output serializer version always comes from ``stock_path``. The stock
    file is never modified in-place; callers must choose a distinct output.
    """

    stock_abs = os.path.abspath(os.fspath(stock_path))
    replacement_abs = os.path.abspath(os.fspath(replacement_path))
    output_abs = os.path.abspath(os.fspath(output_path))

    if output_abs == stock_abs:
        raise PilotAnimationPatchError(
            "Refusing to overwrite the stock skeleton in-place; choose a separate output path"
        )
    if output_abs == replacement_abs:
        raise PilotAnimationPatchError(
            "Refusing to overwrite the replacement skeleton in-place; choose a separate output path"
        )

    stock = load_skeleton(stock_abs, validate_chunk_sizes=validate_chunk_sizes)
    replacement = load_skeleton(
        replacement_abs, validate_chunk_sizes=validate_chunk_sizes
    )

    patched = patch_animations(
        stock,
        replacement,
        animation_names,
        validate_bind_pose=validate_bind_pose,
        bind_tolerance=bind_tolerance,
    )

    output_dir = os.path.dirname(output_abs)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    save_skeleton(
        output_abs,
        stock,
        version=stock.serializer_version,
        validate_chunk_sizes=validate_chunk_sizes,
    )
    return patched
