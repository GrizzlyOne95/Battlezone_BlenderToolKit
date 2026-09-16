from __future__ import annotations

"""Pure-Python compatibility model for Ogre skeleton animation tracks.

The coordinate contract is pinned against the bundled CPython-3.11 native
backend in ``tests/native_animation_oracle.py``. For the normal no-scale path
used by the addon, both ordinary F-curve collection and visual-keying/baked
matrix collection reduce to the same contract:

* export translation: ``D @ bone_matrix @ blender_location``
  where ``D = diag(-1, 1, 1)``;
* export quaternion: ``(w, x, y, z) -> (w, y, z, x)``;
* import uses the inverse mappings. The importer supplies the transpose of
  the export bone matrix, so the translation transform inverts exactly for
  the orthonormal bone bases produced by the Blender collector.
"""

from dataclasses import dataclass

import numpy as np

from .kenshi_compat import SkeletonData


class UnsupportedAnimationScale(RuntimeError):
    pass


@dataclass
class _OgreAnimationTrack:
    bone_name: str
    times: np.ndarray
    translations: np.ndarray
    rotations: np.ndarray
    scales: np.ndarray
    has_scale: bool = False


class BlenderAnimationTrack:
    def __init__(
        self,
        name: str,
        nd_locations: np.ndarray,
        nd_rotations: np.ndarray,
        nd_scales: np.ndarray,
        has_scale: bool,
    ):
        self._name = str(name)
        self._nd_locations = np.asarray(nd_locations, dtype=np.float32)
        self._nd_rotations = np.asarray(nd_rotations, dtype=np.float32)
        self._nd_scales = np.asarray(nd_scales, dtype=np.float32)
        self._has_scale = bool(has_scale)

    @property
    def name(self):
        return self._name

    @property
    def nd_locations(self):
        return self._nd_locations

    @property
    def nd_rotations(self):
        return self._nd_rotations

    @property
    def nd_scales(self):
        return self._nd_scales

    @property
    def has_scale(self):
        return self._has_scale


