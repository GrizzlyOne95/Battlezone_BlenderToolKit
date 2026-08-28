# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Build one export-ready Blender armature from the BZ2 Jak animation FBXs.

This module is deliberately a Blender-side asset preparation step rather than
part of the stock pilot skeleton patcher.  It consolidates the original Jak
FBX animation set onto the fixed ``jak_walk.fbx`` bind pose, bakes the older
``jak_skel.fbx`` idle onto that rig in armature/world space, creates the
logical aliases used by the original BZ2 ODF, and lays the resulting Actions
out as muted NLA tracks ready for the native Ogre exporter.

The public ``build_jak_animation_set`` function is intended to become the
backend for a future toolkit panel.  ``scripts/build_jak_pilot_asset.py`` is a
thin command-line wrapper for qualification before that UI is wired in.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:  # Allows source-level tests outside Blender.
    import bpy
except ImportError:  # pragma: no cover - exercised only outside Blender.
    bpy = None


BAKE_FPS = 30
CANONICAL_FILE = "jak_walk.fbx"
IDLE_FILE = "jak_skel.fbx"

# These are the six fixed-animation FBXs whose bind pose matches jak_walk.fbx.
DIRECT_CLIPS: Tuple[Tuple[str, str], ...] = (
    ("walk", "jak_walk.fbx"),
    ("attack1", "jak_attack01.fbx"),
    ("attack2", "jak_attack02.fbx"),
    ("attack3", "jak_attack03.fbx"),
    ("curious", "jak_curious.fbx"),
    ("death", "jak_death01.fbx"),
    ("eat1", "jak_eat01.fbx"),
)

# The original mcjak01.odf reused these source clips rather than shipping
# unique animation files for every logical state.
ODF_ALIASES: Mapping[str, str] = {
    "run": "walk",
    "jump": "walk",
    "attack4": "attack3",
    "eat2": "eat1",
}

# Known Redux Person/pilot compatibility names.  Do not try to guess the
# remaining stock pilot vocabulary here; PR #9 can later populate an exact
# compatibility map from the authoritative stock .skeleton.
DEFAULT_COMPAT_ALIASES: Mapping[str, str] = {
    "stand2Kneel": "idle",
    "idleParachute": "idle",
    "landParachute": "idle",
}

JAK_SIGNATURE_BONES = frozenset(
    {
        "hip",
        "head",
        "spine_1",
        "lthigh",
        "rthigh",
        "lshoulder",
        "rshoulder",
        "tail_1",
    }
)


class JakAnimationBuildError(RuntimeError):
    """Raised when the Jak source set cannot be consolidated safely."""


@dataclass(frozen=True)
class ClipReport:
    name: str
    source_file: str
    source_start: float
    source_end: float
    baked_start: int
    baked_end: int
    frames: int
    duration_seconds: float
    retargeted: bool


@dataclass(frozen=True)
class BuildReport:
    source_dir: str
    canonical_file: str
    armature_name: str
    bone_count: int
    fps: int
    clips: Tuple[ClipReport, ...]
    aliases: Mapping[str, str]

    def to_json(self) -> str:
        payload = asdict(self)
        payload["clips"] = [asdict(clip) for clip in self.clips]
        return json.dumps(payload, indent=2, sort_keys=True)


def _require_blender():
    if bpy is None:
        raise JakAnimationBuildError(
            "This operation requires Blender's Python runtime (bpy)."
        )


def parse_alias_specs(specs: Iterable[str]) -> Dict[str, str]:
    """Parse ``DEST=SOURCE`` alias specifications."""

    aliases: Dict[str, str] = {}
    for raw in specs:
        if "=" not in raw:
            raise JakAnimationBuildError(
                f"Invalid alias {raw!r}; expected DEST=SOURCE."
            )
        dest, source = (part.strip() for part in raw.split("=", 1))
        if not dest or not source:
            raise JakAnimationBuildError(
                f"Invalid alias {raw!r}; expected non-empty DEST=SOURCE."
            )
        if dest == source:
            raise JakAnimationBuildError(f"Alias {raw!r} points to itself.")
        aliases[dest] = source
    return aliases


