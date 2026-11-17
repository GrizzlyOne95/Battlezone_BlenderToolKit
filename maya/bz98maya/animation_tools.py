"""Blender's legacy animation tools on the Maya scene (needs Maya).

The slot table lives on the model group (``animations`` in its extra data); the operations on
it are the pure functions in ``core/anim.py``. Mirror Legacy Animation Keys works on the part
transforms' keys (``legacy_anim``).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import maya.cmds as cmds

from . import attributes, legacy_anim
from .core import anim


def slots(model: str) -> List[dict]:
    return [dict(item) for item in (attributes.read_model(model).get("animations") or [])]


def set_slots(model: str, elements: List[dict]) -> None:
    meta = attributes.read_model(model)
    meta["animations"] = elements
    attributes.write_model(model, meta)


def add_preset(model: str, preset: str) -> List[dict]:
    result = anim.apply_preset(slots(model), preset)
    set_slots(model, result)
    return result


def add_slot(model: str, index: int = 0) -> List[dict]:
    result = slots(model) + [{"index": int(index), "start": 0, "length": 0, "loop": 1, "speed": 15.0,
                              "mask": [1] * 32 if index in (0, 1) else [0] * 32}]
    set_slots(model, result)
    return result


def remove_slot(model: str, position: int) -> List[dict]:
    result = slots(model)
    if 0 <= position < len(result):
        del result[position]
    set_slots(model, result)
    return result


def duplicate_slot(model: str, position: int) -> List[dict]:
    result = anim.duplicate_element(slots(model), position)
    set_slots(model, result)
    return result


def move_slot(model: str, position: int, direction: str) -> int:
    result, at = anim.move_element(slots(model), position, direction)
    set_slots(model, result)
    return at


def update_slot(model: str, position: int, **changes) -> List[dict]:
    result = slots(model)
    result[position].update(changes)
    set_slots(model, result)
    return result


def _legacy_part_transforms() -> List[str]:
    return [t for t in (cmds.ls(type="transform", long=True) or []) if attributes.is_part(t)]


def _short(transform: str) -> str:
    return transform.split("|")[-1].split(":")[-1]


def mirror_keys(source_token: str = ".L", target_token: str = ".R", axis: str = "X",
                include: Sequence[str] = ("pos", "rot"), frame_range=None, rest_frame: int = 0,
                overwrite: bool = True, pair: Optional[Sequence[str]] = None,
                use_selection: bool = True) -> int:
    """Blender's Mirror Legacy Animation Keys.

    ``pair`` = (source, target) transforms mirrors exactly that pair. Otherwise every animated
    part (the selection with its descendants, or the whole scene) whose name holds
    ``source_token`` is mirrored onto the part named with ``target_token`` instead. Returns the
    number of parts written.
    """
    if not include:
        raise ValueError("Enable at least one transform channel.")
    if pair:
        pairs = [(pair[0], pair[1])]
    else:
        if not source_token:
            raise ValueError("Source token cannot be blank.")
        by_name: Dict[str, str] = {_short(t): t for t in _legacy_part_transforms()}
        selected = cmds.ls(selection=True, long=True) if use_selection else []
        if selected:
            candidates = cmds.ls(selected, dag=True, long=True, type="transform") or []
        else:
            candidates = list(by_name.values())
        pairs = []
        for source in candidates:
            name = _short(source)
            target_name = anim.replace_name_token(name, source_token, target_token)
            if not target_name or target_name == name or target_name not in by_name:
                continue
            pairs.append((source, by_name[target_name]))
    written = 0
    for source, target in pairs:
        if legacy_anim.collect_keys(source) is None:
            continue
        if legacy_anim.mirror_keys(source, target, axis, tuple(include), frame_range, rest_frame, overwrite):
            written += 1
    if not written:
        raise RuntimeError("No matching animated source/target pairs were found.")
    return written
