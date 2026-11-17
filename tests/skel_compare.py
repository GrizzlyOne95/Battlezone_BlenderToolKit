# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Comparison of two neutral scenes after a skeleton / skinned mesh round trip (shared by the
mayapy integration tests and the stock skeleton sweep). Pure Python + numpy."""

from typing import Dict, List

import numpy as np

TOL = 1e-4
MAX_INFLUENCES = 3  # Blender's bone-assignment limit


def _q_close(a, b, tol=TOL):
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    return min(np.abs(a - b).max(), np.abs(a + b).max()) <= tol


def top_weights(influences, id_map=None):
    """Blender's per-vertex rule: three heaviest, renormalised; {bone id: weight}."""
    items = [((id_map or {}).get(b, b), float(w)) for b, w in influences if (id_map is None or b in id_map)]
    items.sort(key=lambda item: item[1], reverse=True)
    items = items[:MAX_INFLUENCES]
    total = sum(w for _b, w in items)
    if total > 0:
        items = [(b, w / total) for b, w in items if w != 0]
    return dict(items)


def compare_skeletons(src, out, tol=TOL, animation=True) -> List[str]:
    problems: List[str] = []
    ids = {b.id: i for i, b in enumerate(sorted(src.bones, key=lambda b: b.id))}
    a = {b.name: b for b in src.bones}
    b = {x.name: x for x in out.bones}
    if set(a) != set(b):
        return ["bone names differ: -%s +%s" % (sorted(set(a) - set(b))[:5], sorted(set(b) - set(a))[:5])]
    for name, bone in a.items():
        other = b[name]
        if ids[bone.id] != other.id:
            problems.append("bone %s id %d -> %d (expected %d)" % (name, bone.id, other.id, ids[bone.id]))
        if bone.parent != other.parent:
            problems.append("bone %s parent %s -> %s" % (name, bone.parent, other.parent))
        if np.abs(np.array(bone.position) - np.array(other.position)).max() > tol:
            problems.append("bone %s position" % name)
        if not _q_close(bone.orientation, other.orientation, tol):
            problems.append("bone %s orientation" % name)
        if np.abs(np.array(bone.scale) - np.array(other.scale)).max() > tol:
            problems.append("bone %s scale" % name)
    if not animation:
        return problems
    if [c.name for c in src.clips] != [c.name for c in out.clips]:
        return problems + ["clip names %s -> %s" % ([c.name for c in src.clips][:6], [c.name for c in out.clips][:6])]
    for clip, other in zip(src.clips, out.clips):
        if abs(clip.length - other.length) > tol:
            problems.append("clip %s length %.6f -> %.6f" % (clip.name, clip.length, other.length))
        mine = {t.bone: t for t in clip.tracks if t.times}
        theirs = {t.bone: t for t in other.tracks if t.times}
        if set(mine) != set(theirs):
            problems.append("clip %s tracks differ: -%s +%s" % (
                clip.name, sorted(set(mine) - set(theirs))[:4], sorted(set(theirs) - set(mine))[:4]))
            continue
        for bone, track in mine.items():
            t2 = theirs[bone]
            if len(track.times) != len(t2.times) or np.abs(np.array(track.times) - np.array(t2.times)).max() > tol:
                problems.append("clip %s bone %s key times" % (clip.name, bone))
                continue
            if np.abs(np.array(track.translations) - np.array(t2.translations)).max() > tol:
                problems.append("clip %s bone %s translations" % (clip.name, bone))
            if not all(_q_close(x, y, tol) for x, y in zip(track.rotations, t2.rotations)):
                problems.append("clip %s bone %s rotations" % (clip.name, bone))
            if np.abs(np.array(track.scales) - np.array(t2.scales)).max() > tol:
                problems.append("clip %s bone %s scales" % (clip.name, bone))
    return problems


def corners(mesh):
    """Per face corner: position, uv0, normal and the vertex's top-3 weights."""
    rows = []
    loop = 0
    for face in mesh.faces:
        for vi in face:
            rows.append((
                mesh.positions[vi],
                mesh.uv_sets[0][loop] if mesh.uv_sets else None,
                mesh.normals[loop] if mesh.normals else None,
                mesh.weights[vi] if mesh.weights is not None else None,
            ))
            loop += 1
    return rows


def compare_meshes(src, out, id_map: Dict[int, int], tol=TOL) -> List[str]:
    """Nodes matched by name; geometry corner by corner; weights after Blender's top-3 normalisation."""
    problems: List[str] = []
    mine = {n.name: n for n in src.nodes if n.mesh is not None}
    theirs = {n.name: n for n in out.nodes if n.mesh is not None}
    if set(mine) != set(theirs):
        return ["mesh nodes differ: -%s +%s" % (sorted(set(mine) - set(theirs))[:4], sorted(set(theirs) - set(mine))[:4])]
    for name, node in mine.items():
        other = theirs[name]
        if node.mesh.face_materials and other.mesh.face_materials and node.mesh.face_materials[0] != other.mesh.face_materials[0]:
            problems.append("%s material %s -> %s" % (name, node.mesh.face_materials[0], other.mesh.face_materials[0]))
        a, b = corners(node.mesh), corners(other.mesh)
        if len(a) != len(b):
            problems.append("%s corner count %d -> %d" % (name, len(a), len(b)))
            continue
        pos_err = uv_err = nrm_err = w_err = 0.0
        for (p0, u0, n0, w0), (p1, u1, n1, w1) in zip(a, b):
            pos_err = max(pos_err, float(np.abs(np.array(p0) - np.array(p1)).max()))
            if u0 is not None and u1 is not None:
                uv_err = max(uv_err, float(np.abs(np.array(u0) - np.array(u1)).max()))
            if n0 is not None and n1 is not None:
                n0a, n1a = np.array(n0), np.array(n1)
                n0a = n0a / (np.linalg.norm(n0a) or 1.0)
                n1a = n1a / (np.linalg.norm(n1a) or 1.0)
                nrm_err = max(nrm_err, float(np.abs(n0a - n1a).max()))
            if w0 is not None or w1 is not None:
                x = top_weights(w0 or [], id_map)
                y = top_weights(w1 or [])
                for key in set(x) | set(y):
                    w_err = max(w_err, abs(x.get(key, 0.0) - y.get(key, 0.0)))
        if pos_err > tol:
            problems.append("%s positions off by %.2e" % (name, pos_err))
        if uv_err > tol:
            problems.append("%s uvs off by %.2e" % (name, uv_err))
        if nrm_err > 1e-3:
            problems.append("%s normals off by %.2e" % (name, nrm_err))
        if w_err > tol:
            problems.append("%s weights off by %.2e" % (name, w_err))
    return problems
