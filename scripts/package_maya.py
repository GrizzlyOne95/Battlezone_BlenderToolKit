# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Build the Maya plug-in release archive ``Battlezone_MayaToolKit-<version>.zip``.

    python scripts/package_maya.py [--version v1.5.0] [--out-dir DIR]

Archive layout (unzip into ``Documents/maya/modules`` and it works)::

    bz98maya.mod                 module description, points at ./bz98maya
    bz98maya/
      plug-ins/bz98maya_plugin.py
      bz98maya/...               the plug-in's Python package
      bz98tools/...              bundled copy of the toolkit's serializers
      README.md, LICENSE

Maya only reads ``.mod`` files that sit directly in a MAYA_MODULE_PATH folder,
so the ``.mod`` goes beside the module folder, not inside it. The in-repo
``maya/bz98maya.mod`` (path ``.``) stays the developer install.

The plug-in finds the bundled ``bz98tools`` through
``bz98maya/core/toolkit.py`` (``<module root>/bz98tools``). The bundle is the
whole ``bz98tools`` folder minus what the plug-in never reads: terrain art
(``map_assets/``), the native Ogre backend (``ogrefast/vendor/``; without it
``ogrefast`` installs its pure-Python facade, as it already does on Maya's
Python 3.13), the Ogre command-line tools and other native files (``.pyd`` /
``.dll`` / ``.exe`` / ``.so``), logs and byte-code. The one native
file kept is ``lib/lzo_bridge.dll``: ``zfs_reader.py`` loads it to read
textures out of the stock ZFS archive (the import option MapTextureZFS).

Stdlib only; runs on Linux CI and Windows. Exits non-zero when the
self-check of the written archive fails.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAYA_DIR = os.path.join(REPO_ROOT, "maya")
TOOLKIT_DIR = os.path.join(REPO_ROOT, "bz98tools")

MODULE = "bz98maya"
ARCHIVE_PREFIX = "Battlezone_MayaToolKit-"

# Paths are relative to bz98tools/, forward slashes. __pycache__ is skipped everywhere.
EXCLUDED_DIRS = {"map_assets", "ogrefast/vendor"}
EXCLUDED_PATTERNS = ("*.pyc", "*.pyo", "*.pyd", "*.so", "*.dll", "*.exe", "*.log")
KEPT_NATIVE = {"lib/lzo_bridge.dll"}
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

REQUIRED = (
    MODULE + ".mod",
    MODULE + "/plug-ins/bz98maya_plugin.py",
    MODULE + "/bz98maya/__init__.py",
    MODULE + "/bz98maya/menu.py",
    MODULE + "/bz98maya/core/toolkit.py",
    MODULE + "/bz98tools/bzrmodelporter/bzgeo_serializer.py",  # toolkit.py's marker file
    MODULE + "/bz98tools/_addon_core.py",  # read as source by core/schema.py
    MODULE + "/bz98tools/import_geo.py",  # read as source by core/mapload.py
    MODULE + "/bz98tools/ogrefast/pure/serializer.py",
    MODULE + "/bz98tools/lib/lzo_bridge.dll",
    MODULE + "/README.md",
    MODULE + "/LICENSE",
)


def addon_version() -> str:
    with open(os.path.join(TOOLKIT_DIR, "__init__.py"), "r", encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r'"version"\s*:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)', text)
    if not match:
        match = re.search(r'bl_info\["version"\]\s*=\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)', text)
    if not match:
        raise SystemExit("Could not read the add-on version from bz98tools/__init__.py")
    return "v" + ".".join(match.groups())


