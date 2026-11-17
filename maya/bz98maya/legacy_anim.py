"""Legacy VDF / SDF animation on part transforms (needs Maya).

Keys sit on the part transform's own channels, at the file's integer frames (Maya's timeline
frames): ``translate`` for position keys, ``rotate`` (Euler, linear tangents, unrolled) for
rotation keys and ``scale`` for scale ("translation2") keys. The slot table (animation
elements: index, start, length, loop, speed, geo mask) is ``meta["animations"]`` on the model
group; see ``core/anim.py`` for the conversions.

Export reads the keys back: the key frames of each channel group, with the other channels of
the group evaluated on those frames. Animated parts should be direct children of exported
parts (keys are local values of the part itself, not composed through skipped groups).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from . import rig
from .core import anim, axes

GROUPS = {
    "pos": ("translateX", "translateY", "translateZ"),
    "rot": ("rotateX", "rotateY", "rotateZ"),
    "scale": ("scaleX", "scaleY", "scaleZ"),
}


def _plug(node: str, attr: str) -> "om.MPlug":
    selection = om.MSelectionList()
    selection.add(node)
    return om.MFnDependencyNode(selection.getDependNode(0)).findPlug(attr, False)


def _frame(frame: float) -> "om.MTime":
    return om.MTime(float(frame), om.MTime.uiUnit())


def apply_keys(transform: str, keys: Dict[str, list]) -> None:
    """Key ``transform`` with neutral ANIM keys (Maya space), replacing any existing animation."""
    order = cmds.getAttr(transform + ".rotateOrder")
    channels: Dict[str, List[Tuple[float, float]]] = {}
    for frame, (x, y, z) in keys.get("pos", []):
        for attr, value in zip(GROUPS["pos"], (x, y, z)):
            channels.setdefault(attr, []).append((frame, value))
    for frame, (x, y, z) in keys.get("scale", []):
        for attr, value in zip(GROUPS["scale"], (x, y, z)):
            channels.setdefault(attr, []).append((frame, value))
    rot_keys = keys.get("rot", [])
    eulers = rig.unrolled_eulers([om.MQuaternion(x, y, z, w) for _f, (w, x, y, z) in rot_keys], order)
    for (frame, _q), euler in zip(rot_keys, eulers):
        for attr, value in zip(GROUPS["rot"], euler):
            channels.setdefault(attr, []).append((frame, value))
    for attr, series in channels.items():
        series.sort(key=lambda item: item[0])
        times = om.MTimeArray([_frame(f) for f, _v in series])
        values = om.MDoubleArray([v for _f, v in series])
        curve = oma.MFnAnimCurve()
        curve.create(_plug(transform, attr))
        curve.addKeys(times, values, oma.MFnAnimCurve.kTangentLinear, oma.MFnAnimCurve.kTangentLinear)


def _curve(transform: str, attr: str):
    found = oma.MAnimUtil.findAnimation(_plug(transform, attr))
    return oma.MFnAnimCurve(found[0]) if len(found) else None


def _value(transform: str, attr: str, curve, frame: float) -> float:
    if curve is not None:
        return curve.evaluate(_frame(frame))
    plug = _plug(transform, attr)
    if attr.startswith("rotate"):
        return plug.asMAngle().asRadians()
    if attr.startswith("translate"):
        return plug.asMDistance().asCentimeters()
    return plug.asDouble()


def collect_keys(transform: str) -> Optional[Dict[str, list]]:
    """Neutral keys of a part transform, or None when no channel is animated."""
    out: Dict[str, list] = {}
    order = cmds.getAttr(transform + ".rotateOrder")
    for group, attrs in GROUPS.items():
        curves = {a: _curve(transform, a) for a in attrs}
        if not any(curves.values()):
            continue
        frames = sorted({round(c.input(i).asUnits(om.MTime.uiUnit())) for c in curves.values() if c
                         for i in range(c.numKeys)})
        keys = []
        for frame in frames:
            v = [_value(transform, a, curves[a], frame) for a in attrs]
            if group == "rot":
                q = om.MEulerRotation(v[0], v[1], v[2], order).asQuaternion()
                keys.append((int(frame), (q.w, q.x, q.y, q.z)))
            else:
                keys.append((int(frame), (v[0], v[1], v[2])))
        out[group] = keys
    return out or None


def set_playback_range(scene_meta_elements: List[dict], keyed_frames: List[int]) -> None:
    end = max(keyed_frames + [int(e.get("start", 0)) + abs(int(e.get("length", 0))) for e in scene_meta_elements] + [1])
    cmds.playbackOptions(minTime=0, maxTime=end)


def clear_keys(transform: str, start: Optional[float] = None, end: Optional[float] = None) -> None:
    for attrs in GROUPS.values():
        for attr in attrs:
            if start is None:
                cmds.cutKey(transform, attribute=attr, clear=True)
            else:
                cmds.cutKey(transform, attribute=attr, time=(start, end), clear=True)


# --- Blender's "Mirror Legacy Animation Keys" ------------------------------------------

def mirror_keys(source: str, target: str, axis: str = "X", include=("pos", "rot", "scale"),
                frame_range: Optional[Tuple[int, int]] = None, rest_frame: int = 0,
                overwrite: bool = True) -> int:
    """Copy ``source``'s keys onto ``target`` mirrored across a local axis, relative to their rest poses.

    As in Blender: the target's rest matrix is carried by the source motion mirrored about
    ``axis`` (``target = T_rest * (M S_rest M)^-1 * M S M`` per keyed frame). Returns the number
    of keyed frames written.
    """
    keys = collect_keys(source)
    if not keys:
        return 0
    frames = sorted({f for group in include for f, _v in keys.get(group, [])})
    if frame_range:
        frames = [f for f in frames if frame_range[0] <= f <= frame_range[1]]
    if not frames:
        return 0
    result = anim.mirror_keys_matrix(
        keys, frames, axis, include, rest_frame,
        _rest_matrix(source, rest_frame, keys), _rest_matrix(target, rest_frame, collect_keys(target) or {}),
    )
    if overwrite:
        clear_keys(target, frames[0], frames[-1])
    apply_keys(target, result)
    return len(frames)


def _rest_matrix(transform: str, frame: int, keys: Dict[str, list]):
    """Local matrix (column vectors) of ``transform`` at ``frame`` from its keys / static values."""
    return anim.local_matrix_at(keys, frame, _static_trs(transform))


def _static_trs(transform: str):
    selection = om.MSelectionList()
    selection.add(transform)
    fn = om.MFnTransform(selection.getDagPath(0))
    t = fn.translation(om.MSpace.kTransform)
    q = fn.rotation(om.MSpace.kTransform, asQuaternion=True)
    s = fn.scale()
    return {"pos": (t.x, t.y, t.z), "rot": (q.w, q.x, q.y, q.z), "scale": tuple(s)}
