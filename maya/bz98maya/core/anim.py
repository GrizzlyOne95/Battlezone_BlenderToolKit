"""Legacy VDF / SDF ANIM blocks <-> neutral animation (Maya-free).

An ANIM block holds animation *elements* (slots: index, start, length, loop, speed and a 32-int
geo mask) and, per part, rotation / position / scale ("translation2") keys at integer frames.
Keys are absolute local transforms of the part (they replace its record matrix while playing).

Neutral form: ``Scene.meta["animations"]`` is the slot table; ``Node.anim`` is
``{"rot": [(frame, (w, x, y, z))], "pos": [(frame, (x, y, z))], "scale": [(frame, (x, y, z))]}``
in Maya space, plus ``Node.props["anim_orientation_flags"]``.

Conventions, derived from the toolkit's importer / exporter (``import_vdf.py`` / ``export_vdf.py``):

* position keys are BZ local positions, so Maya ``translate = (x, y, -z)`` (``axes.bz_point_to_maya``);
* scale keys are per-axis factors and need no mirror: Maya ``scale = (x, y, z)``;
* a rotation key stores the quaternion of the *row* matrix of the rotation (the same 3x3 the
  record matrix holds in its first nine floats), ``(w, x, y, z)`` as written by
  ``Euler(...).to_quaternion()``. The Maya rotation follows by building that matrix and running it
  through the verified matrix conversion in ``axes`` (rows are the basis vectors).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import axes
from .model import Node, Scene


# --- rotation conversion -----------------------------------------------------------

def quat_to_matrix(q: Sequence[float]) -> np.ndarray:
    w, x, y, z = (float(v) for v in q)
    n = w * w + x * x + y * y + z * z
    if n == 0.0:
        return np.identity(3)
    s = 2.0 / n
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
    ])


def matrix_to_quat(m) -> Tuple[float, float, float, float]:
    m = np.array(m, dtype=float)
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = np.sqrt(1.0 + m[i, i] - m[j, j] - m[k, k]) * 2
        q = [0.0] * 4
        q[0] = (m[k, j] - m[j, k]) / s
        q[i + 1] = 0.25 * s
        q[j + 1] = (m[j, i] + m[i, j]) / s
        q[k + 1] = (m[k, i] + m[i, k]) / s
        w, x, y, z = q
    return (float(w), float(x), float(y), float(z))


def stored_to_maya_quat(stored: Sequence[float]) -> Tuple[float, float, float, float]:
    """A VDF/SDF rotation key (w, x, y, z) -> the local rotation in Maya space (w, x, y, z)."""
    rows = quat_to_matrix(stored)  # row i = image of local axis i, as in the record matrix
    maya = np.array(axes.bz_matrix12_to_maya(list(rows.reshape(9)) + [0.0, 0.0, 0.0]))[:3, :3]
    return matrix_to_quat(maya)


def maya_to_stored_quat(q: Sequence[float]) -> Tuple[float, float, float, float]:
    """Inverse of ``stored_to_maya_quat``."""
    maya = np.identity(4)
    maya[:3, :3] = quat_to_matrix(q)
    return matrix_to_quat(np.array(axes.maya_matrix_to_bz12(maya.tolist())[:9]).reshape(3, 3))


def quats_close(a: Sequence[float], b: Sequence[float], tol: float = 1e-5) -> bool:
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    return bool(min(np.abs(a - b).max(), np.abs(a + b).max()) <= tol)


# --- read ----------------------------------------------------------------------------

def element_dict(element) -> Dict[str, object]:
    return {
        "index": int(element.index),
        "start": int(element.start),
        "length": int(element.length),
        "loop": int(element.loop),
        "speed": float(element.speed),
        "mask": [int(v) for v in element.unknowngeoflag],
    }


def read_anim(scene: Scene, header, elements, orientations, rotations, translations2, positions) -> None:
    """Fill ``scene.meta`` and the nodes' ``anim`` from the parsed ANIM block."""
    scene.meta["animations"] = [element_dict(e) for e in elements]
    scene.meta["use_advanced_anim_header"] = True
    scene.meta["anim_null2"] = int(header.null2)
    scene.meta["anim_unknown2"] = int(header.unknown2)
    scene.meta["anim_reserved"] = [int(v) for v in header._reserved[:5]]
    scene.meta["use_translation2"] = bool(translations2)
    by_name = {n.name.lower(): n for n in scene.nodes}
    for orientation in orientations:
        node = by_name.get(orientation.name.lower())
        if node is None:
            continue
        node.props["anim_orientation_flags"] = int(getattr(orientation, "unknown", 0))
        keys: Dict[str, list] = {"rot": [], "pos": [], "scale": []}
        for i in range(orientation.rotationindex, orientation.rotationindex + orientation.rotationcount):
            if 0 <= i < len(rotations):
                keys["rot"].append((int(rotations[i].frame), stored_to_maya_quat(rotations[i].translate)))
        for i in range(orientation.positionindex, orientation.positionindex + orientation.positioncount):
            if 0 <= i < len(positions):
                keys["pos"].append((int(positions[i].frame), axes.bz_point_to_maya(positions[i].translate)))
        for i in range(orientation.translation2index, orientation.translation2index + orientation.translation2count):
            if 0 <= i < len(translations2):
                t = translations2[i].translate
                keys["scale"].append((int(translations2[i].frame), (float(t[0]), float(t[1]), float(t[2]))))
        if any(keys.values()):
            node.anim = keys


