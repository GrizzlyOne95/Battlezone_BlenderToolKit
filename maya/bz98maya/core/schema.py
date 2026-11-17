"""Field schema for the authoring attributes and panel (Maya-free).

Names, defaults, ranges, enum lists and tooltips are read from the Blender
add-on source (``bz98tools/_addon_core.py``) with ``ast`` rather than copied, so
the Maya panel cannot drift from Blender's wording. ``_addon_core`` itself needs
``bpy`` and is never imported; only its literal property declarations and the
literal reference tables (GEO types, hints, info text) are evaluated.

Each :class:`Field` ties one Blender property to

* a neutral ``props`` / ``meta`` key (what ``vdf_io`` / ``sdf_io`` read and write), and
* a Maya extra attribute name (``bz`` + the Blender property name).
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .toolkit import ensure_toolkit, find_toolkit_dir

BIG = 1.0e30  # ranges beyond this are "unbounded" (Blender uses float32 max for LODs)


@dataclass
class Field:
    key: str  # props / meta key ("" for derived fields)
    blender: str  # Blender property name
    kind: str  # int float bool string enum float3 int3 floatarray
    group: str
    default: Any = None
    label: str = ""
    tip: str = ""
    lo: Optional[float] = None
    hi: Optional[float] = None
    maxlen: Optional[int] = None
    size: int = 1
    children: Tuple[str, ...] = ()  # compound child names (float3 / int3)
    keys: Tuple[str, ...] = ()  # float3 stored as several scalar keys (SDF target)
    enum: List[Tuple[str, int]] = field(default_factory=list)
    derived: bool = False  # computed from another field (ObjectFlags bits)

    @property
    def attr(self) -> str:
        return "bz" + self.blender


# --- Blender source ----------------------------------------------------------

_SOURCE_CACHE: Dict[str, Any] = {}


def _tree():
    if "tree" not in _SOURCE_CACHE:
        path = os.path.join(find_toolkit_dir(), "_addon_core.py")
        with open(path, "r", encoding="utf-8") as handle:
            _SOURCE_CACHE["tree"] = ast.parse(handle.read())
    return _SOURCE_CACHE["tree"]


def _literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        pass
    if isinstance(node, ast.Name):  # e.g. items=NORMAL_MODE_ITEMS
        return blender_constant(node.id)
    # list(range(...)) inside a literal table (ANIMATION_PRESET_SLOTS); nothing else is evaluated
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    if names and names <= {"list", "range", "tuple"}:
        try:
            return eval(compile(ast.Expression(node), "_addon_core.py", "eval"), {"__builtins__": {}},
                        {"list": list, "range": range, "tuple": tuple})
        except Exception:
            return None
    return None


def blender_properties(class_name: str) -> Dict[str, Dict[str, Any]]:
    """{property: {keyword: literal}} for a PropertyGroup class in _addon_core.py."""
    cache = _SOURCE_CACHE.setdefault("props", {})
    if class_name in cache:
        return cache[class_name]
    out: Dict[str, Dict[str, Any]] = {}
    for node in _tree().body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and isinstance(item.annotation, ast.Call)
                ):
                    kwargs = {"__type__": getattr(item.annotation.func, "attr", getattr(item.annotation.func, "id", ""))}
                    for keyword in item.annotation.keywords:
                        value = _literal(keyword.value)
                        if value is not None:
                            kwargs[keyword.arg] = value
                    out[item.target.id] = kwargs
    cache[class_name] = out
    return out


def blender_constant(name: str):
    """A module-level literal assignment of _addon_core.py (hints, info text, ...)."""
    cache = _SOURCE_CACHE.setdefault("consts", {})
    if not cache:
        for node in _tree().body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                value = _literal(node.value)
                if value is not None:
                    cache[node.targets[0].id] = value
    return cache.get(name)


def blender_function(name: str):
    """A bpy-free module-level helper function of _addon_core.py, compiled on its own."""
    cache = _SOURCE_CACHE.setdefault("functions", {})
    if name not in cache:
        for node in _tree().body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                namespace: Dict[str, Any] = {}
                exec(compile(ast.Module(body=[node], type_ignores=[]), "_addon_core.py", "exec"), namespace)
                cache[name] = namespace[name]
                break
        else:
            raise KeyError("no function %s in _addon_core.py" % name)
    return cache[name]


def function_label_texts(name: str) -> List[str]:
    """The ``label(text=...)`` strings of a Blender draw function, in order ("" for separators)."""
    for node in _tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            out: List[str] = []
            calls = sorted((n for n in ast.walk(node) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))
            for call in calls:
                attr = getattr(call.func, "attr", "")
                if attr == "label":
                    for keyword in call.keywords:
                        if keyword.arg == "text":
                            value = _literal(keyword.value)
                            if isinstance(value, str):
                                out.append(value)
                elif attr == "separator":
                    out.append("")
            return out
    return []


def geo_types() -> List[Tuple[int, str]]:
    """Blender's GEO type list: ``insertgeotypedata(idx, label)`` calls, in source order."""
    if "geotypes" not in _SOURCE_CACHE:
        items = []
        for node in _tree().body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "insertgeotypedata"
            ):
                args = [_literal(a) for a in node.value.args]
                if len(args) == 2 and isinstance(args[0], int):
                    items.append((args[0], str(args[1])))
        _SOURCE_CACHE["geotypes"] = items
    return _SOURCE_CACHE["geotypes"]


