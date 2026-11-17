"""Mapping between neutral props / meta dicts and Maya attribute values (Maya-free).

``props`` is what ``vdf_io`` / ``sdf_io`` read and write per part; ``meta`` is the
per-model header. Both map to flat ``{attr_name: value}`` dicts using the schema
(``schema.py``). Keys with no schema field (``lod``, ``slot``, anything a future
file version adds) travel as ``extra`` (the Maya layer keeps them as JSON), which is
how unknown data survives a trip through Maya.

ObjectFlags: ``bzGEOFlags`` holds the raw int; the decoded bit attributes
(keep bounds, view render, destroyed, light attached, collision class, team) win for
the bits they cover, and every other bit of the raw value is preserved untouched, as
in Blender's ``apply_flag_bit`` semantics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import schema
from .toolkit import ensure_toolkit

FLAG_BITS = {
    "FlagKeepBounds": "OBJFLAG_KEEP_BOUNDS",
    "FlagViewRender": "OBJFLAG_VIEW_RENDER",
    "FlagDestroyedSeed": "OBJFLAG_DESTROYED",
    "FlagLightAttached": "OBJFLAG_LIGHT_ATTACHED",
}


def _semantics():
    ensure_toolkit()
    from bz98tools import semantics

    return semantics


def _by_key(fields) -> Dict[str, schema.Field]:
    return {f.key: f for f in fields if f.key}


def part_known_keys() -> set:
    keys = set()
    for fld in schema.part_fields():
        if fld.keys:
            keys.update(fld.keys)
        elif fld.key:
            keys.add(fld.key)
    return keys


def _scalar(fld: schema.Field, value):
    if fld.kind == "bool":
        return bool(value)
    if fld.kind in ("int", "enum"):
        return int(value)
    if fld.kind == "float":
        return float(value)
    if fld.kind == "string":
        return str(value or "")
    return value


# --- parts -------------------------------------------------------------------

def default_part_props() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for fld in schema.part_fields():
        if fld.derived or fld.default is None:
            continue
        if fld.keys:
            for key, value in zip(fld.keys, fld.default):
                out[key] = value
        else:
            out[fld.key] = fld.default
    return out


def part_attr_values(props: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(attr values, extra) for a part. Every schema attribute gets a value."""
    sem = _semantics()
    merged = default_part_props()
    merged.update({k: v for k, v in props.items() if v is not None})
    flags = int(merged.get("flags", 0))
    values: Dict[str, Any] = {}
    for fld in schema.part_fields():
        if fld.derived:
            if fld.blender in FLAG_BITS:
                values[fld.attr] = bool(flags & getattr(sem, FLAG_BITS[fld.blender]))
            elif fld.blender == "CollisionClass":
                values[fld.attr] = flags & sem.OBJFLAG_COLLISION_MASK
            elif fld.blender == "ObjFlagTeam":
                values[fld.attr] = (flags & sem.OBJFLAG_TEAM_MASK) >> 16
            continue
        if fld.keys:
            values[fld.attr] = tuple(float(merged.get(k, d)) for k, d in zip(fld.keys, fld.default))
        elif fld.kind == "float3" or fld.kind == "int3" or fld.kind == "floatarray":
            value = merged.get(fld.key, fld.default)
            values[fld.attr] = tuple(value)
        elif fld.key == "bounds_mode":
            mode = merged.get("bounds_mode", "AUTO")
            values[fld.attr] = schema.BOUNDS_MODES.index(mode) if isinstance(mode, str) else int(mode)
        elif fld.key == "flags":
            values[fld.attr] = _signed32(flags)
        elif fld.key.startswith("damage") and fld.key[-1:] in "123":
            names = merged.get("damage") or ["", "", ""]
            values[fld.attr] = str(names[int(fld.key[-1]) - 1] or "")
        else:
            values[fld.attr] = _scalar(fld, merged.get(fld.key, fld.default))
    known = part_known_keys() | {"damage"}
    extra = {k: v for k, v in props.items() if k not in known}
    return values, extra