def expected_source_files() -> Tuple[str, ...]:
    names = {IDLE_FILE}
    names.update(filename for _, filename in DIRECT_CLIPS)
    return tuple(sorted(names))


def validate_source_directory(source_dir: os.PathLike | str) -> Path:
    source = Path(source_dir).expanduser().resolve()
    if not source.is_dir():
        raise JakAnimationBuildError(f"Jak source directory does not exist: {source}")
    missing = [name for name in expected_source_files() if not (source / name).is_file()]
    if missing:
        raise JakAnimationBuildError(
            "Jak source directory is missing required FBX files: " + ", ".join(missing)
        )
    return source


def _import_fbx(path: Path):
    _require_blender()
    before = set(bpy.data.objects)
    result = bpy.ops.import_scene.fbx(
        filepath=str(path),
        use_anim=True,
        use_image_search=False,
        automatic_bone_orientation=False,
        use_prepost_rot=True,
    )
    if result != {"FINISHED"}:
        raise JakAnimationBuildError(f"Blender FBX import failed for {path}")
    imported = [obj for obj in bpy.data.objects if obj not in before]
    if not imported:
        raise JakAnimationBuildError(f"FBX import produced no objects for {path}")
    return imported


def _find_jak_armature(objects):
    candidates = []
    for obj in objects:
        if getattr(obj, "type", None) != "ARMATURE":
            continue
        names = {bone.name for bone in obj.data.bones}
        score = len(names & JAK_SIGNATURE_BONES)
        if score:
            candidates.append((score, len(names), obj))
    if not candidates:
        raise JakAnimationBuildError(
            "Imported FBX contains no armature matching the Jak bone signature."
        )
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = candidates[0]
    if best[0] != len(JAK_SIGNATURE_BONES):
        missing = sorted(JAK_SIGNATURE_BONES - {b.name for b in best[2].data.bones})
        raise JakAnimationBuildError(
            f"Best imported armature is missing Jak signature bones: {missing}"
        )
    return best[2]


def _pose_fcurve_score(action) -> int:
    return sum(1 for curve in action.fcurves if curve.data_path.startswith('pose.bones["'))


def _find_armature_action(armature):
    animdata = getattr(armature, "animation_data", None)
    if animdata is not None:
        if animdata.action is not None and _pose_fcurve_score(animdata.action):
            return animdata.action
        nla_actions = []
        for track in animdata.nla_tracks:
            for strip in track.strips:
                action = getattr(strip, "action", None)
                if action is not None:
                    score = _pose_fcurve_score(action)
                    if score:
                        nla_actions.append((score, action))
        if nla_actions:
            nla_actions.sort(key=lambda item: item[0], reverse=True)
            return nla_actions[0][1]

    raise JakAnimationBuildError(
        f"Imported armature {armature.name!r} has no attached pose-bone Action. "
        "Refusing to guess from unrelated Actions already present in the .blend."
    )


def _pose_action_frame_range(action) -> Tuple[float, float]:
    starts: List[float] = []
    ends: List[float] = []
    for curve in action.fcurves:
        if not curve.data_path.startswith('pose.bones["') or not curve.keyframe_points:
            continue
        xs = [float(point.co.x) for point in curve.keyframe_points]
        starts.append(min(xs))
        ends.append(max(xs))
    if not starts:
        raise JakAnimationBuildError(f"Action {action.name!r} has no pose-bone keys.")
    return min(starts), max(ends)


def _bone_parent_map(armature) -> Dict[str, Optional[str]]:
    return {
        bone.name: (bone.parent.name if bone.parent is not None else None)
        for bone in armature.data.bones
    }


def _matrix_max_abs_delta(left, right) -> float:
    return max(abs(float(left[row][col]) - float(right[row][col])) for row in range(4) for col in range(4))


