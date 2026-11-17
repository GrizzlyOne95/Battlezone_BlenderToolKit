"""Redux ``.skeleton`` <-> neutral skeleton (bones, bind pose, animation clips). Maya-free.

The binary work is the toolkit's ``bzrmodelporter.ogreskeleton_serializer.SkeletonSerializer``
(Serializer_v1.80, the version the pure Blender exporter writes). Ogre space is Maya space, so
bone positions, orientations and key values are copied unchanged; keyframe values stay Ogre's
bind-relative offsets (``position = bind + translation``, ``orientation = bind * rotation``).

Not carried: linked skeleton animation sources (rejected on read), the blend mode other than
the value kept on ``Skeleton.blendmode``.
"""

from __future__ import annotations

from typing import List, Optional

from .model import Bone, Clip, Skeleton, Track
from .toolkit import ensure_toolkit


def _modules():
    ensure_toolkit()
    from bz98tools.bzrmodelporter.ogreskeleton import Skeleton as LegacySkeleton
    from bz98tools.bzrmodelporter.ogreskeleton_serializer import SkeletonSerializer
    from bz98tools.bzrmodelporter.spacial import Quaternion, Vector3

    return LegacySkeleton, SkeletonSerializer, Quaternion, Vector3


def read_skeleton(path: str) -> Skeleton:
    _, SkeletonSerializer, _, _ = _modules()
    with open(path, "rb") as handle:
        legacy = SkeletonSerializer(handle).read()
    if any(True for _ in legacy.sources()):
        raise ValueError("linked skeleton animation sources are not supported")
    skeleton = Skeleton(name=path.replace("\\", "/").rsplit("/", 1)[-1], blendmode=int(legacy.blendmode))
    for bone in legacy.bones():
        skeleton.bones.append(
            Bone(
                name=str(bone.name),
                id=int(bone.handle),
                parent=str(bone.parent.name) if bone.parent is not None else None,
                position=(float(bone.position.x), float(bone.position.y), float(bone.position.z)),
                orientation=(
                    float(bone.orientation.w), float(bone.orientation.x),
                    float(bone.orientation.y), float(bone.orientation.z),
                ),
                scale=(float(bone.scale.x), float(bone.scale.y), float(bone.scale.z)),
            )
        )
    for animation in legacy.animations():
        clip = Clip(name=str(animation.name), length=float(animation.duration))
        for track in animation.tracks():
            out = Track(bone=str(track.target_bone.name))
            for key in track.keyframe_list:
                out.times.append(float(key.time))
                out.translations.append((float(key.translation.x), float(key.translation.y), float(key.translation.z)))
                out.rotations.append(
                    (float(key.rotation.w), float(key.rotation.x), float(key.rotation.y), float(key.rotation.z))
                )
                out.scales.append((float(key.scale.x), float(key.scale.y), float(key.scale.z)))
            clip.tracks.append(out)
        skeleton.clips.append(clip)
    return skeleton


def write_skeleton(skeleton: Skeleton, path: str) -> None:
    LegacySkeleton, SkeletonSerializer, Quaternion, Vector3 = _modules()
    target = LegacySkeleton()
    target.blendmode = int(skeleton.blendmode)
    by_name = {}
    for bone in sorted(skeleton.bones, key=lambda b: b.id):
        by_name[bone.name] = target.create_bone(
            bone.name, int(bone.id), Vector3(*bone.position), Quaternion(*bone.orientation), Vector3(*bone.scale)
        )
    for bone in skeleton.bones:
        if bone.parent:
            if bone.parent not in by_name:
                raise ValueError("bone %r references missing parent %r" % (bone.name, bone.parent))
            by_name[bone.parent].add_child(by_name[bone.name])
    for clip in skeleton.clips:
        animation = target.create_animation(clip.name, float(clip.length))
        for track in clip.tracks:
            if track.bone not in by_name:
                raise ValueError("clip %r animates missing bone %r" % (clip.name, track.bone))
            legacy_track = animation.create_track(by_name[track.bone])
            for i, time in enumerate(track.times):
                scale = track.scales[i] if i < len(track.scales) else (1.0, 1.0, 1.0)
                legacy_track.create_keyframe(
                    float(time), Quaternion(*track.rotations[i]), Vector3(*track.translations[i]), Vector3(*scale)
                )
    valid, message = target.verify()
    if not valid:
        raise ValueError(message)
    with open(path, "wb") as handle:
        SkeletonSerializer(handle).write(target, version="[Serializer_v1.80]")


def renumbered(skeleton: Skeleton) -> dict:
    """Blender's export renumbers bone ids 0..n-1 in id order; returns {old id: new id}."""
    return {bone.id: index for index, bone in enumerate(sorted(skeleton.bones, key=lambda b: b.id))}
