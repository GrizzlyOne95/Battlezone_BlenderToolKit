# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Blender UI for safe Redux pilot animation replacement.

The stock .skeleton remains the authoritative bone/bind/serializer contract.
Artists map Blender Actions onto named stock clips, the toolkit bakes evaluated
poses at 30 FPS into a temporary Ogre skeleton, and the pure patch backend copies
only those animations into a new stock-compatible .skeleton.
"""

from __future__ import annotations

import os
import tempfile

import bpy

from . import pilot_animation_patch as patch_backend
from .pilot_animation_profiles import (
    action_name_matches_clip,
    detect_pilot_profile,
)


BAKE_FPS = 30


class BZPilotAnimationClipEntry(bpy.types.PropertyGroup):
    clip_name: bpy.props.StringProperty(name="Redux Clip")
    selected: bpy.props.BoolProperty(
        name="Replace",
        description="Replace this named stock clip in the output skeleton",
        default=False,
    )
    action: bpy.props.PointerProperty(
        name="Source Action",
        description="Blender Action to bake into this Redux pilot clip",
        type=bpy.types.Action,
    )
    stock_duration: bpy.props.FloatProperty(name="Stock Duration", default=0.0)
    stock_track_count: bpy.props.IntProperty(name="Stock Tracks", default=0)


class BZ98TOOLS_UL_pilot_animation_clips(bpy.types.UIList):
    bl_idname = "BZ98TOOLS_UL_pilot_animation_clips"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
    ):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.clip_name, icon="ANIM")
        row.prop(item, "action", text="")
        if item.stock_duration > 0.0:
            row.label(text=f"{item.stock_duration:.2f}s")


def _resolve_path(value):
    if not value:
        return ""
    return os.path.abspath(bpy.path.abspath(value))


def _resolve_armature(context):
    scene = context.scene
    explicit = getattr(scene, "bz_pilot_armature", None)
    if explicit is not None and getattr(explicit, "type", None) == "ARMATURE":
        return explicit

    active = getattr(context, "object", None)
    if getattr(active, "type", None) == "ARMATURE":
        return active
    if getattr(active, "type", None) == "MESH":
        try:
            found = active.find_armature()
        except Exception:
            found = None
        if found is not None:
            return found

    selected = [
        obj
        for obj in getattr(context, "selected_objects", [])
        if getattr(obj, "type", None) == "ARMATURE"
    ]
    if len(selected) == 1:
        return selected[0]

    candidates = []
    for obj in getattr(context.scene, "objects", []):
        if getattr(obj, "type", None) != "ARMATURE":
            continue
        if any("OGREID" in bone for bone in obj.data.bones):
            candidates.append(obj)
    return candidates[0] if len(candidates) == 1 else None


def _load_stock_skeleton(scene):
    stock_path = _resolve_path(scene.bz_pilot_stock_skeleton_path)
    if not stock_path:
        raise patch_backend.PilotAnimationPatchError(
            "Choose the original stock pilot .skeleton first."
        )
    if not os.path.isfile(stock_path):
        raise patch_backend.PilotAnimationPatchError(
            f"Stock skeleton does not exist: {stock_path}"
        )
    return stock_path, patch_backend.load_skeleton(stock_path)


def _armature_contract(armature):
    contract = {}
    duplicate_handles = []
    for bone in armature.data.bones:
        if "OGREID" not in bone:
            continue
        try:
            handle = int(bone["OGREID"])
        except (TypeError, ValueError):
            raise patch_backend.PilotAnimationPatchError(
                f"Bone {bone.name!r} has a non-integer OGREID."
            )
        if handle in contract:
            duplicate_handles.append(handle)
            continue

        parent_handle = None
        if bone.parent is not None:
            if "OGREID" not in bone.parent:
                raise patch_backend.PilotAnimationPatchError(
                    f"OGRE bone {bone.name!r} is parented to non-export bone "
                    f"{bone.parent.name!r}. Keep the stock OGRE hierarchy unchanged."
                )
            parent_handle = int(bone.parent["OGREID"])
        contract[handle] = (bone.name, parent_handle)

    if duplicate_handles:
        raise patch_backend.PilotAnimationPatchError(
            f"Duplicate OGREID handle(s) on armature: {sorted(set(duplicate_handles))}"
        )
    if not contract:
        raise patch_backend.PilotAnimationPatchError(
            "Selected armature has no OGREID bone metadata. Import the stock Redux skeleton/mesh first."
        )
    return contract


def _validate_armature_against_stock(armature, stock):
    contract = _armature_contract(armature)
    stock_handles = set(stock.bone_map)
    armature_handles = set(contract)
    if stock_handles != armature_handles:
        missing = sorted(stock_handles - armature_handles)
        extra = sorted(armature_handles - stock_handles)
        raise patch_backend.PilotAnimationPatchError(
            f"Armature OGREID set differs from stock (missing={missing}, extra={extra})."
        )

    for handle in sorted(stock_handles):
        stock_bone = stock.get_bone(handle)
        stock_parent = None if stock_bone.parent is None else stock_bone.parent.handle
        armature_name, armature_parent = contract[handle]
        if armature_name != stock_bone.name:
            raise patch_backend.PilotAnimationPatchError(
                f"OGREID {handle} name mismatch: stock={stock_bone.name!r}, "
                f"armature={armature_name!r}."
            )
        if armature_parent != stock_parent:
            raise patch_backend.PilotAnimationPatchError(
                f"Bone {stock_bone.name!r} parent mismatch: stock={stock_parent}, "
                f"armature={armature_parent}."
            )
    return contract


def _profile_from_stock(stock):
    return detect_pilot_profile(
        stock.bone_map,
        serializer_version=getattr(stock, "serializer_version", ""),
    )


def _find_matching_action(clip_name):
    exact = []
    suffixed = []
    for action in bpy.data.actions:
        if action.name.lower() == clip_name.lower():
            exact.append(action)
        elif action_name_matches_clip(clip_name, action.name):
            suffixed.append(action)
    return exact[0] if exact else (suffixed[0] if suffixed else None)


def _selected_plan(scene):
    plan = []
    missing = []
    for entry in scene.bz_pilot_animation_clips:
        if not entry.selected:
            continue
        if entry.action is None:
            missing.append(entry.clip_name)
            continue
        plan.append((entry.clip_name, entry.action))

    if missing:
        raise patch_backend.PilotAnimationPatchError(
            "Selected clip(s) have no source Action: " + ", ".join(missing)
        )
    if not plan:
        raise patch_backend.PilotAnimationPatchError(
            "Select at least one clip and assign a source Action."
        )
    return plan


def _set_status(scene, text):
    scene.bz_pilot_last_status = str(text)


def _prepare_output_path(scene, stock_path):
    output_path = _resolve_path(scene.bz_pilot_output_skeleton_path)
    if not output_path:
        stem = os.path.splitext(os.path.basename(stock_path))[0]
        output_path = os.path.join(
            os.path.dirname(stock_path), f"{stem}_custom.skeleton"
        )
        scene.bz_pilot_output_skeleton_path = output_path

    if os.path.abspath(output_path) == os.path.abspath(stock_path):
        raise patch_backend.PilotAnimationPatchError(
            "Output cannot overwrite the original stock skeleton. Choose a separate file."
        )
    if os.path.exists(output_path) and not scene.bz_pilot_overwrite_output:
        raise patch_backend.PilotAnimationPatchError(
            f"Output already exists: {output_path}. Enable Overwrite Output or choose another file."
        )
    return output_path


def _capture_context_state(context):
    return {
        "active": context.view_layer.objects.active,
        "selected": [obj for obj in context.selected_objects],
        "mode": getattr(context, "mode", "OBJECT"),
        "fps": context.scene.render.fps,
        "fps_base": context.scene.render.fps_base,
        "frame_step": context.scene.frame_step,
        "frame_current": context.scene.frame_current,
    }


def _restore_context_state(context, state):
    try:
        if getattr(context, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass

    for obj in context.view_layer.objects:
        try:
            obj.select_set(False)
        except Exception:
            pass

    for obj in state["selected"]:
        if obj.name in context.view_layer.objects:
            try:
                obj.select_set(True)
            except Exception:
                pass

    active = state["active"]
    if active is not None and active.name in context.view_layer.objects:
        context.view_layer.objects.active = active

    context.scene.render.fps = state["fps"]
    context.scene.render.fps_base = state["fps_base"]
    context.scene.frame_step = state["frame_step"]
    try:
        context.scene.frame_set(state["frame_current"])
    except Exception:
        pass

    previous_mode = state["mode"]
    if active is not None and active.name in context.view_layer.objects:
        try:
            if previous_mode == "POSE" and active.type == "ARMATURE":
                bpy.ops.object.mode_set(mode="POSE")
            elif previous_mode.startswith("EDIT"):
                bpy.ops.object.mode_set(mode="EDIT")
        except Exception:
            pass


def _build_baked_replacement(context, operator, armature, plan, temp_dir):
    """Bake mapped Actions through the native Ogre exporter into one skeleton."""

    from .ogrefast import ogre_exporter

    state = _capture_context_state(context)
    temp_rig = None
    action_copies = []
    token_to_clip = {}
    temp_path = os.path.join(temp_dir, "pilot_action_bake.skeleton")

    try:
        if getattr(context, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        temp_rig = armature.copy()
        temp_rig.name = f"__BZ_PilotBake_{armature.name}"
        context.scene.collection.objects.link(temp_rig)
        temp_rig.animation_data_clear()
        animdata = temp_rig.animation_data_create()

        for index, (clip_name, source_action) in enumerate(plan):
            action_copy = source_action.copy()
            action_copy.name = f"__BZPILOT_{index:03d}_{clip_name}"
            action_copies.append(action_copy)
            token_to_clip[action_copy.name] = clip_name

            track = animdata.nla_tracks.new()
            track.name = action_copy.name
            track.mute = True
            start = int(round(float(action_copy.frame_range[0])))
            track.strips.new(action_copy.name, start, action_copy)

        for obj in context.view_layer.objects:
            obj.select_set(False)
        temp_rig.select_set(True)
        context.view_layer.objects.active = temp_rig

        # Stock clips were sampled at 30 FPS.  Always bake every frame and omit
        # scale tracks so the temporary replacement matches observed stock data.
        context.scene.render.fps = BAKE_FPS
        context.scene.render.fps_base = 1.0
        context.scene.frame_step = 1

        result = ogre_exporter.save_skeleton(
            operator=operator,
            context=context,
            filepath=temp_path,
            apply_transform=False,
            export_animation=True,
            export_all_bones=False,
            export_version="V_1_10",
            is_visual_keying=True,
            use_scale_keyframe=False,
        )
        if result != {"FINISHED"} or not os.path.isfile(temp_path):
            raise patch_backend.PilotAnimationPatchError(
                "Native Ogre skeleton bake did not produce a temporary skeleton."
            )

        replacement = patch_backend.load_skeleton(temp_path)
        renamed = {}
        for token, clip_name in token_to_clip.items():
            animation = replacement.animation_map.get(token)
            if animation is None:
                raise patch_backend.PilotAnimationPatchError(
                    f"Baked skeleton is missing temporary Action {token!r}."
                )
            animation.name = clip_name
            renamed[clip_name] = animation
        replacement.animation_map = renamed
        return replacement
    finally:
        if temp_rig is not None:
            try:
                bpy.data.objects.remove(temp_rig, do_unlink=True)
            except Exception:
                pass
        for action in action_copies:
            try:
                bpy.data.actions.remove(action)
            except Exception:
                pass
        _restore_context_state(context, state)


def _write_patched_output(stock, replacement, plan, output_path, bind_tolerance):
    clip_names = [clip_name for clip_name, _ in plan]
    original_animation_names = tuple(stock.animation_map.keys())

    patch_backend.patch_animations(
        stock,
        replacement,
        clip_names,
        validate_bind_pose=True,
        bind_tolerance=bind_tolerance,
    )

    output_dir = os.path.dirname(output_path) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    payload = patch_backend.dump_skeleton(
        stock,
        version=stock.serializer_version,
        validate_chunk_sizes=False,
    )

    fd, temp_output = tempfile.mkstemp(
        prefix=".bz_pilot_patch_", suffix=".skeleton", dir=output_dir
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(temp_output, output_path)
    except Exception:
        try:
            os.unlink(temp_output)
        except OSError:
            pass
        raise

    reloaded = patch_backend.load_skeleton(output_path)
    if reloaded.serializer_version != stock.serializer_version:
        raise patch_backend.PilotAnimationPatchError(
            "Output serializer version changed during write validation."
        )
    patch_backend.validate_skeleton_compatibility(
        stock,
        reloaded,
        bind_tolerance=bind_tolerance,
    )
    for name in original_animation_names:
        if name not in reloaded.animation_map:
            raise patch_backend.PilotAnimationPatchError(
                f"Output lost untouched stock animation {name!r}."
            )
    for name in clip_names:
        if name not in reloaded.animation_map:
            raise patch_backend.PilotAnimationPatchError(
                f"Output is missing patched animation {name!r}."
            )
    return reloaded


class BZ98TOOLS_OT_pilot_refresh_clips(bpy.types.Operator):
    bl_idname = "bz.pilot_refresh_clips"
    bl_label = "Load Stock Clips"
    bl_description = "Read the stock pilot skeleton and populate its exact named animation clips"

    def execute(self, context):
        scene = context.scene
        try:
            stock_path, stock = _load_stock_skeleton(scene)
            profile = _profile_from_stock(stock)

            previous = {
                item.clip_name: (item.action, item.selected)
                for item in scene.bz_pilot_animation_clips
            }
            scene.bz_pilot_animation_clips.clear()
            for animation in stock.animations():
                entry = scene.bz_pilot_animation_clips.add()
                entry.clip_name = animation.name
                entry.stock_duration = float(animation.duration)
                entry.stock_track_count = len(animation.track_map)
                if animation.name in previous:
                    entry.action, entry.selected = previous[animation.name]

            scene.bz_pilot_profile = profile["label"]
            scene.bz_pilot_serializer_version = getattr(
                stock, "serializer_version", ""
            )
            scene.bz_pilot_stock_bone_count = len(stock.bone_map)
            scene.bz_pilot_animation_active_index = 0

            armature = _resolve_armature(context)
            if armature is not None:
                scene.bz_pilot_armature = armature

            if not scene.bz_pilot_output_skeleton_path:
                stem = os.path.splitext(os.path.basename(stock_path))[0]
                scene.bz_pilot_output_skeleton_path = os.path.join(
                    os.path.dirname(stock_path), f"{stem}_custom.skeleton"
                )

            message = (
                f"Loaded {len(stock.animation_map)} clips from {profile['label']} "
                f"({len(stock.bone_map)} bones, {stock.serializer_version})."
            )
            _set_status(scene, message)
            self.report({"INFO"}, message)
            for warning in profile["warnings"]:
                self.report({"WARNING"}, warning)
            return {"FINISHED"}
        except Exception as exc:
            _set_status(scene, f"ERROR: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class BZ98TOOLS_OT_pilot_auto_map_actions(bpy.types.Operator):
    bl_idname = "bz.pilot_auto_map_actions"
    bl_label = "Auto-map Actions"
    bl_description = "Match Blender Actions to stock clip names, including the idleEject/idleElect stock spelling variant"

    def execute(self, context):
        mapped = 0
        for entry in context.scene.bz_pilot_animation_clips:
            action = _find_matching_action(entry.clip_name)
            if action is not None:
                entry.action = action
                entry.selected = True
                mapped += 1
        message = f"Auto-mapped {mapped} stock clip(s) to Blender Actions."
        _set_status(context.scene, message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BZ98TOOLS_OT_pilot_select_clips(bpy.types.Operator):
    bl_idname = "bz.pilot_select_clips"
    bl_label = "Select Pilot Clips"

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            ("ALL", "All", "Select every stock clip"),
            ("NONE", "None", "Clear every stock clip selection"),
            ("MAPPED", "Mapped", "Select only clips that have source Actions"),
        ),
        default="MAPPED",
    )

    def execute(self, context):
        for entry in context.scene.bz_pilot_animation_clips:
            if self.mode == "ALL":
                entry.selected = True
            elif self.mode == "NONE":
                entry.selected = False
            else:
                entry.selected = entry.action is not None
        return {"FINISHED"}


class BZ98TOOLS_OT_pilot_use_active_action(bpy.types.Operator):
    bl_idname = "bz.pilot_use_active_action"
    bl_label = "Use Active Action"
    bl_description = "Assign the selected armature's active Action to the highlighted stock clip"

    def execute(self, context):
        scene = context.scene
        index = scene.bz_pilot_animation_active_index
        if not (0 <= index < len(scene.bz_pilot_animation_clips)):
            self.report({"ERROR"}, "No stock clip is highlighted.")
            return {"CANCELLED"}
        armature = _resolve_armature(context)
        action = (
            armature.animation_data.action
            if armature is not None and armature.animation_data is not None
            else None
        )
        if action is None:
            self.report({"ERROR"}, "The pilot armature has no active Action.")
            return {"CANCELLED"}
        entry = scene.bz_pilot_animation_clips[index]
        entry.action = action
        entry.selected = True
        self.report({"INFO"}, f"Mapped {action.name!r} -> {entry.clip_name!r}.")
        return {"FINISHED"}


class BZ98TOOLS_OT_pilot_preview_action(bpy.types.Operator):
    bl_idname = "bz.pilot_preview_action"
    bl_label = "Preview Mapped Action"
    bl_description = "Make the highlighted mapped Action active on the pilot armature"

    def execute(self, context):
        scene = context.scene
        index = scene.bz_pilot_animation_active_index
        if not (0 <= index < len(scene.bz_pilot_animation_clips)):
            return {"CANCELLED"}
        entry = scene.bz_pilot_animation_clips[index]
        if entry.action is None:
            self.report({"ERROR"}, "Highlighted clip has no mapped Action.")
            return {"CANCELLED"}
        armature = _resolve_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Choose a pilot armature first.")
            return {"CANCELLED"}
        animdata = armature.animation_data_create()
        animdata.action = entry.action
        try:
            scene.frame_set(int(round(float(entry.action.frame_range[0]))))
        except Exception:
            pass
        scene.bz_pilot_armature = armature
        for obj in context.view_layer.objects:
            obj.select_set(False)
        armature.select_set(True)
        context.view_layer.objects.active = armature
        self.report({"INFO"}, f"Previewing {entry.action.name!r} on {armature.name!r}.")
        return {"FINISHED"}


class BZ98TOOLS_OT_pilot_validate_patch(bpy.types.Operator):
    bl_idname = "bz.pilot_validate_patch"
    bl_label = "Validate Pilot Patch"
    bl_description = "Validate stock path, armature OGRE contract, output path, and selected Action mappings"

    def execute(self, context):
        scene = context.scene
        try:
            stock_path, stock = _load_stock_skeleton(scene)
            armature = _resolve_armature(context)
            if armature is None:
                raise patch_backend.PilotAnimationPatchError(
                    "Choose or select the imported stock pilot armature."
                )
            scene.bz_pilot_armature = armature
            _validate_armature_against_stock(armature, stock)
            plan = _selected_plan(scene)
            _prepare_output_path(scene, stock_path)
            profile = _profile_from_stock(stock)
            message = (
                f"Ready: {profile['label']}, {len(plan)} clip(s), "
                f"{len(stock.bone_map)} OGRE bones, bake {BAKE_FPS} FPS."
            )
            _set_status(scene, message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except Exception as exc:
            _set_status(scene, f"ERROR: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class BZ98TOOLS_OT_pilot_export_patch(bpy.types.Operator):
    bl_idname = "bz.pilot_export_patch"
    bl_label = "Bake + Export Animation Patch"
    bl_description = "Bake selected Actions at 30 FPS and replace only those named clips in a stock-compatible pilot skeleton"

    def execute(self, context):
        scene = context.scene
        try:
            stock_path, stock = _load_stock_skeleton(scene)
            armature = _resolve_armature(context)
            if armature is None:
                raise patch_backend.PilotAnimationPatchError(
                    "Choose or select the imported stock pilot armature."
                )
            scene.bz_pilot_armature = armature
            _validate_armature_against_stock(armature, stock)
            plan = _selected_plan(scene)
            output_path = _prepare_output_path(scene, stock_path)

            with tempfile.TemporaryDirectory(prefix="bz_pilot_anim_") as temp_dir:
                replacement = _build_baked_replacement(
                    context, self, armature, plan, temp_dir
                )
                _write_patched_output(
                    stock,
                    replacement,
                    plan,
                    output_path,
                    scene.bz_pilot_bind_tolerance,
                )

            scene.bz_pilot_last_export_path = output_path
            profile = _profile_from_stock(stock)
            clips = ", ".join(clip_name for clip_name, _ in plan)
            message = (
                f"Exported {len(plan)} patched clip(s) for {profile['label']}: {clips}"
            )
            _set_status(scene, message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except Exception as exc:
            _set_status(scene, f"ERROR: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class BZ98TOOLS_PT_view3d_pilot_animation_patch(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_PILOT_ANIMATION_PATCH"
    bl_label = "Pilot Animation Patch"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        intro = layout.box()
        intro.label(text="Redux Pilot Skeletal Animation", icon="ARMATURE_DATA")
        intro.label(text="Stock skeleton stays authoritative.")
        intro.label(text="Mapped Actions are visually baked at 30 FPS.")
        intro.label(text="Retarget Actions to the stock armature before export.")

        rig_box = layout.box()
        rig_box.label(text="1. Pilot Rig", icon="OUTLINER_OB_ARMATURE")
        rig_box.prop(scene, "bz_pilot_armature", text="Armature")
        if scene.bz_pilot_profile:
            rig_box.label(text=f"Stock Profile: {scene.bz_pilot_profile}")
        if scene.bz_pilot_serializer_version:
            rig_box.label(
                text=(
                    f"Serializer: {scene.bz_pilot_serializer_version}  |  "
                    f"Bones: {scene.bz_pilot_stock_bone_count}"
                )
            )

        stock_box = layout.box()
        stock_box.label(text="2. Stock Contract", icon="LOCKED")
        stock_box.prop(scene, "bz_pilot_stock_skeleton_path", text="Stock .skeleton")
        stock_box.operator(
            "bz.pilot_refresh_clips", text="Load / Refresh Stock Clips", icon="FILE_REFRESH"
        )
        stock_box.prop(scene, "bz_pilot_output_skeleton_path", text="Output .skeleton")
        stock_box.prop(scene, "bz_pilot_overwrite_output")

        clips_box = layout.box()
        clips_box.label(text="3. Clip Mapping", icon="ACTION")
        if len(scene.bz_pilot_animation_clips) == 0:
            clips_box.label(text="Load the stock skeleton to populate clips.", icon="INFO")
        else:
            clips_box.template_list(
                "BZ98TOOLS_UL_pilot_animation_clips",
                "",
                scene,
                "bz_pilot_animation_clips",
                scene,
                "bz_pilot_animation_active_index",
                rows=8,
            )
            row = clips_box.row(align=True)
            row.operator("bz.pilot_auto_map_actions", text="Auto-map")
            op = row.operator("bz.pilot_select_clips", text="Mapped")
            op.mode = "MAPPED"
            op = row.operator("bz.pilot_select_clips", text="None")
            op.mode = "NONE"

            row = clips_box.row(align=True)
            row.operator("bz.pilot_use_active_action", text="Use Active Action")
            row.operator("bz.pilot_preview_action", text="Preview")

            index = scene.bz_pilot_animation_active_index
            if 0 <= index < len(scene.bz_pilot_animation_clips):
                entry = scene.bz_pilot_animation_clips[index]
                detail = clips_box.box()
                detail.label(text=f"Target: {entry.clip_name}")
                detail.prop(entry, "action", text="Source Action")
                detail.prop(entry, "selected", text="Replace on Export")
                detail.label(
                    text=(
                        f"Stock: {entry.stock_duration:.3f}s, "
                        f"{entry.stock_track_count} tracks"
                    )
                )

        export_box = layout.box()
        export_box.label(text="4. Validate + Export", icon="EXPORT")
        export_box.prop(scene, "bz_pilot_bind_tolerance", text="Bind Tolerance")
        row = export_box.row(align=True)
        row.operator("bz.pilot_validate_patch", text="Validate", icon="CHECKMARK")
        row.operator(
            "bz.pilot_export_patch",
            text="Bake + Export Patch",
            icon="ARMATURE_DATA",
        )

        if scene.bz_pilot_last_status:
            status = layout.box()
            icon = "ERROR" if scene.bz_pilot_last_status.startswith("ERROR:") else "INFO"
            status.label(text=scene.bz_pilot_last_status, icon=icon)
        if scene.bz_pilot_last_export_path:
            layout.label(
                text=f"Last output: {scene.bz_pilot_last_export_path}", icon="FILE_TICK"
            )


_CLASSES = (
    BZPilotAnimationClipEntry,
    BZ98TOOLS_UL_pilot_animation_clips,
    BZ98TOOLS_OT_pilot_refresh_clips,
    BZ98TOOLS_OT_pilot_auto_map_actions,
    BZ98TOOLS_OT_pilot_select_clips,
    BZ98TOOLS_OT_pilot_use_active_action,
    BZ98TOOLS_OT_pilot_preview_action,
    BZ98TOOLS_OT_pilot_validate_patch,
    BZ98TOOLS_OT_pilot_export_patch,
    BZ98TOOLS_PT_view3d_pilot_animation_patch,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.bz_pilot_armature = bpy.props.PointerProperty(
        name="Pilot Armature",
        description="Imported stock pilot armature used as the target animation rig",
        type=bpy.types.Object,
    )
    bpy.types.Scene.bz_pilot_stock_skeleton_path = bpy.props.StringProperty(
        name="Stock Pilot Skeleton",
        description="Original unmodified Redux pilot .skeleton used as the authoritative contract",
        subtype="FILE_PATH",
        default="",
    )
    bpy.types.Scene.bz_pilot_output_skeleton_path = bpy.props.StringProperty(
        name="Patched Pilot Skeleton",
        description="New .skeleton to write; the stock input is never overwritten",
        subtype="FILE_PATH",
        default="",
    )
    bpy.types.Scene.bz_pilot_overwrite_output = bpy.props.BoolProperty(
        name="Overwrite Output",
        description="Allow replacing an existing non-stock output skeleton",
        default=True,
    )
    bpy.types.Scene.bz_pilot_bind_tolerance = bpy.props.FloatProperty(
        name="Bind Tolerance",
        description="Maximum floating-point drift accepted when comparing the baked skeleton bind pose to stock",
        default=1.0e-4,
        min=1.0e-7,
        max=1.0e-2,
        precision=6,
    )
    bpy.types.Scene.bz_pilot_animation_clips = bpy.props.CollectionProperty(
        type=BZPilotAnimationClipEntry
    )
    bpy.types.Scene.bz_pilot_animation_active_index = bpy.props.IntProperty(
        name="Pilot Clip", default=0
    )
    bpy.types.Scene.bz_pilot_profile = bpy.props.StringProperty(
        name="Pilot Profile", default=""
    )
    bpy.types.Scene.bz_pilot_serializer_version = bpy.props.StringProperty(
        name="Pilot Serializer", default=""
    )
    bpy.types.Scene.bz_pilot_stock_bone_count = bpy.props.IntProperty(
        name="Pilot Bone Count", default=0
    )
    bpy.types.Scene.bz_pilot_last_status = bpy.props.StringProperty(
        name="Pilot Patch Status", default=""
    )
    bpy.types.Scene.bz_pilot_last_export_path = bpy.props.StringProperty(
        name="Last Pilot Patch", default=""
    )


def unregister():
    for prop_name in (
        "bz_pilot_armature",
        "bz_pilot_stock_skeleton_path",
        "bz_pilot_output_skeleton_path",
        "bz_pilot_overwrite_output",
        "bz_pilot_bind_tolerance",
        "bz_pilot_animation_clips",
        "bz_pilot_animation_active_index",
        "bz_pilot_profile",
        "bz_pilot_serializer_version",
        "bz_pilot_stock_bone_count",
        "bz_pilot_last_status",
        "bz_pilot_last_export_path",
    ):
        if hasattr(bpy.types.Scene, prop_name):
            delattr(bpy.types.Scene, prop_name)

    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
