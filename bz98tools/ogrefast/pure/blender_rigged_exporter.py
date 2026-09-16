from __future__ import annotations

"""Run the established Blender mesh/rig/pose collector on the pure facade.

The ogrefast package installs ``kenshi_blender_tool`` before this module is
loaded, so the historical exporter can now be reused directly without
stateful monkey-patching of its module globals.
"""

from .. import ogre_exporter


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
    return ogre_exporter.save(
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