# --- write ---------------------------------------------------------------------------

def build_anim(layout_entries: Dict[str, Node], matrices: Dict[str, list], meta: Dict[str, object],
               classes, name: str, warnings: Optional[List[str]] = None):
    """Records for the ANIM block: (header, elements, orientations, rotations, translation2, positions).

    ``classes`` is the module (``vdf_classes`` or ``sdf_classes``) providing the ANIM* section classes.
    ``matrices`` maps part name -> the 12-float record matrix (the orientation's ``matrix2``).
    Returns None when there are no animation elements (nothing to write, as in Blender).
    """
    elements_meta = meta.get("animations") or []
    if not elements_meta:
        return None
    header = classes.ANIMHeader()
    header.name = (name[:15] + ".")[:16]
    if meta.get("use_advanced_anim_header"):
        header.null2 = int(meta.get("anim_null2", 0))
        header.unknown2 = int(meta.get("anim_unknown2", 0))
        header._reserved = [int(v) for v in (list(meta.get("anim_reserved") or []) + [0] * 5)[:5]]
    use_t2 = bool(meta.get("use_translation2"))

    elements = []
    for item in elements_meta:
        element = classes.ANIMElement()
        element.index = int(item["index"])
        element.start = int(item.get("start", 0))
        element.length = int(item.get("length", 0))
        element.loop = int(item.get("loop", 1))
        element.speed = float(item.get("speed", 15.0))
        mask = [int(v) for v in item.get("mask") or []]
        element.unknowngeoflag = (mask + [0] * 32)[:32] if mask else ([1] * 32 if element.index in (0, 1) else [0] * 32)
        elements.append(element)

    orientations, rotations, translations2, positions = [], [], [], []
    for name_key, node in layout_entries.items():
        if name_key not in matrices:  # not written as a record (no slot)
            continue
        anim = node.anim or {}
        orientation = classes.ANIMOrientation()
        orientation.name = name_key
        orientation.unknown = int((node.props or {}).get("anim_orientation_flags", 0))
        orientation.matrix1 = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        orientation.matrix2 = list(matrices[name_key])
        rot_keys = sorted(anim.get("rot", []), key=lambda k: k[0])
        pos_keys = sorted(anim.get("pos", []), key=lambda k: k[0])
        scale_keys = sorted(anim.get("scale", [])) if use_t2 else []
        if anim.get("scale") and not use_t2 and warnings is not None:  # export_vdf.py:578: only written with the Translation2 track on
            warnings.append("%s: scale keys are not written (enable use_translation2)" % name_key)
        orientation.rotationindex = len(rotations) if rot_keys else 0
        orientation.rotationcount = len(rot_keys)
        orientation.positionindex = len(positions) if pos_keys else 0
        orientation.positioncount = len(pos_keys)
        orientation.translation2index = len(translations2) if scale_keys else 0
        orientation.translation2count = len(scale_keys)
        for frame, q in rot_keys:
            key = classes.ANIMRotation()
            key.frame = int(frame)
            key.translate = maya_to_stored_quat(q)
            rotations.append(key)
        for frame, p in pos_keys:
            key = classes.ANIMPosition()
            key.frame = int(frame)
            key.translate = axes.maya_point_to_bz(p)
            positions.append(key)
        for frame, s in scale_keys:
            key = classes.ANIMTranslation2()
            key.frame = int(frame)
            key.translate = tuple(float(v) for v in s)
            translations2.append(key)
        orientations.append(orientation)
    return header, elements, orientations, rotations, translations2, positions


