# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Stock skeleton round-trip sweep (headless Maya, one process).

    mayapy.exe tests/skeleton_sweep.py <skel_dir> <results.jsonl> [first] [last]

``skel_dir`` holds ``manifest.json`` and one folder per case (a skinned ``.mesh`` with its
``.skeleton``, or a lone ``.skeleton``), as the gather script lays them out. Each case is
imported into Maya, exported again, re-read with the toolkit's serializers and compared (bones,
bind pose, weights, clips and keys, mesh corners). One JSON line per case:
``{"id", "dir", "status": pass | known-drop | fail, "reason"/"problems", "seconds"}``.
Never commit the stock files this reads.
"""

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

import _bootstrap  # noqa: F401,E402
from bz98maya import maya_io  # noqa: E402
from bz98maya.core import ogre_io, skeleton_io  # noqa: E402
from skel_compare import compare_meshes, compare_skeletons  # noqa: E402

maya_io.BLOCK_ON_ERRORS = False


def unplaceable_drop(skeleton, problems):
    """known-drop reason when every problem sits in a stock clip Maya cannot hold exactly:
    keys past the declared clip length, or two keys of one track at the same time."""
    from bz98maya import rig

    over, twin = [], []
    for c in skeleton.clips:
        if any(t.times and max(t.times) > c.length + rig.CLIP_MARGIN for t in c.tracks):
            over.append(c.name)
        elif any(len(set(t.times)) != len(t.times) for t in c.tracks):
            twin.append(c.name)
    bad = over + twin
    if bad and all(any(("clip %s " % b) in p for b in bad) for p in problems):
        why = []
        if over:
            why.append("keys past the declared clip length (%s)" % ", ".join(over[:3]))
        if twin:
            why.append("two keys of a track at the same time (%s)" % ", ".join(twin[:3]))
        return "; ".join(why)
    return None


def run_case(case_dir: str, entry: dict):
    tmp = tempfile.mkdtemp(prefix="bzsweep_")
    options = {"export_animation": True, "export_materials": False, "export_tangents": False}
    cmds.file(new=True, force=True)
    if entry["mesh"]:
        mesh_path = os.path.join(case_dir, entry["mesh"])
        try:
            src = ogre_io.read_ogre_mesh(mesh_path, import_materials=False)
        except Exception as exc:  # the toolkit's own import path rejects these
            return "known-drop", "toolkit import rejects it: %s" % str(exc)[:120]
        notes = list(src.warnings)
        maya_io.import_ogre_mesh(mesh_path)
        cmds.select(clear=True)
        out_path = os.path.join(tmp, entry["mesh"])
        maya_io.export_redux_mesh(out_path, options)
        out = ogre_io.read_ogre_mesh(out_path, import_materials=False)
        if src.skeleton is None:
            return "known-drop", "linked skeleton missing"
        problems = compare_skeletons(src.skeleton, out.skeleton) if out.skeleton else ["no skeleton exported"]
        degenerate = 0
        for node in src.nodes:  # Maya cannot hold faces that repeat a vertex: compare without them
            if node.mesh is not None:
                before = sum(len(f) for f in node.mesh.faces)
                node.mesh = maya_io.drop_degenerate_faces(node.mesh)
                degenerate += before - sum(len(f) for f in node.mesh.faces)
        problems += compare_meshes(src, out, skeleton_io.renumbered(src.skeleton))
        pose_note = any("pose" in n for n in notes)
        if not problems and degenerate:
            return "known-drop", "%d face corner(s) collapsed or dropped (Maya cannot hold faces that repeat a vertex)" % degenerate
        if problems:
            reason = unplaceable_drop(src.skeleton, problems)
            return ("known-drop", reason) if reason else ("fail", problems[:8])
        return ("known-drop", "pose / shape-key data not imported") if pose_note else ("pass", "")
    skeleton_path = os.path.join(case_dir, entry["skeleton"])
    src = skeleton_io.read_skeleton(skeleton_path)
    maya_io.import_skeleton(skeleton_path)
    cmds.select(clear=True)
    out_path = os.path.join(tmp, entry["skeleton"])
    maya_io.export_skeleton(out_path, options)
    problems = compare_skeletons(src, skeleton_io.read_skeleton(out_path))
    if problems:
        reason = unplaceable_drop(src, problems)
        return ("known-drop", reason) if reason else ("fail", problems[:8])
    return ("pass", "")


def main():
    skel_dir, results = sys.argv[1], sys.argv[2]
    first = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    last = int(sys.argv[4]) if len(sys.argv) > 4 else 10 ** 9
    with open(os.path.join(skel_dir, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    with open(results, "a", encoding="utf-8") as out:
        for entry in manifest:
            if not first <= entry["id"] <= last:
                continue
            started = time.time()
            try:
                status, detail = run_case(os.path.join(skel_dir, entry["dir"]), entry)
            except Exception as exc:
                status, detail = "fail", ["%s: %s" % (type(exc).__name__, str(exc)[:200])]
                traceback.print_exc()
            record = {
                "id": entry["id"], "dir": entry["dir"], "src": entry["src"], "status": status,
                ("problems" if isinstance(detail, list) else "reason"): detail,
                "seconds": round(time.time() - started, 2),
            }
            out.write(json.dumps(record) + "\n")
            out.flush()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