def geo_type_hints() -> Dict[int, Tuple[str, str]]:
    return dict(blender_constant("GEO_TYPE_UI_HINTS") or {})


def info_lines(name: str) -> List[str]:
    return list(blender_constant(name) or [])


def geo_type_reference_text() -> str:
    """The text of Blender's "GEO Type Reference" popover."""
    lines = ["ID - Description", ""]
    for idx, label in geo_types():
        lines.append("%d - %s" % (idx, label))
    return "\n".join(lines)


def geo_type_label(value: int) -> str:
    ensure_toolkit()
    from bz98tools import semantics

    return semantics.part_type_label(int(value))


def enum_name(label: str) -> str:
    """A label made safe for a Maya enum attribute ('=' and ':' are separators)."""
    short = label.split(" (")[0].strip()
    return short.replace("=", "-").replace(":", "-")


def geo_type_enum() -> List[Tuple[str, int]]:
    return [("%d - %s" % (idx, enum_name(label)), idx) for idx, label in geo_types()]


# --- schema ------------------------------------------------------------------

def _info(props: Dict[str, Dict[str, Any]], name: str) -> Dict[str, Any]:
    return props.get(name, {})


def _make(props, blender, key, kind, group, **extra) -> Field:
    info = _info(props, blender)
    values = dict(
        default=info.get("default"),
        label=info.get("name", blender) or blender,
        tip=info.get("description", ""),
        lo=info.get("min"),
        hi=info.get("max"),
        maxlen=info.get("maxlen"),
    )
    values.update(extra)
    return Field(key=key, blender=blender, kind=kind, group=group, **values)


_PART_CACHE: List[Field] = []
_MODEL_CACHE: List[Field] = []