# --- Blender's animation tools (data operations) ------------------------------------------

def presets() -> Dict[str, List[int]]:
    """Blender's ANIMATION_PRESET_SLOTS (preset name -> slot indices)."""
    from . import schema

    return {k: list(v) for k, v in (schema.blender_constant("ANIMATION_PRESET_SLOTS") or {}).items()}


def preset_items() -> List[Tuple[str, str, str]]:
    from . import schema

    return [tuple(i[:3]) for i in (schema.blender_constant("ANIMATION_PRESET_ITEMS") or [])]


def apply_preset(elements: List[dict], preset: str) -> List[dict]:
    """Blender's Add Animation Preset: append slots (start 0, length 0, loop 1, speed 15)."""
    indices = presets().get(preset, [])
    if not indices:
        raise KeyError("unknown animation preset %s" % preset)
    out = list(elements)
    for index in indices:
        out.append({"index": int(index), "start": 0, "length": 0, "loop": 1, "speed": 15.0, "mask": [1] * 32 if index in (0, 1) else [0] * 32})
    return out


def duplicate_element(elements: List[dict], position: int) -> List[dict]:
    out = list(elements)
    out.insert(position + 1, dict(out[position], mask=list(out[position].get("mask") or [])))
    return out


def move_element(elements: List[dict], position: int, direction: str) -> Tuple[List[dict], int]:
    out = list(elements)
    target = position - 1 if direction == "UP" else position + 1
    if not 0 <= target < len(out):
        return out, position
    out[position], out[target] = out[target], out[position]
    return out, target


def reference_text() -> str:
    """Blender's Animation Index Reference popup, as text (the label strings of its draw function)."""
    from . import schema

    return "\n".join(schema.function_label_texts("draw_anim_index_reference_popup"))


def replace_name_token(name: str, source: str, target: str) -> str:
    """Blender's mirror token swap for matching left / right parts (_replace_name_token)."""
    from . import schema

    return schema.blender_function("_replace_name_token")(name, source, target)


def _slerp(a, b, t):
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    dot = float(np.dot(a, b))
    if dot < 0.0:
        b, dot = -b, -dot
    if dot > 0.9995:
        out = a + t * (b - a)
    else:
        theta = np.arccos(dot)
        out = (np.sin((1 - t) * theta) * a + np.sin(t * theta) * b) / np.sin(theta)
    return tuple(float(v) for v in out / np.linalg.norm(out))