def _validate_common_rig(canonical, source, *, rest_tolerance: float = 1.0e-4):
    canonical_names = {bone.name for bone in canonical.data.bones}
    source_names = {bone.name for bone in source.data.bones}
    if canonical_names != source_names:
        raise JakAnimationBuildError(
            "Fixed Jak animation FBX bone set differs from canonical jak_walk rig "
            f"(missing={sorted(canonical_names-source_names)}, "
            f"extra={sorted(source_names-canonical_names)})."
        )

    if _bone_parent_map(canonical) != _bone_parent_map(source):
        raise JakAnimationBuildError(
            "Fixed Jak animation FBX hierarchy differs from canonical jak_walk rig."
        )

    mismatches = []
    for name in sorted(canonical_names):
        delta = _matrix_max_abs_delta(
            canonical.data.bones[name].matrix_local,
            source.data.bones[name].matrix_local,
        )
        if delta > rest_tolerance:
            mismatches.append((name, delta))
    if mismatches:
        preview = ", ".join(f"{name}:{delta:.3g}" for name, delta in mismatches[:8])
        raise JakAnimationBuildError(
            "Fixed Jak animation FBX bind pose differs from jak_walk beyond "
            f"tolerance {rest_tolerance}: {preview}"
        )


def _validate_retarget_hierarchy(canonical, source):
    canonical_names = {bone.name for bone in canonical.data.bones}
    source_names = {bone.name for bone in source.data.bones}
    if canonical_names != source_names:
        raise JakAnimationBuildError(
            "jak_skel idle retarget requires the same named bone set as jak_walk "
            f"(missing={sorted(canonical_names-source_names)}, "
            f"extra={sorted(source_names-canonical_names)})."
        )
    if _bone_parent_map(canonical) != _bone_parent_map(source):
        raise JakAnimationBuildError(
            "jak_skel idle retarget requires the same named bone hierarchy as jak_walk."
        )


def _clear_pose(armature):
    for pose_bone in armature.pose.bones:
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone.rotation_mode = "QUATERNION"
        pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pose_bone.scale = (1.0, 1.0, 1.0)


def _ordered_bone_names(armature) -> List[str]:
    def depth(bone):
        count = 0
        current = bone.parent
        while current is not None:
            count += 1
            current = current.parent
        return count

    return [bone.name for bone in sorted(armature.data.bones, key=lambda bone: (depth(bone), bone.name))]


def _sample_direct_action(context, canonical, source_action):
    animdata = canonical.animation_data_create()
    previous = animdata.action
    _clear_pose(canonical)
    animdata.action = source_action
    start, end = _pose_action_frame_range(source_action)
    first = int(round(start))
    last = int(round(end))
    samples = []
    for frame in range(first, last + 1):
        context.scene.frame_set(frame)
        context.view_layer.update()
        samples.append(
            {
                bone.name: canonical.pose.bones[bone.name].matrix_basis.copy()
                for bone in canonical.data.bones
            }
        )
    animdata.action = previous
    return start, end, samples


def _sample_retargeted_action(context, canonical, source_armature, source_action):
    source_animdata = source_armature.animation_data_create()
    source_previous = source_animdata.action
    canonical_animdata = canonical.animation_data_create()
    canonical_previous = canonical_animdata.action

    _clear_pose(source_armature)
    _clear_pose(canonical)
    source_animdata.action = source_action
    canonical_animdata.action = None

    start, end = _pose_action_frame_range(source_action)
    first = int(round(start))
    last = int(round(end))
    order = _ordered_bone_names(canonical)
    canonical_world_inverse = canonical.matrix_world.inverted_safe()
    samples = []

    try:
        for frame in range(first, last + 1):
            context.scene.frame_set(frame)
            context.view_layer.update()

            # Set each target pose bone to the source bone's evaluated world-space
            # matrix.  Parent-first assignment lets Blender solve matrix_basis
            # against the canonical (jak_walk) rest pose rather than copying raw
            # F-curves from the older jak_skel bind pose.
            for name in order:
                source_world = source_armature.matrix_world @ source_armature.pose.bones[name].matrix
                canonical.pose.bones[name].matrix = canonical_world_inverse @ source_world
            context.view_layer.update()

            samples.append(
                {
                    name: canonical.pose.bones[name].matrix_basis.copy()
                    for name in order
                }
            )
    finally:
        source_animdata.action = source_previous
        canonical_animdata.action = canonical_previous

    return start, end, samples


