"""Export modes, options and presets (Maya-free).

The option set of each export kind is read from Blender's operator classes in
``_addon_core.py`` (``schema.blender_properties``), so names, defaults, enum items
and tooltips match Blender's export dialogs. Presets are Blender's built-in table
plus user presets stored as JSON, as Blender's Save / Apply / Delete Export Preset do.

Kinds: ``VDF``, ``SDF``, ``GEO`` (legacy export, optionally followed by the Redux
auto-port) and ``MESH`` ("Redux Mesh Only").
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import schema

KINDS = ("VDF", "SDF", "GEO", "MESH")
OPERATOR_CLASS = {
    "VDF": "ExportVDF",
    "SDF": "ExportSDF",
    "GEO": "ExportGEO",
    "MESH": "BZ98TOOLS_OT_export_bzr_mesh",
}
EXTENSION = {"VDF": ".vdf", "SDF": ".sdf", "GEO": ".geo", "MESH": ".mesh"}

MODE_LEGACY = "Legacy only"
MODE_LEGACY_REDUX = "Legacy + Redux"
MODE_REDUX = "Redux only"
MODE_TEXT = {
    MODE_LEGACY: "Writes only the selected legacy format.",
    MODE_LEGACY_REDUX: "Writes legacy files first, then generates Redux mesh-side files.",
    MODE_REDUX: "Writes Redux mesh-side files without creating GEO, VDF, or SDF files.",
}

# Not offered in Maya: the OgreXMLConverter path (the pure serializer needs no converter) and
# the skeleton / animation options (static export only, animation is a later tier).
_HIDDEN = {
    "filter_glob", "xml_converter", "keep_xml", "export_skeleton", "export_poses", "export_animation",
    "renormalize_weights", "export_colour", "ExportAnimations", "ogre_skeletalanims_mode", "ogre_no_pov_rots",
    "ogre_stabilize_walker_cockpit",
}
GROUPS = {
    "mesh": ("apply_transform", "apply_modifiers", "batch_export"),
    "tangents": ("export_tangents", "export_binormals", "zero_tangents_binormals", "tangent_parity"),
    "materials": ("export_materials", "overwrite_material", "copy_textures"),
}


@dataclass
class Option:
    name: str
    kind: str  # bool string enum floatvec intvec int float
    label: str
    tip: str
    default: Any
    items: List[Tuple[str, str, str]] = field(default_factory=list)
    size: int = 1
    vdf_only: bool = False


_KIND_OF = {
    "BoolProperty": "bool",
    "StringProperty": "string",
    "EnumProperty": "enum",
    "FloatVectorProperty": "floatvec",
    "IntVectorProperty": "intvec",
    "IntProperty": "int",
    "FloatProperty": "float",
}


def option_fields(kind: str) -> List[Option]:
    props = schema.blender_properties(OPERATOR_CLASS[kind])
    vdf_only = set(schema.blender_properties(OPERATOR_CLASS["VDF"])) - set(
        schema.blender_properties(OPERATOR_CLASS["SDF"])
    )
    out: List[Option] = []
    for name, info in props.items():
        if name in _HIDDEN or info.get("__type__") not in _KIND_OF:
            continue
        default = info.get("default")
        if isinstance(default, (list, tuple)):
            default = list(default)
        out.append(
            Option(
                name=name,
                kind=_KIND_OF[info["__type__"]],
                label=info.get("name", name),
                tip=info.get("description", ""),
                default=default,
                items=[tuple(item[:3]) for item in info.get("items", [])],
                size=int(info.get("size", 1)),
                vdf_only=name in vdf_only,
            )
        )
    return out


def default_options(kind: str) -> Dict[str, Any]:
    return {o.name: o.default for o in option_fields(kind)}


def merged_options(kind: str, options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = default_options(kind)
    out.update({k: v for k, v in (options or {}).items() if k in out})
    return out


def export_mode(kind: str, options: Dict[str, Any]) -> str:
    if kind == "MESH":
        return MODE_REDUX
    return MODE_LEGACY_REDUX if options.get("auto_port_ogre") else MODE_LEGACY


def autoport_options(options: Dict[str, Any]) -> Dict[str, Any]:
    """The porter options dict the Blender operators build (``ogre_*`` -> porter keys)."""

    def text(key):
        return str(options.get(key) or "").strip() or None

    opts = {
        "name": text("ogre_name"),
        "suffix": text("ogre_suffix") or "_port",
        "flat_colors": options.get("ogre_flat_colors", False),
        "normal_mode": options.get("ogre_normal_mode", "CORRECT"),
        "bounds_mult": list(options.get("ogre_bounds_mult", (1.0, 1.0, 1.0))),
        "act_path": text("ogre_act_path"),
        "config_path": text("ogre_config_path"),
        "only_once": options.get("ogre_only_once", False),
        "nowrite": options.get("ogre_nowrite", False),
        "skip_unchanged": options.get("ogre_skip_unchanged", True),
        "profile_export": options.get("ogre_profile_export", False),
        "dest_dir": text("ogre_dest_dir"),
    }
    if "ogre_headlights" in options:  # VDF-only options
        opts.update(
            {
                "headlights": options["ogre_headlights"],
                "person_mode": options.get("ogre_person_mode", "AUTO"),
                "turret_mode": options.get("ogre_turret_mode", "AUTO"),
                "cockpit_mode": options.get("ogre_cockpit_mode", "AUTO"),
                "skeletalanims_mode": options.get("ogre_skeletalanims_mode", "AUTO"),
                "scope_mode": options.get("ogre_scope_mode", "AUTO"),
                "scope_type": options.get("ogre_scope_type", "AUTO"),
                "scope_nation": text("ogre_scope_nation"),
                "scope_screen": list(options.get("ogre_scope_screen", (0.0, 0.0, 0.0, 1.0, 0.0))),
                "scope_gun": text("ogre_scope_gun"),
                "scope_transform": list(options.get("ogre_scope_transform", (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0))),
                "scope_texture": text("ogre_scope_texture"),
                "no_pov_rots": options.get("ogre_no_pov_rots", False),
                "stabilize_walker_cockpit": options.get("ogre_stabilize_walker_cockpit", False),
            }
        )
    return opts


# --- presets (port of Blender's save / apply / delete export preset) -----------------

def preset_property_names(kind: str) -> Tuple[str, ...]:
    return tuple((schema.blender_constant("EXPORT_PRESET_PROPERTY_NAMES") or {}).get(kind, ()))


def builtin_presets(kind: str) -> List[Tuple[str, str, Dict[str, Any]]]:
    return [(k, label, dict(values)) for k, label, values in (schema.blender_constant("BUILTIN_EXPORT_PRESETS") or {}).get(kind, ())]


def normalize_preset_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (name or "").strip())
    return cleaned.strip("._") or "preset"


def default_preset_dir(kind: str) -> str:
    root = os.environ.get("BZ98MAYA_PRESET_DIR")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".bz98maya", "presets")
    return os.path.join(root, kind.lower())


def serialize_options(kind: str, options: Dict[str, Any]) -> Dict[str, Any]:
    return {
        name: (list(options[name]) if isinstance(options[name], (list, tuple)) else options[name])
        for name in preset_property_names(kind)
        if name in options
    }


def apply_preset_values(kind: str, options: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """Options with the preset's values applied (only the properties presets cover)."""
    out = dict(options)
    for name in preset_property_names(kind):
        if name in values and name in out:
            out[name] = list(values[name]) if isinstance(values[name], (list, tuple)) else values[name]
    return out