def eval_keys(series, frame, default, is_quat=False):
    """Value of a key series at ``frame``: held outside the range, linear (slerp for rotations) inside."""
    if not series:
        return default
    series = sorted(series, key=lambda k: k[0])
    if frame <= series[0][0]:
        return series[0][1]
    if frame >= series[-1][0]:
        return series[-1][1]
    for (f0, v0), (f1, v1) in zip(series, series[1:]):
        if f0 <= frame <= f1:
            t = (frame - f0) / float(f1 - f0) if f1 != f0 else 0.0
            if is_quat:
                return _slerp(v0, v1, t)
            return tuple(float(a + t * (b - a)) for a, b in zip(v0, v1))
    return series[-1][1]


def compose(pos, rot, scale) -> np.ndarray:
    m = np.identity(4)
    m[:3, :3] = quat_to_matrix(rot) @ np.diag([float(v) for v in scale])
    m[:3, 3] = [float(v) for v in pos]
    return m


def decompose(m) -> Dict[str, tuple]:
    m = np.array(m, dtype=float)
    scale = np.linalg.norm(m[:3, :3], axis=0)
    rot = m[:3, :3] / np.where(scale > 0, scale, 1.0)
    return {"pos": tuple(m[:3, 3]), "rot": matrix_to_quat(rot), "scale": tuple(scale)}


def local_matrix_at(keys: Dict[str, list], frame: float, static: Dict[str, tuple]) -> np.ndarray:
    """Local matrix of a part at ``frame``: keyed channel groups evaluated, the rest from ``static``."""
    return compose(
        eval_keys(keys.get("pos"), frame, static["pos"]),
        eval_keys(keys.get("rot"), frame, static["rot"], True),
        eval_keys(keys.get("scale"), frame, static["scale"]),
    )


def mirror_keys_matrix(keys, frames, axis, include, rest_frame, source_rest, target_rest) -> Dict[str, list]:
    """Blender's mirror: ``target = T_rest . inv(M S_rest M) . (M S_basis M)`` at every keyed frame.

    ``source_rest`` / ``target_rest`` are the local matrices at the rest frame; ``M`` flips ``axis``.
    """
    index = "XYZ".index(axis.upper())
    mirror = np.identity(4)
    mirror[index, index] = -1.0
    source_rest = np.array(source_rest, dtype=float)
    rest_inverse = np.linalg.inv(mirror @ source_rest @ mirror)
    static = decompose(source_rest)
    out: Dict[str, list] = {g: [] for g in include}
    previous = None
    for frame in frames:
        basis = local_matrix_at(keys, frame, static)
        parts = decompose(np.array(target_rest, dtype=float) @ (rest_inverse @ (mirror @ basis @ mirror)))
        if "pos" in include:
            out["pos"].append((frame, parts["pos"]))
        if "rot" in include:
            q = parts["rot"]
            if previous is not None and float(np.dot(q, previous)) < 0.0:
                q = tuple(-v for v in q)
            previous = q
            out["rot"].append((frame, q))
        if "scale" in include:
            out["scale"].append((frame, parts["scale"]))
    return out


# --- Blender's pilot-animation helpers that need no Blender (Load Stock Clips, Auto-map) --------

def stock_clip_table(path: str) -> List[Tuple[str, float, int]]:
    """Blender's Load Stock Clips: ``(clip name, duration, track count)`` of a stock ``.skeleton``."""
    from . import skeleton_io

    return [(c.name, float(c.length), len(c.tracks)) for c in skeleton_io.read_skeleton(path).clips]


def auto_map(clip_names: Sequence[str], available: Sequence[str]) -> Dict[str, Optional[str]]:
    """Blender's Auto-map Actions: stock clip -> the first available name that matches it.

    Case-insensitive exact / alias match, tolerating a trailing ``.001`` (the stock
    idleEject / idleElect spelling variant included); None when nothing matches.
    """
    from .toolkit import ensure_toolkit

    ensure_toolkit()
    from bz98tools import pilot_animation_profiles as profiles

    out: Dict[str, Optional[str]] = {}
    for clip in clip_names:
        out[clip] = next((a for a in available if profiles.action_name_matches_clip(clip, a)), None)
    return out
