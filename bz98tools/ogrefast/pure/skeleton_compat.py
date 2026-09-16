from __future__ import annotations

"""Compatibility bridge between ogrefast's legacy API and pure Ogre skeleton I/O."""

from pathlib import Path

from ...bzrmodelporter.ogreskeleton import Skeleton as LegacySkeleton
from ...bzrmodelporter.ogreskeleton_serializer import SkeletonSerializer as LegacySkeletonSerializer
from ...bzrmodelporter.spacial import Quaternion as LegacyQuaternion
from ...bzrmodelporter.spacial import Vector3 as LegacyVector3
from .kenshi_compat import (
    BoneData,
    KenshiObjectSerializer as MeshKenshiObjectSerializer,
    OgreQuaternion,
    SkeletonData,
    SkeletonVersion,
    Vector3,
)


class UnsupportedPureSkeleton(RuntimeError):
    pass


class KenshiObjectSerializer(MeshKenshiObjectSerializer):
    """Add bone/hierarchy `.skeleton` read/write to the pure mesh serializer."""

    def __init__(self, logfile="Kenshi_io_OGRE.log"):
        super().__init__(logfile=logfile)
        self._resource_locations: list[Path] = []

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
        if getattr(skeleton, "_animations", None):
            raise UnsupportedPureSkeleton(
                "animation serialization is not wired through the compatibility API yet"
            )

        legacy = _to_legacy_skeleton(skeleton)
        path = Path(path)
        with path.open("wb") as stream:
            LegacySkeletonSerializer(stream).write(legacy, version="[Serializer_v1.80]")

    def load_skeleton(self, file):
        path = self._resolve_resource(file)
        with path.open("rb") as stream:
            legacy = LegacySkeletonSerializer(stream).read()
        if any(True for _ in legacy.animations()):
            raise UnsupportedPureSkeleton(
                "animation import is not wired through the compatibility API yet"
            )
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


def _to_legacy_skeleton(source: SkeletonData) -> LegacySkeleton:
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

    valid, message = target.verify()
    if not valid:
        raise UnsupportedPureSkeleton(message)
    return target


def _from_legacy_skeleton(source: LegacySkeleton, filename: str) -> SkeletonData:
    target = SkeletonData(filename, "General")
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
    return target


__all__ = [
    "KenshiObjectSerializer",
    "UnsupportedPureSkeleton",
]
