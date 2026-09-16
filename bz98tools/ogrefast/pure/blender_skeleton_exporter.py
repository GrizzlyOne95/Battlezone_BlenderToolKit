from __future__ import annotations

"""Run the established Blender skeleton collector on pure serializers.

This is the standalone `.skeleton` counterpart to the mesh collector adapter.
It deliberately reuses ``ogre_exporter.save_skeleton`` so bone selection,
hierarchy rules, ordinary Action collection and visual-keying bake behavior
stay identical between native and pure backends.
"""

import importlib
import sys

from . import kenshi_compat
from .animation_compat import AnimationData
from .skeleton_compat import KenshiObjectSerializer


def _load_shared_exporter():
    module_name = "kenshi_blender_tool"
    previous = sys.modules.get(module_name)
    inserted = previous is None
    if inserted:
        sys.modules[module_name] = kenshi_compat
    try:
        exporter = importlib.import_module("bz98tools.ogrefast.ogre_exporter")
    finally:
        if inserted:
            sys.modules.pop(module_name, None)

    # ogre_exporter copies these symbols from kenshi_blender_tool at import
    # time. Replace the stateful types explicitly in case another Ogre path
    # imported the module earlier in this Blender session.
    exporter.KenshiObjectSerializer = KenshiObjectSerializer
    exporter.AnimationData = AnimationData
    exporter.BoneData = kenshi_compat.BoneData
    exporter.Vector3 = kenshi_compat.Vector3
    exporter.OgreQuaternion = kenshi_compat.OgreQuaternion
    exporter.Matrix3 = kenshi_compat.Matrix3
    exporter.SkeletonVersion = kenshi_compat.SkeletonVersion
    return exporter


def save(
    operator,
    context,
    filepath,
    *,
    apply_transform=True,
    export_animation=False,
    export_all_bones=False,
    export_version="V_1_10",
    is_visual_keying=False,
    use_scale_keyframe=False,
):
    exporter = _load_shared_exporter()
    return exporter.save_skeleton(
        operator=operator,
        context=context,
        filepath=filepath,
        apply_transform=apply_transform,
        export_animation=export_animation,
        export_all_bones=export_all_bones,
        export_version=export_version,
        is_visual_keying=is_visual_keying,
        use_scale_keyframe=use_scale_keyframe,
    )


__all__ = ["save"]
