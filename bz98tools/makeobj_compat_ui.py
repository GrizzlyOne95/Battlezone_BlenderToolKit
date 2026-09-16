# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Blender UI for optional MakeOBJ-compatible GEO export behavior."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty

from . import makeobj_compat_export


class BZ98TOOLS_PT_makeobj_compat(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_MAKEOBJ_COMPAT"
    bl_label = "MakeOBJ Compatibility"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.label(text="Legacy GEO Authoring", icon="MODIFIER")
        box.prop(scene, "bz_makeobj_legacy_normals", text="Legacy Vertex Normals")
        box.prop(scene, "bz_makeobj_compute_poly_color", text="ComputePolyColor")

        info = layout.box()
        info.label(text="Applies to direct GEO exports and GEOs", icon="INFO")
        info.label(text="generated while exporting VDF/SDF.")
        info.label(text="Both options are disabled by default.")


_CLASSES = (BZ98TOOLS_PT_makeobj_compat,)


def register():
    bpy.types.Scene.bz_makeobj_legacy_normals = BoolProperty(
        name="Legacy Vertex Normals",
        description=(
            "Rebuild each GEO vertex normal from an equal-weight sum of adjacent "
            "unit face normals, matching MakeOBJ 1.1.5"
        ),
        default=False,
    )
    bpy.types.Scene.bz_makeobj_compute_poly_color = BoolProperty(
        name="ComputePolyColor",
        description=(
            "Derive GEO face RGB by rasterizing each UV polygon over its image "
            "texture and averaging covered texels, matching MakeOBJ 1.1.5"
        ),
        default=False,
    )

    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    makeobj_compat_export.install()


def unregister():
    makeobj_compat_export.uninstall()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    if hasattr(bpy.types.Scene, "bz_makeobj_compute_poly_color"):
        del bpy.types.Scene.bz_makeobj_compute_poly_color
    if hasattr(bpy.types.Scene, "bz_makeobj_legacy_normals"):
        del bpy.types.Scene.bz_makeobj_legacy_normals