def module_version(release_version: str):
    """'v1.5.0' -> '1.5.0', 'v1.5.0-rc' -> '1.5.0'; None when the label has no numeric part ('ci')."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", release_version)
    return match.group(1) if match else None


def module_description(release_version: str) -> str:
    """The repo's .mod with the release's version and its module path changed from '.' to './bz98maya'.

    A label without a numeric part (``ci``) keeps the repo .mod's version.
    """
    numeric = module_version(release_version)
    with open(os.path.join(MAYA_DIR, MODULE + ".mod"), "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "+" and parts[1] == MODULE:
            parts[-1] = "./" + MODULE
            if numeric:
                parts[2] = numeric
            line = " ".join(parts)
        out.append(line)
    if not any(line.startswith("+ ") and line.endswith("./" + MODULE) for line in out):
        raise SystemExit("maya/bz98maya.mod has no '+ bz98maya <version> .' line")
    return "\n".join(out) + "\n"


def _excluded(rel: str) -> bool:
    if rel in KEPT_NATIVE:
        return False
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_PATTERNS)


def _walk(root: str, skip_dirs=frozenset()):
    """Yield (abs_path, rel_path) of files under root, sorted, skipping __pycache__ and skip_dirs (relative)."""
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and prefix + d not in skip_dirs)
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            yield full, os.path.relpath(full, root).replace(os.sep, "/")


def entries(version: str):
    """(source path or None, archive name, text or None) for every archive member."""
    yield None, MODULE + ".mod", module_description(version)
    for full, rel in _walk(os.path.join(MAYA_DIR, "plug-ins")):
        if not _excluded(rel):
            yield full, MODULE + "/plug-ins/" + rel, None
    for full, rel in _walk(os.path.join(MAYA_DIR, MODULE)):
        if not _excluded(rel):
            yield full, MODULE + "/" + MODULE + "/" + rel, None
    for full, rel in _walk(TOOLKIT_DIR, skip_dirs=frozenset(EXCLUDED_DIRS)):
        if not _excluded(rel):
            yield full, MODULE + "/bz98tools/" + rel, None
    yield os.path.join(MAYA_DIR, "README.md"), MODULE + "/README.md", None
    yield os.path.join(REPO_ROOT, "LICENSE"), MODULE + "/LICENSE", None


def check_archive(path: str) -> None:
    """Raise SystemExit when the archive is not an unzip-into-modules Maya module."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        mod_text = zf.read(MODULE + ".mod").decode("utf-8") if MODULE + ".mod" in names else ""
    problems = []
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        problems.append(f"missing {missing}")
    top = {name.split("/", 1)[0] + ("/" if "/" in name else "") for name in names}
    if top != {MODULE + ".mod", MODULE + "/"}:
        problems.append(f"top level must be exactly {MODULE}.mod and {MODULE}/, got {sorted(top)}")
    for name in names:
        if "\\" in name:
            problems.append(f"backslash in {name}")
        segments = name.split("/")
        if "__pycache__" in segments:
            problems.append(f"byte-code folder in {name}")
        if name.startswith(MODULE + "/bz98tools/"):
            rel = name[len(MODULE + "/bz98tools/"):]
            if _excluded(rel) or any(rel.startswith(d + "/") for d in EXCLUDED_DIRS):
                problems.append(f"excluded file {name}")
    if not re.search(r"^\+ " + MODULE + r" \S+ \./" + MODULE + r"\s*$", mod_text, re.MULTILINE):
        problems.append(f"{MODULE}.mod does not point at ./{MODULE}: {mod_text!r}")
    if problems:
        raise SystemExit(f"Invalid Maya module archive {path}:\n  " + "\n  ".join(problems))


def build(version: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    archive = os.path.join(out_dir, ARCHIVE_PREFIX + version + ".zip")
    with zipfile.ZipFile(archive, "w") as zf:
        for source, name, text in entries(version):
            if text is not None:
                data = text.encode("utf-8")
            else:
                with open(source, "rb") as handle:
                    data = handle.read()
            # Fixed date and mode: the archive does not depend on checkout times or umask.
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    check_archive(archive)
    return archive


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="version label in the archive name (default: v<bl_info version>)")
    parser.add_argument("--out-dir", default=os.getcwd(), help="folder for the archive (default: current folder)")
    args = parser.parse_args(argv)
    archive = build(args.version or addon_version(), os.path.abspath(args.out_dir))
    with zipfile.ZipFile(archive) as zf:
        count = len(zf.namelist())
    print(f"Validated Maya module archive: {archive} ({count} files, {os.path.getsize(archive)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
