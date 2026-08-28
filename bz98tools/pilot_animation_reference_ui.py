# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Blender-side reference for Redux pilot animation names and Person indices."""

from __future__ import annotations

import bpy

from .pilot_animation_profiles import pilot_animation_reference_rows


class BZ98TOOLS_PT_view3d_pilot_animation_reference(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_PILOT_ANIMATION_REFERENCE"
    bl_label = "Pilot Animation Reference"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        intro = layout.box()
        intro.label(text="Redux Person / Ogre Animation Contract", icon="ACTION")
        intro.label(text="Person indices are gameplay semantics.")
        intro.label(text="Ogre .skeleton animations are name-addressed.")

        indexed_box = layout.box()
        indexed_box.label(text="Verified Person Indices", icon="SORTBYEXT")
        for index, name, indexed in pilot_animation_reference_rows():
            if not indexed:
                continue
            row = indexed_box.row(align=True)
            row.label(text=f"{index:02d}")
            row.label(text=name, icon="ANIM")
            if index >= 9:
                row.label(text="Redux")

        named_box = layout.box()
        named_box.label(text="Additional Stock Ogre Clips", icon="ARMATURE_DATA")
        named_box.label(text="Named clips; no verified Person index.")
        for index, name, indexed in pilot_animation_reference_rows():
            if indexed:
                continue
            row = named_box.row(align=True)
            row.label(text="--")
            row.label(text=name, icon="ANIM")

        note = layout.box()
        note.label(text="Do not infer an index from skeleton ordering.", icon="INFO")
        note.label(text="The selected stock .skeleton remains authoritative")
        note.label(text="for which named clips are actually present.")


_CLASSES = (BZ98TOOLS_PT_view3d_pilot_animation_reference,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