def list_custom_presets(kind: str, directory: Optional[str] = None) -> List[Dict[str, str]]:
    directory = directory or default_preset_dir(kind)
    if not os.path.isdir(directory):
        return []
    results = []
    for entry in sorted(os.listdir(directory)):
        if not entry.lower().endswith(".json"):
            continue
        path = os.path.join(directory, entry)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        results.append({"label": (payload.get("label") or os.path.splitext(entry)[0]).strip(), "path": path})
    return results


def load_custom_preset(kind: str, label: str, directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for entry in list_custom_presets(kind, directory):
        if entry["label"] == label:
            try:
                with open(entry["path"], "r", encoding="utf-8") as handle:
                    return json.load(handle).get("values", {})
            except (OSError, ValueError):
                return None
    return None


def save_custom_preset(kind: str, label: str, options: Dict[str, Any], directory: Optional[str] = None) -> str:
    label = (label or "").strip()
    if not label:
        raise ValueError("Preset name cannot be empty.")
    directory = directory or default_preset_dir(kind)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, normalize_preset_filename(label) + ".json")
    payload = {"label": label, "export_kind": kind, "values": serialize_options(kind, options)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def delete_custom_preset(kind: str, label: str, directory: Optional[str] = None) -> bool:
    for entry in list_custom_presets(kind, directory):
        if entry["label"] == (label or "").strip():
            os.remove(entry["path"])
            return True
    return False
