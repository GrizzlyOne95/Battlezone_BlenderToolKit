from __future__ import annotations

"""Standalone `.skeleton` export through the unified pure facade.

The historical Blender skeleton collector is reused unchanged. ogrefast package
startup has already installed the pure ``kenshi_blender_tool`` compatibility
module on runtimes where the native extension cannot load.
"""

from .. import ogre_exporter


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
    return ogre_exporter.save_skeleton(
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
