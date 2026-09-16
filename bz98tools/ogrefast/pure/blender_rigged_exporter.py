from __future__ import annotations

"""Run the established Blender mesh/rig/pose collector on pure serializers."""

import importlib
import sys

from . import pose_compat
from .animation_compat import AnimationData
from .skeleton_compat import KenshiObjectSerializer as SkeletonKenshiObjectSerializer


class KenshiObjectSerializer(SkeletonKenshiObjectSerializer):
    """Combine pure mesh-pose creation with pure skeleton/animation I/O."""

    def create_mesh(self, filename):
        return pose_compat.MeshData(filename, "General")


def _load_shared_exporter():
    module_name = "kenshi_blender_tool"
    previous = sys.modules.get(module_name)
    inserted = previous is None
    if inserted:
        sys.modules[module_name] = pose_compat
    try:
        exporter = importlib.import_module("bz98tools.ogrefast.ogre_exporter")
    finally:
        if inserted:
            sys.modules.pop(module_name, None)

    # ogre_exporter copies symbols from kenshi_blender_tool at import time.
    # Explicitly replace the stateful compatibility types too, in case the
    # module had already been imported earlier in this Blender session.
    exporter.KenshiObjectSerializer = KenshiObjectSerializer
    exporter.SubMeshData = pose_compat.SubMeshData
    exporter.AnimationData = AnimationData
    return exporter


def save(
    operator,
    context,
    filepath,
    *,
    tangent_format="TANGENT_4",
    export_colour=False,
    apply_transform=True,
    apply_modifiers=True,
    export_skeleton=True,
    export_poses=False,
    export_animation=False,
    renormalize_weights=True,
):
    exporter = _load_shared_exporter()
    return exporter.save(
        operator,
        context,
        filepath,
        tangent_format=tangent_format,
        export_colour=export_colour,
        apply_transform=apply_transform,
        apply_modifiers=apply_modifiers,
        export_skeleton=export_skeleton,
        export_poses=export_poses,
        export_animation=export_animation,
        export_all_bones=False,
        mesh_optimize=True,
        export_version="V_1_10",
        is_visual_keying=False,
        use_scale_keyframe=False,
        num_fake_pose=0,
        renormalize_weights=renormalize_weights,
    )


__all__ = ["save"]
