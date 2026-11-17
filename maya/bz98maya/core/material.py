"""Ogre ``.material`` files (Maya-free): the writer Blender uses, and a reader for import.

``write_material_file`` is a bpy-free port of ``OgreExport.xSaveMaterialData``
(Battlezone BZBase templates, derived ``_d`` / ``_n`` / ``_S`` / ``_e`` .dds maps); the
golden test runs Blender's own function against it. Material info dicts use the same
keys as Blender's ``bCollectMaterialData``: ``ambient``, ``diffuse``, ``specular``,
``emissive`` (RGB floats) and optionally ``texture`` (image name) and ``texture_path``.

A material read from a ``.material`` also carries ``source`` (its raw block, the file's preamble and
what the Maya shader looked like at import). Writing such a material reproduces the source text and
changes only what the user changed in Maya (``diffuse_override`` / ``texture_override``): the parent
material (BZBase, BZBaseCockpit ...), every texture alias with its exact file name and every
parameter come back as they were. Blender's own writer (``xSaveMaterialData``, which this module
mirrors for materials with no source) rebuilds all of that from the material name, so a stock
``abbarr.material`` does not survive a Blender round trip either (toolkit issue).
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Dict, List, Optional, Tuple

DEFAULT_COLOUR = (1.0, 1.0, 1.0)


def guess_bz_base_template(mat_name: str) -> str:
    name_l = mat_name.lower()
    if "cockpit" in name_l or name_l.endswith("2"):  # cockpit-ish names get the cockpit base
        return "BZBaseCockpit"
    return "BZBase"


def derive_map_names(mat_name: str, tex_name: Optional[str]) -> Tuple[str, str, str, str]:
    base_src = tex_name or mat_name
    base = os.path.splitext(os.path.basename(base_src))[0]
    base_l = base.lower()
    if base_l.endswith("_d") or base_l.endswith("_diffuse"):
        root = base.rsplit("_", 1)[0]
    else:
        root = base
    return root + "_d.dds", root + "_n.dds", root + "_S.dds", root + "_e.dds"


def _fmt_rgb(rgb) -> str:
    return f"{rgb[0]:.3g} {rgb[1]:.3g} {rgb[2]:.3g}"


STANDARD_PREAMBLE = 'import * from "BZBase.material"\n\n'


def source_block_text(source: dict) -> str:
    """The source block with the user's changes applied (and nothing else touched)."""
    text = source["raw"]
    override = source.get("texture_override")
    if override:
        text = re.sub(r"(set_texture_alias\s+DiffuseMap\s+)\S+", lambda m: m.group(1) + override, text, count=1)
    colour = source.get("diffuse_override")
    if colour:
        text = re.sub(r'(set\s+\$diffuse\s+")[^"]*(")', lambda m: m.group(1) + _fmt_rgb(colour) + m.group(2), text, count=1)
    return text


def material_text(materials: Dict[str, dict], exists=None) -> str:
    """The text written for ``materials`` (name -> info dict).

    A material with a ``source`` is its source text (see the module docstring). Any other is the
    exact text Blender writes; with ``exists`` (a callable: file name -> the real file name or None)
    a derived texture alias whose file is not there is left out, so BZBase's default applies, instead
    of naming a file that does not exist.
    """
    sourced = [i["source"] for i in materials.values() if i.get("source")]
    out = [sourced[0].get("preamble") or STANDARD_PREAMBLE if sourced else STANDARD_PREAMBLE]
    for name, info in materials.items():
        if info.get("source"):
            out.append(source_block_text(info["source"]).rstrip() + "\n\n")
            continue
        base_template = guess_bz_base_template(name)
        amb = info.get("ambient", [1.0, 1.0, 1.0])
        dif = info.get("diffuse", [1.0, 1.0, 1.0])
        spe = info.get("specular", [0.7, 0.7, 0.7])
        derived = derive_map_names(name, info.get("texture", None))
        out.append(f"material {name} : {base_template}\n")
        out.append("{\n")
        for alias, file_name in zip(("DiffuseMap", "NormalMap", "SpecularMap", "EmissiveMap"), derived):
            if exists is not None:
                file_name = exists(file_name)
                if not file_name:
                    continue
            out.append(f"\tset_texture_alias {alias} {file_name}\n")
        out.append("\n")
        out.append(f'\tset $diffuse "{_fmt_rgb(dif)}"\n')
        out.append(f'\tset $ambient "{_fmt_rgb(amb)}"\n')
        out.append(f'\tset $specular "{_fmt_rgb(spe)}"\n')
        out.append('\tset $shininess "127"\n')
        out.append("}\n\n")
    return "".join(out)