class AnimationData:
    def __init__(self):
        self.name = ""
        self.length = 0.0
        self._tracks: list[_OgreAnimationTrack] = []

    def append_animation_track(
        self,
        bone_name,
        bone_matrix,
        nd_times,
        nd_locations,
        nd_rotations,
        nd_scales,
        use_scale=False,
    ):
        if use_scale:
            raise UnsupportedAnimationScale(
                "pure Python animation export does not yet support scale keyframes"
            )

        times = np.asarray(nd_times, dtype=np.float32).reshape(-1)
        locations = _channel_array(nd_locations, 3, len(times), "locations")
        rotations = _channel_array(nd_rotations, 4, len(times), "rotations")

        matrix = _matrix3_array(bone_matrix)
        translated = matrix @ locations
        translated = translated.astype(np.float32, copy=False)
        translated[0] *= -1.0

        # Native backend / legacy XML contract: quaternion vector part cycles
        # x -> z, y -> x, z -> y.
        ogre_rotations = np.empty((4, len(times)), dtype=np.float32)
        ogre_rotations[0] = rotations[0]
        ogre_rotations[1] = rotations[2]
        ogre_rotations[2] = rotations[3]
        ogre_rotations[3] = rotations[1]

        self._tracks.append(
            _OgreAnimationTrack(
                str(bone_name),
                times.copy(),
                translated.T.copy(),
                ogre_rotations.T.copy(),
                np.ones((len(times), 3), dtype=np.float32),
                False,
            )
        )

    def append_ogre_track(
        self,
        bone_name,
        times,
        translations,
        rotations,
        scales=None,
        has_scale=False,
    ):
        times = np.asarray(times, dtype=np.float32).reshape(-1)
        translations = np.asarray(translations, dtype=np.float32).reshape(-1, 3)
        rotations = np.asarray(rotations, dtype=np.float32).reshape(-1, 4)
        if len(translations) != len(times) or len(rotations) != len(times):
            raise ValueError("animation track arrays have inconsistent keyframe counts")
        if scales is None:
            scales = np.ones((len(times), 3), dtype=np.float32)
        scales = np.asarray(scales, dtype=np.float32).reshape(-1, 3)
        if len(scales) != len(times):
            raise ValueError("animation scale array has inconsistent keyframe count")
        self._tracks.append(
            _OgreAnimationTrack(
                str(bone_name),
                times.copy(),
                translations.copy(),
                rotations.copy(),
                scales.copy(),
                bool(has_scale),
            )
        )

    def get_animations(self, bone_matrix_map, fps, round_frame):
        result = []
        fps = float(fps)
        for track in self._tracks:
            if track.has_scale:
                raise UnsupportedAnimationScale(
                    "pure Python animation import does not yet support scale keyframes"
                )
            if track.bone_name not in bone_matrix_map:
                continue

            inverse_basis = _matrix3_array(bone_matrix_map[track.bone_name])
            ogre_translation = track.translations.T.copy()
            ogre_translation[0] *= -1.0
            blender_translation = inverse_basis @ ogre_translation

            ogre_rotation = track.rotations.T
            blender_rotation = np.empty_like(ogre_rotation)
            blender_rotation[0] = ogre_rotation[0]
            blender_rotation[1] = ogre_rotation[3]
            blender_rotation[2] = ogre_rotation[1]
            blender_rotation[3] = ogre_rotation[2]

            frames = track.times.astype(np.float32) * fps
            if round_frame:
                frames = np.rint(frames).astype(np.float32)

            locations = _as_fcurve_pairs(blender_translation, frames)
            rotations = _as_fcurve_pairs(blender_rotation, frames)
            scales = _as_fcurve_pairs(
                np.ones((3, len(frames)), dtype=np.float32), frames
            )
            result.append(
                BlenderAnimationTrack(
                    track.bone_name, locations, rotations, scales, False
                )
            )
        return result

    def set_animation_tracks(
        self,
        bone_matrix_map,
        pose_matrix_map,
        time_array,
        use_scale=False,
    ):
        """Convert baked local pose matrices through the native animation contract.

        ``ogre_exporter.collect_bake_tracks`` supplies one Blender local 4x4
        pose matrix per sampled frame. The old extension decomposes those
        matrices, then applies the same basis/axis conversion used by
        ``append_animation_track``. This behavior is pinned by the native
        baked-matrix cases in ``tests/native_animation_oracle.py``.
        """

        if use_scale:
            raise UnsupportedAnimationScale(
                "pure Python baked animation export does not yet support scale keyframes"
            )

        times = np.asarray(time_array, dtype=np.float32).reshape(-1)
        for bone_name, matrix_frames in pose_matrix_map.items():
            if bone_name not in bone_matrix_map:
                raise ValueError(
                    f"baked animation track {bone_name!r} has no bone basis matrix"
                )
            if len(matrix_frames) != len(times):
                raise ValueError(
                    f"baked animation track {bone_name!r} has {len(matrix_frames)} matrices for {len(times)} times"
                )

            locations = np.empty((3, len(times)), dtype=np.float32)
            rotations = np.empty((4, len(times)), dtype=np.float32)
            scales = np.ones((3, len(times)), dtype=np.float32)
            previous_quaternion = None

            for index, matrix in enumerate(matrix_frames):
                location, quaternion, _scale = _decompose_pose_matrix(matrix)
                # q and -q encode the same orientation. Keep neighboring baked
                # samples in one hemisphere so Ogre interpolation cannot take
                # a gratuitous long path if decomposition changes sign.
                if (
                    previous_quaternion is not None
                    and float(np.dot(previous_quaternion, quaternion)) < 0.0
                ):
                    quaternion = -quaternion
                previous_quaternion = quaternion
                locations[:, index] = location
                rotations[:, index] = quaternion

            self.append_animation_track(
                bone_name=bone_name,
                bone_matrix=bone_matrix_map[bone_name],
                nd_times=times,
                nd_locations=locations,
                nd_rotations=rotations,
                nd_scales=scales,
                use_scale=False,
            )


class AnimatedSkeletonData(SkeletonData):
    def get_animations(self):
        return list(self._animations)

    def calc_animation_fps(self):
        deltas = []
        for animation in self._animations:
            for track in getattr(animation, "_tracks", ()):  # pragma: no branch
                if len(track.times) > 1:
                    diff = np.diff(track.times.astype(np.float64))
                    deltas.extend(float(value) for value in diff if value > 1e-8)
        if not deltas:
            return 24.0
        return float(1.0 / min(deltas))


def _matrix3_array(matrix):
    values = getattr(matrix, "values", matrix)
    array = np.asarray(values, dtype=np.float32)
    if array.shape == (9,):
        array = array.reshape(3, 3)
    if array.shape != (3, 3):
        raise ValueError(f"expected 3x3 bone matrix, got {array.shape}")
    return array