def _write_sampled_action(canonical, name: str, samples) -> object:
    action = bpy.data.actions.new(name=name)
    action.use_fake_user = True
    animdata = canonical.animation_data_create()
    previous = animdata.action
    animdata.action = action

    try:
        for baked_frame, frame_sample in enumerate(samples):
            for bone_name, matrix_basis in frame_sample.items():
                pose_bone = canonical.pose.bones[bone_name]
                location, rotation, scale = matrix_basis.decompose()
                rotation.normalize()
                pose_bone.rotation_mode = "QUATERNION"
                pose_bone.location = location
                pose_bone.rotation_quaternion = rotation
                pose_bone.scale = scale
                pose_bone.keyframe_insert("location", frame=baked_frame, group=bone_name)
                pose_bone.keyframe_insert(
                    "rotation_quaternion", frame=baked_frame, group=bone_name
                )
                pose_bone.keyframe_insert("scale", frame=baked_frame, group=bone_name)

        for curve in action.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"
    finally:
        animdata.action = previous
        _clear_pose(canonical)

    return action


def _remove_imported_objects(objects, *, keep: Sequence[object] = ()):
    keep_set = set(keep)
    for obj in list(objects):
        if obj in keep_set:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except ReferenceError:
            pass


def _copy_action(source_action, dest_name: str):
    action = source_action.copy()
    action.name = dest_name
    action.use_fake_user = True
    return action


def _install_nla_tracks(armature, actions: Mapping[str, object]):
    animdata = armature.animation_data_create()
    animdata.action = None
    while animdata.nla_tracks:
        animdata.nla_tracks.remove(animdata.nla_tracks[0])

    for name in actions:
        action = actions[name]
        track = animdata.nla_tracks.new()
        track.name = name
        track.mute = True
        start = int(round(float(action.frame_range[0])))
        strip = track.strips.new(name, start, action)
        strip.name = name


def _ensure_unique_action_names(names: Iterable[str]):
    seen = set()
    duplicates = []
    for name in names:
        if name in seen:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        raise JakAnimationBuildError(
            f"Duplicate destination Action names: {sorted(set(duplicates))}"
        )


def _capture_scene_state(context):
    return {
        "fps": context.scene.render.fps,
        "fps_base": context.scene.render.fps_base,
        "frame": context.scene.frame_current,
        "active": context.view_layer.objects.active,
        "selected": list(context.selected_objects),
    }


def _restore_scene_state(context, state):
    context.scene.render.fps = state["fps"]
    context.scene.render.fps_base = state["fps_base"]
    try:
        context.scene.frame_set(state["frame"])
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


