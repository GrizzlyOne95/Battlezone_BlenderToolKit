from __future__ import annotations

"""Compatibility bridge between ogrefast's legacy API and pure Ogre skeleton I/O."""

from pathlib import Path

import numpy as np

from ...bzrmodelporter.ogreskeleton import Skeleton as LegacySkeleton
from ...bzrmodelporter.ogreskeleton_serializer import SkeletonSerializer as LegacySkeletonSerializer
from ...bzrmodelporter.spacial import Quaternion as LegacyQuaternion
from ...bzrmodelporter.spacial import Vector3 as LegacyVector3
from .animation_compat import (
    AnimatedSkeletonData,
    AnimationData,
    UnsupportedAnimationScale,
)
from .kenshi_compat import (
    BoneData,
    KenshiObjectSerializer as MeshKenshiObjectSerializer,
    OgreQuaternion,
    SkeletonVersion,
    Vector3,
)


class UnsupportedPureSkeleton(RuntimeError):
    pass


class KenshiObjectSerializer(MeshKenshiObjectSerializer):
    """Add bones, hierarchy and no-scale animation `.skeleton` I/O."""

    def __init__(self, logfile="Kenshi_io_OGRE.log"):
        super().__init__(logfile=logfile)
        self._resource_locations: list[Path] = []

    def create_skeleton(self, filename):
        return AnimatedSkeletonData(filename, "General")

    def add_resource_location(self, folder):
        path = Path(folder).expanduser()
        if path not in self._resource_locations:
            self._resource_locations.append(path)

    def save_skeleton(self, skeleton, path, version=SkeletonVersion.V_Latest):
        if version not in {
            SkeletonVersion.V_Latest,
            SkeletonVersion.V_1_8,
        }:
            raise UnsupportedPureSkeleton(
                f"pure Python skeleton writer currently targets Serializer_v1.80, got {version!r}"
            )

        legacy = _to_legacy_skeleton(skeleton)
        path = Path(path)
        with path.open("wb") as stream:
            LegacySkeletonSerializer(stream).write(legacy, version="[Serializer_v1.80]")

    def load_skeleton(self, file):
        path = self._resolve_resource(file)
        with path.open("rb") as stream:
            legacy = LegacySkeletonSerializer(stream).read()
        if any(True for _ in legacy.sources()):
            raise UnsupportedPureSkeleton(
                "linked skeleton animation sources are not supported by the compatibility API yet"
            )
        return _from_legacy_skeleton(legacy, path.name)

    def _resolve_resource(self, file) -> Path:
        candidate = Path(file).expanduser()
        if candidate.is_file():
            return candidate
        for folder in self._resource_locations:
            candidate = folder / file
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(file)


def _to_legacy_skeleton(source: AnimatedSkeletonData) -> LegacySkeleton:
    target = LegacySkeleton()
    by_name = {}

    for bone in sorted(source.get_bones(has_helper=True), key=lambda item: int(item.id)):
        legacy_bone = target.create_bone(
            str(bone.name),
            int(bone.id),
            LegacyVector3(
                float(bone.position.x),
                float(bone.position.y),
                float(bone.position.z),
            ),
            LegacyQuaternion(
                float(bone.rotate.w),
                float(bone.rotate.x),
                float(bone.rotate.y),
                float(bone.rotate.z),
            ),
            LegacyVector3(
                float(bone.scale.x),
                float(bone.scale.y),
                float(bone.scale.z),
            ),
        )
        by_name[str(bone.name)] = legacy_bone

    for bone in source.get_bones(has_helper=True):
        parent_name = str(getattr(bone, "parent_name", "") or "")
        if not parent_name:
            continue
        if parent_name not in by_name:
            raise UnsupportedPureSkeleton(
                f"bone {bone.name!r} references missing parent {parent_name!r}"
            )
        by_name[parent_name].add_child(by_name[str(bone.name)])

    for animation in source.get_animations():
        legacy_animation = target.create_animation(
            str(animation.name), float(animation.length)
        )
        for source_track in getattr(animation, "_tracks", ()):
            if source_track.has_scale:
                raise UnsupportedAnimationScale(
                    "pure Python skeleton writer does not yet support scale keyframes"
                )
            if source_track.bone_name not in by_name:
                raise UnsupportedPureSkeleton(
                    f"animation {animation.name!r} references missing bone {source_track.bone_name!r}"
                )
            legacy_track = legacy_animation.create_track(
                by_name[source_track.bone_name]
            )
            for index, time in enumerate(source_track.times):
                translation = source_track.translations[index]
                rotation = source_track.rotations[index]
                legacy_track.create_keyframe(
                    float(time),
                    LegacyQuaternion(
                        float(rotation[0]),
                        float(rotation[1]),
                        float(rotation[2]),
                        float(rotation[3]),
                    ),
                    LegacyVector3(
                        float(translation[0]),
                        float(translation[1]),
                        float(translation[2]),
                    ),
                    LegacyVector3(1.0, 1.0, 1.0),
                )

    valid, message = target.verify()
    if not valid:
        raise UnsupportedPureSkeleton(message)
    return target


def _from_legacy_skeleton(source: LegacySkeleton, filename: str) -> AnimatedSkeletonData:
    target = AnimatedSkeletonData(filename, "General")
    bones = []
    for bone in source.bones():
        bones.append(
            BoneData(
                int(bone.handle),
                str(bone.name),
                Vector3(
                    float(bone.position.x),
                    float(bone.position.y),
                    float(bone.position.z),
                ),
                OgreQuaternion(
                    float(bone.orientation.w),
                    float(bone.orientation.x),
                    float(bone.orientation.y),
                    float(bone.orientation.z),
                ),
                Vector3(
                    float(bone.scale.x),
                    float(bone.scale.y),
                    float(bone.scale.z),
                ),
                str(bone.parent.name) if bone.parent is not None else "",
                [str(child.name) for child in bone.children],
            )
        )
    target.set_bones(bones)

    for source_animation in source.animations():
        animation = AnimationData()
        animation.name = str(source_animation.name)
        animation.length = float(source_animation.duration)
        for source_track in source_animation.tracks():
            times = []
            translations = []
            rotations = []
            scales = []
            has_scale = False
            for keyframe in source_track.keyframe_list:
                times.append(float(keyframe.time))
                translations.append(
                    [
                        float(keyframe.translation.x),
                        float(keyframe.translation.y),
                        float(keyframe.translation.z),
                    ]
                )
                rotations.append(
                    [
                        float(keyframe.rotation.w),
                        float(keyframe.rotation.x),
                        float(keyframe.rotation.y),
                        float(keyframe.rotation.z),
                    ]
                )
                scale = np.asarray(
                    [
                        float(keyframe.scale.x),
                        float(keyframe.scale.y),
                        float(keyframe.scale.z),
                    ],
                    dtype=np.float32,
                )
                if not np.allclose(scale, 1.0, atol=1e-6):
                    has_scale = True
                scales.append(scale)
            if has_scale:
                raise UnsupportedAnimationScale(
                    f"animation {source_animation.name!r} contains scale keyframes; falling back to legacy import"
                )
            animation.append_ogre_track(
                str(source_track.target_bone.name),
                times,
                translations,
                rotations,
                scales,
                has_scale=False,
            )
        target.add_animation(animation)

    return target


__all__ = [
    "KenshiObjectSerializer",
    "UnsupportedPureSkeleton",
]