def _decompose_pose_matrix(matrix):
    """Return Blender-space translation, quaternion (wxyz), and scale.

    This intentionally has no ``mathutils`` dependency so the binary semantic
    suite can run on ordinary CPython. The 3x3 block is split into per-column
    scale and an orthonormal rotation basis, matching Blender's transform
    convention for ordinary local pose matrices. A polar/SVD cleanup handles
    small numerical drift or shear before quaternion extraction.
    """

    array = np.asarray(matrix, dtype=np.float64)
    if array.shape != (4, 4):
        raise ValueError(f"expected 4x4 pose matrix, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError("pose matrix contains non-finite values")

    location = array[:3, 3].astype(np.float32)
    linear = array[:3, :3]
    scale = np.linalg.norm(linear, axis=0)
    if np.any(scale < 1e-12):
        raise ValueError("pose matrix contains a degenerate scale axis")

    normalized = linear / scale[np.newaxis, :]
    u, _singular, vh = np.linalg.svd(normalized)
    rotation = u @ vh
    if np.linalg.det(rotation) < 0.0:
        # Preserve a proper rotation. Scale keys are intentionally unsupported,
        # so reflection magnitude/sign is not serialized on this path.
        u[:, -1] *= -1.0
        rotation = u @ vh

    quaternion = _quaternion_from_rotation_matrix(rotation)
    return location, quaternion.astype(np.float32), scale.astype(np.float32)


def _quaternion_from_rotation_matrix(rotation):
    matrix = np.asarray(rotation, dtype=np.float64)
    if matrix.shape != (3, 3):
        raise ValueError(f"expected 3x3 rotation matrix, got {matrix.shape}")

    trace = float(np.trace(matrix))
    if trace > 0.0:
        s = np.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (matrix[2, 1] - matrix[1, 2]) / s
        y = (matrix[0, 2] - matrix[2, 0]) / s
        z = (matrix[1, 0] - matrix[0, 1]) / s
    elif matrix[0, 0] > matrix[1, 1] and matrix[0, 0] > matrix[2, 2]:
        s = np.sqrt(max(0.0, 1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2])) * 2.0
        if s < 1e-12:
            return np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        w = (matrix[2, 1] - matrix[1, 2]) / s
        x = 0.25 * s
        y = (matrix[0, 1] + matrix[1, 0]) / s
        z = (matrix[0, 2] + matrix[2, 0]) / s
    elif matrix[1, 1] > matrix[2, 2]:
        s = np.sqrt(max(0.0, 1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2])) * 2.0
        if s < 1e-12:
            return np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        w = (matrix[0, 2] - matrix[2, 0]) / s
        x = (matrix[0, 1] + matrix[1, 0]) / s
        y = 0.25 * s
        z = (matrix[1, 2] + matrix[2, 1]) / s
    else:
        s = np.sqrt(max(0.0, 1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1])) * 2.0
        if s < 1e-12:
            return np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        w = (matrix[1, 0] - matrix[0, 1]) / s
        x = (matrix[0, 2] + matrix[2, 0]) / s
        y = (matrix[1, 2] + matrix[2, 1]) / s
        z = 0.25 * s

    quaternion = np.asarray([w, x, y, z], dtype=np.float64)
    norm = float(np.linalg.norm(quaternion))
    if norm < 1e-12:
        raise ValueError("pose rotation decomposed to a zero quaternion")
    return quaternion / norm


def _channel_array(value, channels, count, label):
    array = np.asarray(value, dtype=np.float32)
    if array.shape == (count, channels):
        array = array.T
    if array.shape != (channels, count):
        raise ValueError(
            f"animation {label} must be {channels}x{count}, got {array.shape}"
        )
    return array


def _as_fcurve_pairs(values, frames):
    values = np.asarray(values, dtype=np.float32)
    frames = np.asarray(frames, dtype=np.float32).reshape(-1)
    if values.ndim != 2 or values.shape[1] != len(frames):
        raise ValueError("animation channel/frame counts do not match")
    out = np.empty((values.shape[0], len(frames) * 2), dtype=np.float32)
    out[:, 0::2] = frames
    out[:, 1::2] = values
    return out


__all__ = [
    "AnimatedSkeletonData",
    "AnimationData",
    "BlenderAnimationTrack",
    "UnsupportedAnimationScale",
]
