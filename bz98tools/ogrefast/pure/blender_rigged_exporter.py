from __future__ import annotations

"""Run the established Blender rigged-mesh collector on the pure serializers."""

import importlib
import sys

from . import kenshi_compat
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

    # ogre_exporter copied the symbols from kenshi_blender_tool at import time;
    # swap only the serializer factory so the existing Blender collection code
    # writes through our pure mesh + skeleton implementation.
    exporter.KenshiObjectSerializer = KenshiObjectSerializer
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
        export_animation=False,
        export_all_bones=False,
        mesh_optimize=True,
        export_version="V_1_10",
        is_visual_keying=False,
        use_scale_keyframe=False,
        num_fake_pose=0,
        renormalize_weights=renormalize_weights,
    )


__all__ = ["save"]