def part_fields() -> List[Field]:
    """Per-part fields (the "Battlezone GEO" / "Selected GEO" panels)."""
    if _PART_CACHE:
        return _PART_CACHE
    ensure_toolkit()
    from bz98tools import semantics

    p = blender_properties("GEOPropertyGroup")
    f: List[Field] = []

    f.append(_make(p, "GEOType", "type", "enum", "GEO", enum=geo_type_enum(), label="GEO Type"))
    f.append(_make(p, "GEOFlags", "flags", "int", "GEO", label="Object Flags (raw)",
                   lo=None, hi=None))

    # Engine Object Flags (decoded bits of GEOFlags, Blender's "Advanced Semantics")
    for blender, mask in (
        ("FlagKeepBounds", semantics.OBJFLAG_KEEP_BOUNDS),
        ("FlagViewRender", semantics.OBJFLAG_VIEW_RENDER),
        ("FlagDestroyedSeed", semantics.OBJFLAG_DESTROYED),
        ("FlagLightAttached", semantics.OBJFLAG_LIGHT_ATTACHED),
    ):
        field_ = _make(p, blender, "", "bool", "Flags", derived=True)
        field_.default = False
        f.append(field_)
    collision = _make(p, "CollisionClass", "", "enum", "Flags", derived=True,
                      enum=[(choice[1], choice[0]) for choice in semantics.COLLISION_CLASS_CHOICES])
    collision.default = 0
    f.append(collision)
    team = _make(p, "ObjFlagTeam", "", "int", "Flags", derived=True)
    team.default = 0
    f.append(team)

    f.append(_make(p, "BoundsMode", "bounds_mode", "enum", "Bounds",
                   enum=[(item[1], i) for i, item in enumerate(_info(p, "BoundsMode").get("items", ()))]))
    f.append(_make(p, "GenerateCollision", "generate_collision", "bool", "Bounds"))
    f.append(_make(p, "HasAuthoredBounds", "authored_bounds", "bool", "Bounds"))
    f.append(_make(p, "GeoCenterX", "geocenter", "float3", "Bounds",
                   children=("GeoCenterX", "GeoCenterY", "GeoCenterZ"), default=(0.0, 0.0, 0.0),
                   label="GEO Center"))
    f[-1].blender = "GeoCenter"
    f.append(_make(p, "SphereRadius", "sphereradius", "float", "Bounds"))
    f.append(_make(p, "BoxHalfHeightX", "boxhalfheight", "float3", "Bounds",
                   children=("BoxHalfHeightX", "BoxHalfHeightY", "BoxHalfHeightZ"), default=(0.0, 0.0, 0.0),
                   label="Projectile Box Half-Extents"))
    f[-1].blender = "BoxHalfHeight"

    for state in (1, 2, 3):
        f.append(_make(p, "DamageGeo%d" % state, "damage%d" % state, "string", "Damage"))
    f.append(_make(p, "HasDamageVariants", "has_damage_variants", "bool", "Damage"))

    f.append(_make(p, "IsPOVHelper", "pov_helper", "bool", "Eyepoint"))

    f.append(_make(p, "IsSpinnerHelper", "is_spinner", "bool", "Spinner"))
    f.append(_make(p, "SpinnerTarget", "spinner_target", "string", "Spinner"))
    f.append(_make(p, "SpinnerAxis", "spinner_axis", "float3", "Spinner",
                   children=("SpinnerAxisX", "SpinnerAxisY", "SpinnerAxisZ")))
    f.append(_make(p, "SpinnerSpeed", "spinner_speed", "float", "Spinner"))

    f.append(_make(p, "SDFDDR", "ddr", "int", "SDF"))
    f.append(_make(p, "SDFX", "x", "float3", "SDF", keys=("x", "y", "z"),
                   children=("SDFX", "SDFY", "SDFZ"), default=(0.0, 0.0, 0.0), label="Target"))
    f[-1].blender = "SDFTarget"
    f[-1].tip = (
        "Raw StructObjectType Target vector. Y is the spin speed for stock type-15 structure spinners; "
        "X and Z are used by some special structure helpers."
    )
    f.append(_make(p, "SDFTime", "time", "float", "SDF"))

    f.append(_make(p, "UseRawVDFMatrix", "use_raw_matrix", "bool", "Experimental"))
    f.append(_make(p, "RawVDFMatrix", "raw_matrix", "floatarray", "Experimental", size=12))
    f.append(_make(p, "GEOHeaderUnknown", "header_unknown", "int", "Experimental"))
    f.append(_make(p, "GEOHeaderUnknown2", "header_flags", "int", "Experimental"))
    f.append(_make(p, "GEOFaceUnknownDefault", "face_unknown", "int", "Experimental"))
    f.append(_make(p, "GEOFaceShadeTypeDefault", "face_shade", "int", "Experimental"))
    f.append(_make(p, "GEOFaceTextureTypeDefault", "face_texture", "int", "Experimental"))
    f.append(_make(p, "GEOFaceXluscentTypeDefault", "face_xlu", "int", "Experimental"))
    f.append(_make(p, "GEOFaceParentDefault", "face_parent", "int", "Experimental"))
    f.append(_make(p, "GEOFaceNodeDefault", "face_node", "int", "Experimental"))
    f.append(_make(p, "ANIMOrientationFlags", "anim_orientation_flags", "int", "Experimental"))

    # Overrides where Blender's default is only meaningful for a Blender-authored object.
    for fld in f:
        if fld.key == "damage1" or fld.key == "damage2" or fld.key == "damage3":
            fld.default = ""
        if fld.key == "spinner_axis":
            fld.default = (1.0, 0.0, 0.0)
        if fld.key == "raw_matrix":
            fld.default = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        if fld.key == "bounds_mode":
            fld.default = 0  # AUTO
    _PART_CACHE.extend(f)
    return _PART_CACHE


