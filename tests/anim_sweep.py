# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Stock VDF / SDF ANIM round-trip sweep through headless Maya (one process).

    mayapy.exe tests/anim_sweep.py <results.jsonl> <folder> [<folder> ...]

Every ``.vdf`` / ``.sdf`` under the folders that carries an ANIM block is read with the core,
imported into Maya, exported again and re-read; the slot table and every part's keys must come
back (frames equal, values within 1e-3). Parts the writer skips by its own naming rules
(LOD2/3 parts without a same-name LOD1 twin, keyed or not) are a known drop. One JSON line per file. Stock files are
never committed.
"""

import glob
import json
import os
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "maya"))

try:
    import maya.standalone  # noqa: F401
except ImportError:
    print("maya is not importable; the sweep needs mayapy")
    sys.exit(0)

maya.standalone.initialize(name="python")

import maya.cmds as cmds  # noqa: E402
import numpy as np  # noqa: E402

import _bootstrap  # noqa: F401,E402
from bz98maya import maya_io  # noqa: E402
from bz98maya.core import anim, sdf_io, vdf_io  # noqa: E402

maya_io.BLOCK_ON_ERRORS = False


def compare(source, back):
    """(problems, known): known = keyed or unkeyed LOD2/3 parts with no same-name LOD1 twin (Blender drops those too)."""
    problems = []
    known = []
    if source.meta.get("animations") != back.meta.get("animations"):
        problems.append("slot table differs")
    theirs = {n.name.lower(): n for n in back.nodes}
    ours = {n.name.lower() for n in source.nodes}
    for node in source.nodes:
        other = theirs.get(node.name.lower())
        if other is None:
            name = node.name.lower()
            lod = int((node.props or {}).get("lod") or 1)
            twin = name[:3] + "1" + name[4:]
            if lod > 1 and twin not in ours:
                known.append(node.name)
            else:
                problems.append("%s: part missing after the round trip" % node.name)
            continue
        if bool(node.anim) != bool(other.anim):
            problems.append("%s: keys %s -> %s" % (node.name, bool(node.anim), bool(other.anim)))
            continue
        for group in ("rot", "pos", "scale"):
            a, b = (node.anim or {}).get(group, []), (other.anim or {}).get(group, [])
            if group == "scale" and not source.meta.get("use_translation2"):
                continue
            if [f for f, _v in a] != [f for f, _v in b]:
                problems.append("%s %s frames differ" % (node.name, group))
                continue
            for (_f, va), (_g, vb) in zip(a, b):
                same = anim.quats_close(va, vb, 1e-3) if group == "rot" else np.allclose(va, vb, atol=1e-3)
                if not same:
                    problems.append("%s %s values differ" % (node.name, group))
                    break
    return problems, known


def run_file(path):
    is_vdf = path.lower().endswith(".vdf")
    reader = vdf_io.read_vdf if is_vdf else sdf_io.read_sdf
    source = reader(path)
    if not any(n.anim for n in source.nodes):
        return "skip", "ANIM block without keys"
    cmds.file(new=True, force=True)
    (maya_io.import_vdf if is_vdf else maya_io.import_sdf)(path)
    cmds.select(clear=True)
    out = os.path.join(tempfile.mkdtemp(prefix="bzanim_"), os.path.basename(path))
    (maya_io.export_vdf if is_vdf else maya_io.export_sdf)(out, {})
    problems, known = compare(source, reader(out))
    if problems:
        return "fail", problems[:6]
    if known:
        return "known-drop", "LOD2/3 part(s) with no same-name LOD1 twin, which Blender does not export either: %s" % ", ".join(known[:4])
    return "pass", ""


def main():
    results, folders = sys.argv[1], sys.argv[2:]
    files = []
    for folder in folders:
        for pattern in ("*.vdf", "*.sdf"):
            files += sorted(glob.glob(os.path.join(folder, "**", pattern), recursive=True))
    with open(results, "a", encoding="utf-8") as out:
        for path in files:
            with open(path, "rb") as handle:
                if b"ANIM" not in handle.read():
                    continue
            started = time.time()
            try:
                status, detail = run_file(path)
            except Exception as exc:
                status, detail = "fail", ["%s: %s" % (type(exc).__name__, str(exc)[:200])]
                traceback.print_exc()
            record = {"file": path, "status": status,
                      ("problems" if isinstance(detail, list) else "reason"): detail,
                      "seconds": round(time.time() - started, 2)}
            out.write(json.dumps(record) + "\n")
            out.flush()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
