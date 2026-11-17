"""Ogre ``.material`` files (Maya-free): the writer Blender uses, and a reader for import.

``write_material_file`` is a bpy-free port of ``OgreExport.xSaveMaterialData``
(Battlezone BZBase templates, derived ``_d`` / ``_n`` / ``_S`` / ``_e`` .dds maps); the
golden test runs Blender's own function against it. Material info dicts use the same
keys as Blender's ``bCollectMaterialData``: ``ambient``, ``diffuse``, ``specular``,
``emissive`` (RGB floats) and optionally ``texture`` (image name) and ``texture_path``.
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


def material_text(materials: Dict[str, dict]) -> str:
    """The exact text Blender writes for ``materials`` (name -> info dict)."""
    out = ['import * from "BZBase.material"\n\n']
    for name, info in materials.items():
        base_template = guess_bz_base_template(name)
        amb = info.get("ambient", [1.0, 1.0, 1.0])
        dif = info.get("diffuse", [1.0, 1.0, 1.0])
        spe = info.get("specular", [0.7, 0.7, 0.7])
        diffuse_tex, normal_tex, spec_tex, emiss_tex = derive_map_names(name, info.get("texture", None))
        out.append(f"material {name} : {base_template}\n")
        out.append("{\n")
        out.append(f"\tset_texture_alias DiffuseMap {diffuse_tex}\n")
        out.append(f"\tset_texture_alias NormalMap {normal_tex}\n")
        out.append(f"\tset_texture_alias SpecularMap {spec_tex}\n")
        out.append(f"\tset_texture_alias EmissiveMap {emiss_tex}\n")
        out.append("\n")
        out.append(f'\tset $diffuse "{_fmt_rgb(dif)}"\n')
        out.append(f'\tset $ambient "{_fmt_rgb(amb)}"\n')
        out.append(f'\tset $specular "{_fmt_rgb(spe)}"\n')
        out.append('\tset $shininess "127"\n')
        out.append("}\n\n")
    return "".join(out)


def write_material_file(
    mesh_path: str, materials: Dict[str, dict], overwrite: bool = False, copy_textures: bool = False
) -> Optional[str]:
    """Write ``<mesh>.material`` unless it exists (and ``overwrite`` is off); copy textures if asked.

    Returns the material file path, or None when there is nothing to write.
    """
    if not materials:
        return None
    mat_file = os.path.splitext(mesh_path)[0] + ".material"
    if not os.path.isfile(mat_file) or overwrite:
        with open(mat_file, "w", encoding="utf-8") as handle:
            handle.write(material_text(materials))
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
    for i, match in enumerate(matches):
        body = text[match.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        info = {"textures": {}, "diffuse": None, "template": match.group(2)}
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
