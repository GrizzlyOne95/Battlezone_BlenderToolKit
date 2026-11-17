"""Ogre ``.material`` source round trip for the Blender add-on (bpy-free).

A material read from a ``.material`` keeps its raw block and the file's preamble as custom
properties on the Blender material, plus a snapshot of the diffuse colour / texture name at import.
On export the stored block is reproduced and only what the user changed (diffuse colour, diffuse
texture) is substituted, so the parent (BZBase, BZBaseCockpit ...), every texture alias and every
parameter survive. Materials with no source are written by ``OgreExport.xSaveMaterialData`` exactly
as before. Script items outside ``material`` blocks (abstract materials, programs) are not
carried; a sourced material whose parent is defined in its own source file is reported at export
(``local_parent``). Port of ``maya/bz98maya/core/material.py`` (kept separate on purpose).
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional

STANDARD_PREAMBLE = 'import * from "BZBase.material"\n\n'

PROP_RAW = "bz_material_source"
PROP_PREAMBLE = "bz_material_preamble"
PROP_SNAPSHOT = "bz_material_snapshot"  # JSON {"diffuse": [r, g, b], "texture": name}

_NAME = r"[^\s:{]+"
_MATERIAL_RE = re.compile(r"^\s*material\s+(" + _NAME + r")(?:\s*:\s*([^\s{]+))?", re.MULTILINE)
_DEFINED_RE = re.compile(r"^\s*(?:abstract\s+)?material\s+(" + _NAME + ")", re.MULTILINE)


_HEADER_RE = re.compile(r"^(\s*material\s+)" + _NAME, re.MULTILINE)


def fmt_rgb(rgb) -> str:
    return f"{rgb[0]:.3g} {rgb[1]:.3g} {rgb[2]:.3g}"


def _block(text: str, start: int, limit: int) -> str:
    """The ``material ... { ... }`` text from ``start``: up to the brace that closes it."""
    depth, seen = 0, False
    for i in range(start, limit):
        if text[i] == "{":
            depth, seen = depth + 1, True
        elif text[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return text[start: i + 1]
    return text[start:limit].rstrip()


def parse_material_text(text: str) -> Dict[str, dict]:
    """Material name -> {"raw": block text, "preamble": text before the first material}."""
    matches = list(_MATERIAL_RE.finditer(text))
    # the regex's leading \s* swallows blank lines; a block starts at the keyword itself
    starts = [text.index("material", m.start()) for m in matches]
    preamble = text[: starts[0]] if matches else ""
    defined = set(_DEFINED_RE.findall(text))
    out: Dict[str, dict] = {}
    for i, match in enumerate(matches):
        end = starts[i + 1] if i + 1 < len(matches) else len(text)
        parent = match.group(2)
        out.setdefault(match.group(1), {
            "raw": _block(text, starts[i], end), "preamble": preamble,
            "local_parent": parent if parent in defined and not parent.startswith("BZBase") else "",
        })
    return out


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def find_source(mesh_path: str, material_name: str) -> Optional[dict]:
    """The block for ``material_name`` from ``<mesh>.material``, else any ``.material`` beside the mesh."""
    folder = os.path.dirname(mesh_path) or "."
    own = os.path.splitext(mesh_path)[0] + ".material"
    candidates = [own] if os.path.isfile(own) else []
    try:
        candidates += [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                       if f.lower().endswith(".material") and os.path.join(folder, f) != own]
    except OSError:
        pass
    for path in candidates:
        found = parse_material_text(_read(path)).get(material_name)
        if found:
            return found
    return None


def texture_key(path_or_name) -> str:
    """File name of a texture path or image name (either slash style, on any platform)."""
    return re.split(r"[\\/]",str(path_or_name or ""))[-1]


def read_colour_texture(material):
    """(diffuse rgb, texture name) as ``OgreExport.bCollectMaterialData`` would read them."""
    diffuse = (1.0, 1.0, 1.0)
    texture = ""
    tree = getattr(material, "node_tree", None)
    if getattr(material, "use_nodes", False) and tree:
        for node in tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                col = node.inputs["Base Color"].default_value
                diffuse = (col[0], col[1], col[2])
                break
        for node in tree.nodes:
            if node.type == "TEX_IMAGE" and getattr(node, "image", None):
                texture = texture_key(node.image.filepath or node.image.name)
                break
    elif hasattr(material, "diffuse_color"):
        dc = material.diffuse_color
        diffuse = (dc[0], dc[1], dc[2])
    return diffuse, texture


def attach_source(material, mesh_path: str, ogre_name: str = "") -> bool:
    """Store the source block of ``material`` (a Blender material) on it; False if not found.

    ``ogre_name`` is the name in the ``.material`` when it differs from the Blender name.
    """
    if material is None:
        return False
    source = find_source(mesh_path, ogre_name or material.name)
    if not source:
        return PROP_RAW in material  # keep what an earlier import stored
    diffuse, texture = read_colour_texture(material)
    material[PROP_RAW] = source["raw"]
    material[PROP_PREAMBLE] = source["preamble"]
    material[PROP_SNAPSHOT] = json.dumps(
        {"diffuse": list(diffuse), "texture": texture, "local_parent": source["local_parent"]}
    )
    return True


def source_info(material, diffuse, texture) -> Optional[dict]:
    """The ``source`` dict for export (None if the material has none).

    ``diffuse`` / ``texture`` are what the exporter read now (texture: file or image name).
    """
    raw = material.get(PROP_RAW) if hasattr(material, "get") else None
    if not raw:
        return None
    try:
        snap = json.loads(material.get(PROP_SNAPSHOT) or "{}")
    except ValueError:
        snap = {}
    source = {"raw": raw, "preamble": material.get(PROP_PREAMBLE) or "",
              "local_parent": snap.get("local_parent") or ""}
    was = snap.get("diffuse")
    if was is not None and any(abs(a - b) > 1e-3 for a, b in zip(diffuse, was)):
        source["diffuse_override"] = list(diffuse)
    texture = texture_key(texture)
    if texture and texture != texture_key(snap.get("texture")):
        source["texture_override"] = texture
    return source


def source_block_text(source: dict) -> str:
    """The source block with the user's changes applied (and nothing else touched)."""
    text = source["raw"]
    override = source.get("texture_override")
    if override:
        text = re.sub(r"(set_texture_alias\s+DiffuseMap\s+)\S+", lambda m: m.group(1) + override, text, count=1)
    colour = source.get("diffuse_override")
    if colour:
        text = re.sub(r'(set\s+\$diffuse\s+")[^"]*(")', lambda m: m.group(1) + fmt_rgb(colour) + m.group(2), text, count=1)
    return text


def preamble_for(materials: Dict[str, dict]) -> str:
    """The file preamble: the unique ``import`` lines of every sourced preamble (order kept).

    The BZBase import is added when a material without a source is written or none is present.
    """
    lines = []
    sourced = False
    for info in materials.values():
        source = info.get("source")
        if not source:
            continue
        sourced = True
        for line in (source.get("preamble") or "").splitlines():
            line = line.strip()
            if re.match(r"import\b", line) and line not in lines:
                lines.append(line)
    if not sourced:
        return STANDARD_PREAMBLE
    standard = STANDARD_PREAMBLE.strip()
    unsourced = any(not i.get("source") for i in materials.values())
    if standard not in lines and (unsourced or not any("BZBase.material" in l for l in lines)):
        lines.append(standard)
    return "\n".join(lines) + "\n\n"


def block_for(info: dict, name: str = "") -> str:
    """The block text to write; the header is renamed to ``name`` (the exported material name).

    The mesh's submesh references the exported name, so a renamed or ``.001`` material must not
    keep the old header.
    """
    text = source_block_text(info["source"]).rstrip() + "\n\n"
    if name:
        text = _HEADER_RE.sub(lambda m: m.group(1) + name, text, count=1)
    return text