BOUNDS_MODES = ("AUTO", "PRESERVE", "RECALC")  # enum index -> Blender identifier


def model_fields() -> List[Field]:
    """Model header fields (Blender's "Asset Properties" scene panel)."""
    if _MODEL_CACHE:
        return _MODEL_CACHE
    p = blender_properties("SDFVDFPropertyGroup")
    f: List[Field] = []
    f.append(_make(p, "Name", "name", "string", "Model"))
    f.append(_make(p, "VehicleSize", "vehiclesize", "int", "VDF"))
    f.append(_make(p, "VehicleType", "vehicletype", "int", "VDF"))
    f.append(_make(p, "Mass", "mass", "float", "VDF"))
    f.append(_make(p, "CollMult", "multiplyer", "float", "VDF", label="Collision Multiplier"))
    f.append(_make(p, "DragCoefficient", "drag", "float", "VDF"))
    f.append(_make(p, "StructureType", "structuretype", "int", "SDF"))
    f.append(_make(p, "Defensive", "ddr", "int", "SDF"))
    f.append(_make(p, "DeathExplosion", "explosioneffect", "string", "SDF", label="Death Explosion"))
    f.append(_make(p, "DeathSound", "explosionsound", "string", "SDF", label="Death Sound"))
    for i in range(1, 6):
        f.append(_make(p, "LOD%d" % i, "lod%d" % i, "float", "LOD"))
    f.append(_make(p, "UseCustomSCPS", "use_scps", "bool", "Raw"))
    f.append(_make(p, "SCPSData", "scps_data", "int3", "Raw", children=("SCPSData0", "SCPSData1", "SCPSData2"),
                   default=(0, 0, 0)))
    f.append(_make(p, "VDFCRawNull", "vdfc_null", "int", "Raw"))
    f.append(_make(p, "UseAdvancedAnimHeader", "use_advanced_anim_header", "bool", "Animation"))
    f.append(_make(p, "AnimNull2", "anim_null2", "int", "Animation"))
    f.append(_make(p, "AnimUnknown2", "anim_unknown2", "int", "Animation"))
    f.append(_make(p, "AnimReserved", "anim_reserved", "intarray", "Animation", size=5, default=(0, 0, 0, 0, 0)))
    f.append(_make(p, "UseTranslation2Track", "use_translation2", "bool", "Animation"))
    _MODEL_CACHE.extend(f)
    return _MODEL_CACHE


def defaults_for(fields: List[Field]) -> Dict[str, Any]:
    return {f.key: f.default for f in fields if f.key and f.default is not None}