def build_jak_animation_set(
    source_dir: os.PathLike | str,
    *,
    context=None,
    armature_name: str = "Jak_Armature",
    include_compat_aliases: bool = True,
    extra_aliases: Optional[Mapping[str, str]] = None,
    rest_tolerance: float = 1.0e-4,
) -> Tuple[object, BuildReport]:
    """Import and consolidate the original Jak FBX animation set.

    ``jak_walk.fbx`` is authoritative for mesh, skin and rest pose.  All fixed
    animation FBXs are validated against that bind pose before their evaluated
    pose is baked to a clean Action.  The older ``jak_skel.fbx`` idle is
    world/armature-space retargeted by bone name onto the canonical rig.

    Returns ``(canonical_armature, report)``.  The imported canonical mesh
    objects remain in the scene; temporary source FBX objects are removed.
    """

    _require_blender()
    context = context or bpy.context
    source = validate_source_directory(source_dir)
    state = _capture_scene_state(context)
    clip_reports: List[ClipReport] = []
    actions: Dict[str, object] = {}

    aliases: Dict[str, str] = dict(ODF_ALIASES)
    if include_compat_aliases:
        aliases.update(DEFAULT_COMPAT_ALIASES)
    if extra_aliases:
        aliases.update(extra_aliases)
    _ensure_unique_action_names(["idle"] + [name for name, _ in DIRECT_CLIPS] + list(aliases))

    canonical_objects = []
    canonical = None
    try:
        context.scene.render.fps = BAKE_FPS
        context.scene.render.fps_base = 1.0

        canonical_objects = _import_fbx(source / CANONICAL_FILE)
        canonical = _find_jak_armature(canonical_objects)
        canonical.name = armature_name
        canonical.data.name = f"{armature_name}_Data"

        # Bake walk from the canonical import before touching temporary clips.
        walk_source_action = _find_armature_action(canonical)
        start, end, samples = _sample_direct_action(context, canonical, walk_source_action)
        actions["walk"] = _write_sampled_action(canonical, "walk", samples)
        clip_reports.append(
            ClipReport(
                name="walk",
                source_file=CANONICAL_FILE,
                source_start=start,
                source_end=end,
                baked_start=0,
                baked_end=len(samples) - 1,
                frames=len(samples),
                duration_seconds=(len(samples) - 1) / BAKE_FPS,
                retargeted=False,
            )
        )

        # Import each remaining fixed clip, validate the common bind pose, then
        # evaluate its Action directly on the canonical armature and bake it.
        for clip_name, filename in DIRECT_CLIPS:
            if clip_name == "walk":
                continue
            imported = _import_fbx(source / filename)
            try:
                source_armature = _find_jak_armature(imported)
                _validate_common_rig(
                    canonical, source_armature, rest_tolerance=rest_tolerance
                )
                source_action = _find_armature_action(source_armature)
                start, end, samples = _sample_direct_action(
                    context, canonical, source_action
                )
                actions[clip_name] = _write_sampled_action(
                    canonical, clip_name, samples
                )
                clip_reports.append(
                    ClipReport(
                        name=clip_name,
                        source_file=filename,
                        source_start=start,
                        source_end=end,
                        baked_start=0,
                        baked_end=len(samples) - 1,
                        frames=len(samples),
                        duration_seconds=(len(samples) - 1) / BAKE_FPS,
                        retargeted=False,
                    )
                )
            finally:
                _remove_imported_objects(imported)

        # The older idle shares names/hierarchy but not the same rest pose.
        imported = _import_fbx(source / IDLE_FILE)
        try:
            idle_armature = _find_jak_armature(imported)
            _validate_retarget_hierarchy(canonical, idle_armature)
            idle_source_action = _find_armature_action(idle_armature)
            start, end, samples = _sample_retargeted_action(
                context, canonical, idle_armature, idle_source_action
            )
            actions["idle"] = _write_sampled_action(canonical, "idle", samples)
            clip_reports.append(
                ClipReport(
                    name="idle",
                    source_file=IDLE_FILE,
                    source_start=start,
                    source_end=end,
                    baked_start=0,
                    baked_end=len(samples) - 1,
                    frames=len(samples),
                    duration_seconds=(len(samples) - 1) / BAKE_FPS,
                    retargeted=True,
                )
            )
        finally:
            _remove_imported_objects(imported)

        # Match the original BZ2 logical animation table, plus only the Redux
        # compatibility aliases we have actually observed.  Additional exact
        # pilot-state aliases can be supplied later from PR #9's stock clip list.
        unresolved = []
        for dest, source_name in aliases.items():
            source_action = actions.get(source_name)
            if source_action is None:
                unresolved.append(f"{dest}={source_name}")
                continue
            actions[dest] = _copy_action(source_action, dest)
        if unresolved:
            raise JakAnimationBuildError(
                "Alias map references unavailable Actions: " + ", ".join(unresolved)
            )

        _install_nla_tracks(canonical, actions)
        canonical["BZ98R_JAK_CANONICAL"] = True
        canonical["BZ98R_JAK_SOURCE"] = str(source)
        canonical["BZ98R_JAK_FPS"] = BAKE_FPS

        report = BuildReport(
            source_dir=str(source),
            canonical_file=CANONICAL_FILE,
            armature_name=canonical.name,
            bone_count=len(canonical.data.bones),
            fps=BAKE_FPS,
            clips=tuple(clip_reports),
            aliases=dict(aliases),
        )
        canonical["BZ98R_JAK_MANIFEST"] = report.to_json()
        return canonical, report
    except Exception:
        # Preserve the canonical import on success only; failed qualification
        # should not leave a half-built animal scattered through the scene.
        if canonical_objects:
            _remove_imported_objects(canonical_objects)
        raise
    finally:
        _restore_scene_state(context, state)