def _signed32(value: int) -> int:
    value = int(value) & 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def part_props_from_attrs(values: Dict[str, Any], extra: Dict[str, Any] = None) -> Dict[str, Any]:
    """Props for the attributes present in ``values`` (absent ones stay unset)."""
    sem = _semantics()
    props: Dict[str, Any] = dict(extra or {})
    damage = [None, None, None]
    for fld in schema.part_fields():
        if fld.derived or fld.attr not in values:
            continue
        value = values[fld.attr]
        if fld.keys:
            for key, component in zip(fld.keys, value):
                props[key] = float(component)
        elif fld.kind in ("float3", "int3", "floatarray"):
            props[fld.key] = [float(v) if fld.kind != "int3" else int(v) for v in value]
        elif fld.key == "bounds_mode":
            props[fld.key] = schema.BOUNDS_MODES[int(value)] if 0 <= int(value) < 3 else "AUTO"
        elif fld.key.startswith("damage") and fld.key[-1:] in "123":
            damage[int(fld.key[-1]) - 1] = str(value or "")
        else:
            props[fld.key] = _scalar(fld, value)
    if any(d is not None for d in damage):
        props["damage"] = [d or "" for d in damage]

    flags_present = "bzGEOFlags" in values or any(
        f.attr in values for f in schema.part_fields() if f.derived
    )
    if flags_present:
        flags = int(values.get("bzGEOFlags", props.get("flags", 0))) & 0xFFFFFFFF
        for fld in schema.part_fields():
            if not fld.derived or fld.attr not in values:
                continue
            if fld.blender in FLAG_BITS:
                flags = sem.apply_flag_bit(flags, getattr(sem, FLAG_BITS[fld.blender]), values[fld.attr])
            elif fld.blender == "CollisionClass":
                flags = (flags & ~sem.OBJFLAG_COLLISION_MASK) | (int(values[fld.attr]) & sem.OBJFLAG_COLLISION_MASK)
            elif fld.blender == "ObjFlagTeam":
                flags = (flags & ~sem.OBJFLAG_TEAM_MASK) | ((int(values[fld.attr]) << 16) & sem.OBJFLAG_TEAM_MASK)
        props["flags"] = _signed32(flags)
    return props


# --- model header ------------------------------------------------------------

def default_meta() -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    for fld in schema.model_fields():
        if fld.key.startswith("lod") and fld.key[3:].isdigit():
            continue
        if fld.default is not None:
            meta[fld.key] = fld.default
    meta["lod_dists"] = [
        float(next(f.default for f in schema.model_fields() if f.key == "lod%d" % i)) for i in range(1, 6)
    ]
    return meta


def meta_attr_values(meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    merged = default_meta()
    merged.update({k: v for k, v in meta.items() if v is not None})
    dists = list(merged.get("lod_dists") or [])
    dists += [float(f.default) for f in schema.model_fields() if f.key.startswith("lod") and f.key[3:].isdigit()][len(dists):]
    values: Dict[str, Any] = {}
    known = {"lod_dists"}
    for fld in schema.model_fields():
        known.add(fld.key)
        if fld.key.startswith("lod") and fld.key[3:].isdigit():
            values[fld.attr] = float(dists[int(fld.key[3:]) - 1])
        elif fld.kind == "int3":
            values[fld.attr] = tuple(int(v) for v in merged.get(fld.key, fld.default))
        else:
            values[fld.attr] = _scalar(fld, merged.get(fld.key, fld.default))
    extra = {k: v for k, v in meta.items() if k not in known}
    return values, extra


def meta_from_attr_values(values: Dict[str, Any], extra: Dict[str, Any] = None) -> Dict[str, Any]:
    meta: Dict[str, Any] = dict(extra or {})
    dists = []
    for fld in schema.model_fields():
        if fld.attr not in values:
            continue
        value = values[fld.attr]
        if fld.key.startswith("lod") and fld.key[3:].isdigit():
            dists.append((int(fld.key[3:]), float(value)))
        elif fld.kind == "int3":
            meta[fld.key] = [int(v) for v in value]
        else:
            meta[fld.key] = _scalar(fld, value)
    if dists:
        base = default_meta()["lod_dists"]
        for index, value in dists:
            base[index - 1] = value
        meta["lod_dists"] = base
    return meta


def fields_by_attr(fields) -> Dict[str, schema.Field]:
    return {f.attr: f for f in fields}