def _folder_lookup(folder: str, materials: Dict[str, dict]):
    """file name -> the real name of that file beside the .material (or a texture the user's shader points at)."""
    try:
        entries = {e.lower(): e for e in os.listdir(folder)}
    except OSError:
        entries = {}
    known = {os.path.basename(str(i["texture_path"])).lower(): os.path.basename(str(i["texture_path"]))
             for i in materials.values() if i.get("texture_path") and os.path.isfile(str(i["texture_path"]))}

    def exists(name: str) -> Optional[str]:
        return entries.get(name.lower()) or known.get(name.lower())

    return exists


def write_material_file(
    mesh_path: str, materials: Dict[str, dict], overwrite: bool = False, copy_textures: bool = False,
    warnings: Optional[List[str]] = None,
) -> Optional[str]:
    """Write ``<mesh>.material`` unless it exists (and ``overwrite`` is off); copy textures if asked.

    Returns the material file path, or None when there is nothing to write.
    """
    if not materials:
        return None
    mat_file = os.path.splitext(mesh_path)[0] + ".material"
    if not os.path.isfile(mat_file) or overwrite:
        exists = _folder_lookup(os.path.dirname(mat_file) or ".", materials)
        text = material_text(materials, exists)
        if warnings is not None:
            for name, info in materials.items():
                if info.get("source"):
                    changed = info["source"].get("texture_override")
                    if changed and not exists(changed):
                        warnings.append("material %s: texture %s is not next to the .material" % (name, changed))
                    continue
                left_out = [f for f in derive_map_names(name, info.get("texture")) if not exists(f)]
                if left_out:
                    warnings.append("material %s: %s not found next to the .material; left out so BZBase's default applies"
                                    % (name, ", ".join(left_out)))
        with open(mat_file, "w", encoding="utf-8") as handle:
            handle.write(text)
    if copy_textures:
        for info in materials.values():
            source = info.get("texture_path")
            if info.get("texture") and source and os.path.isfile(source):
                try:
                    shutil.copy(source, os.path.dirname(mat_file))
                except OSError:
                    pass
    return mat_file


# --- reading ---------------------------------------------------------------------

_MATERIAL_RE = re.compile(r"^\s*material\s+(\S+)(?:\s*:\s*(\S+))?", re.MULTILINE)


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


def parse_material_file(path: str) -> Dict[str, dict]:
    """Material name -> {"textures": {alias: file}, "diffuse": rgb or None, "template": base}.

    Understands the BZBase form this toolkit writes (``set_texture_alias`` / ``set $diffuse``)
    and plain ``texture_unit { texture x }`` materials. Anything else is skipped.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return {}
    out: Dict[str, dict] = {}
    matches = list(_MATERIAL_RE.finditer(text))
    preamble = text[: matches[0].start()] if matches else ""
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end(): end]
        info = {"textures": {}, "diffuse": None, "template": match.group(2),
                "raw": _block(text, match.start(), end), "preamble": preamble}
        for alias in re.finditer(r"set_texture_alias\s+(\w+)\s+(\S+)", body):
            info["textures"][alias.group(1)] = alias.group(2)
        plain = re.search(r"^\s*texture\s+(\S+)", body, re.MULTILINE)
        if plain and "DiffuseMap" not in info["textures"]:
            info["textures"]["DiffuseMap"] = plain.group(1)
        diffuse = re.search(r'set\s+\$diffuse\s+"([^"]+)"', body)
        if diffuse:
            try:
                info["diffuse"] = tuple(float(v) for v in diffuse.group(1).split()[:3])
            except ValueError:
                pass
        out[match.group(1)] = info
    return out


def find_texture_file(name: str, folder: str, extensions=(".png", ".tga", ".dds", ".jpg", ".jpeg", ".bmp", ".tif")) -> Optional[str]:
    """A texture file beside the mesh, matched case-insensitively (any listed extension)."""
    if not name or not os.path.isdir(folder):
        return None
    wanted = os.path.basename(name).lower()
    stem = os.path.splitext(wanted)[0]
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    for entry in entries:
        if entry.lower() == wanted:
            return os.path.join(folder, entry)
    for entry in entries:
        base, ext = os.path.splitext(entry.lower())
        if base == stem and ext in extensions:
            return os.path.join(folder, entry)
    return None
