"""Authoring attributes on Maya transforms (needs ``maya.cmds``).

Part fields (GEO type, ObjectFlags, bounds, damage reps, spinner, SDF target ...) and
model header fields are real extra attributes named ``bz`` + the Blender property
name (``bzGEOType``, ``bzSphereRadius``, ``bzLOD1`` ...). Enums are enum attributes.
Which attributes exist and how they map to the neutral model is defined by
``core/schema.py`` and ``core/authoring.py``; this module only moves values.

``bzPartName`` keeps the original (possibly Maya-illegal) part name, ``bzProps`` holds
JSON for data the schema has no field for (``lod``, ``slot``, ...), ``bzPreserved``
the raw chunks / VLOC / damage records of an imported vehicle.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import maya.cmds as cmds

from .core import authoring, schema

PART_MARKER = "bzGEOType"
MODEL_MARKER = "bzLOD1"
EXTRA_ATTR = "bzProps"
MODEL_EXTRA_ATTR = "bzModelExtra"
PRESERVED_ATTR = "bzPreserved"
PART_NAME_ATTR = "bzPartName"


def _plug(node: str, attr: str) -> str:
    return "%s.%s" % (node, attr)


def exists(node: str, attr: str) -> bool:
    return bool(cmds.attributeQuery(attr, node=node, exists=True))


def _enum_string(items) -> str:
    return ":".join("%s=%d" % (label, value) for label, value in items)


def _bounded(fld: schema.Field):
    out = {}
    if fld.lo is not None and abs(fld.lo) < schema.BIG:
        out["minValue"] = fld.lo
    if fld.hi is not None and abs(fld.hi) < schema.BIG:
        out["maxValue"] = fld.hi
    return out


def add_field(node: str, fld: schema.Field) -> None:
    """Create the attribute (and compound children) for a field if missing."""
    attr = fld.attr
    if exists(node, attr):
        return
    kind = fld.kind
    if kind == "int":
        extra = _bounded(fld)
        cmds.addAttr(node, longName=attr, attributeType="long", defaultValue=int(fld.default or 0), **extra)
    elif kind == "float":
        extra = _bounded(fld)
        cmds.addAttr(node, longName=attr, attributeType="double", defaultValue=float(fld.default or 0.0), **extra)
    elif kind == "bool":
        cmds.addAttr(node, longName=attr, attributeType="bool", defaultValue=bool(fld.default))
    elif kind == "string":
        cmds.addAttr(node, longName=attr, dataType="string")
    elif kind == "enum":
        cmds.addAttr(node, longName=attr, attributeType="enum", enumName=_enum_string(fld.enum))
    elif kind in ("float3", "int3"):
        base = "double" if kind == "float3" else "long"
        cmds.addAttr(node, longName=attr, attributeType=base + "3")
        for child in fld.children:
            cmds.addAttr(node, longName="bz" + child, attributeType=base, parent=attr)
    elif kind == "floatarray":
        cmds.addAttr(node, longName=attr, dataType="doubleArray")
    else:
        raise ValueError("unsupported field kind %s" % kind)


def _ensure_enum_value(node: str, fld: schema.Field, value: int) -> None:
    """Extend an enum attribute so a raw value outside the list still shows honestly."""
    plug = _plug(node, fld.attr)
    items = list(fld.enum)
    if value in {v for _, v in items}:
        return
    label = "Unknown (0x%X)" % (value & 0xFFFFFFFF) if fld.blender == "GEOType" else "Reserved (0x%04X)" % value
    cmds.addAttr(plug, edit=True, enumName=_enum_string(items + [(label, value)]))


def set_field(node: str, fld: schema.Field, value: Any) -> None:
    add_field(node, fld)
    plug = _plug(node, fld.attr)
    kind = fld.kind
    if kind == "string":
        cmds.setAttr(plug, value or "", type="string")
    elif kind == "enum":
        _ensure_enum_value(node, fld, int(value))
        cmds.setAttr(plug, int(value))
    elif kind == "float3":
        cmds.setAttr(plug, *[float(v) for v in value], type="double3")
    elif kind == "int3":
        cmds.setAttr(plug, *[int(v) for v in value], type="long3")
    elif kind == "floatarray":
        cmds.setAttr(plug, [float(v) for v in value], type="doubleArray")
    elif kind == "bool":
        cmds.setAttr(plug, bool(value))
    elif kind == "int":
        cmds.setAttr(plug, int(value))
    else:
        cmds.setAttr(plug, float(value))


def get_field(node: str, fld: schema.Field):
    plug = _plug(node, fld.attr)
    value = cmds.getAttr(plug)
    if fld.kind in ("float3", "int3"):
        return tuple(value[0])
    if fld.kind == "floatarray":
        return tuple(value or ())
    if fld.kind == "string":
        return value or ""
    return value


def _write(node: str, fields, values: Dict[str, Any]) -> None:
    for fld in fields:
        if fld.attr in values:
            set_field(node, fld, values[fld.attr])


def _read(node: str, fields) -> Dict[str, Any]:
    return {fld.attr: get_field(node, fld) for fld in fields if exists(node, fld.attr)}


def _json_attr(node: str, name: str, data: Optional[dict]) -> None:
    if not data:
        return
    if not exists(node, name):
        cmds.addAttr(node, longName=name, dataType="string")
    cmds.setAttr(_plug(node, name), json.dumps(data), type="string")


def _read_json(node: str, name: str) -> Dict[str, Any]:
    if not exists(node, name):
        return {}
    try:
        return json.loads(cmds.getAttr(_plug(node, name)) or "{}")
    except ValueError:
        return {}


# --- parts -----------------------------------------------------------------

def is_part(node: str) -> bool:
    return exists(node, PART_MARKER)


def write_part(node: str, props: Dict[str, Any]) -> None:
    """Create / update every part attribute from neutral props."""
    values, extra = authoring.part_attr_values(props)
    _write(node, schema.part_fields(), values)
    _json_attr(node, EXTRA_ATTR, extra)


def read_part(node: str) -> Dict[str, Any]:
    """Neutral props from the attributes that exist on ``node``."""
    values = _read(node, schema.part_fields())
    return authoring.part_props_from_attrs(values, _read_json(node, EXTRA_ATTR))


def ensure_part(node: str) -> None:
    """Tag a plain transform: add every part attribute with Blender's defaults."""
    if not is_part(node):
        write_part(node, {})


# --- model header ------------------------------------------------------------

def write_model(node: str, meta: Dict[str, Any]) -> None:
    values, extra = authoring.meta_attr_values(meta)
    preserved = extra.pop("preserved", None)
    _write(node, schema.model_fields(), values)
    _json_attr(node, MODEL_EXTRA_ATTR, extra)
    _json_attr(node, PRESERVED_ATTR, preserved)


def read_model(node: str) -> Dict[str, Any]:
    values = _read(node, schema.model_fields())
    meta = authoring.meta_from_attr_values(values, _read_json(node, MODEL_EXTRA_ATTR))
    preserved = _read_json(node, PRESERVED_ATTR)
    if preserved:
        meta["preserved"] = preserved
    return meta


def is_model(node: str) -> bool:
    return exists(node, MODEL_MARKER)


def ensure_model(node: str) -> None:
    if not is_model(node):
        write_model(node, {})
