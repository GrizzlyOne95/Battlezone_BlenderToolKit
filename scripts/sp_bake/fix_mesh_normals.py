"""Fix vertex normals and triangle winding of Ogre .mesh files in place.

Only bytes inside existing vertex/index buffers change (NORMAL and TANGENT
floats, triangle index order), so file size, vertex count, bone assignments,
submeshes, LODs and every other chunk are kept byte for byte.

  Tier 1 (always): triangles whose winding disagrees with all three stored
    normals are flipped (main and generated-LOD index lists); degenerate
    normals are replaced.
  Tier 2 (--recompute DEG): every normal is rederived: angle-weighted, welded
    by position so UV seams stay smooth, split where faces differ by more than
    DEG. A vertex the mesh does not split across a hard edge gets the blend of
    its corners (counted as "blended"); topology is never changed.
  Tier 2b (--split DEG): as --recompute, but a vertex whose corners need
    different normals is duplicated (all vertex streams and bone weights
    copied), so hard edges really are hard. Stock BZR meshes share many
    vertices across hard edges (avapc: 22%), which is where the "wonky"
    smoothing comes from; --recompute alone barely changes them.

Tangents (if present) are re-orthogonalised to the new normal, keeping w.

  python fix_mesh_normals.py IN.mesh OUT.mesh [--split 60] [--skip-material "glass|headlight"]
  python fix_mesh_normals.py IN.mesh --report          # measure only
"""

import argparse
import json
import re
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ogre_to_obj import face_normals, smooth_normals  # noqa: E402

M_HEADER, M_MESH, M_SUBMESH, M_SUBMESH_OPERATION = 0x1000, 0x3000, 0x4000, 0x4010
M_GEOMETRY, M_DECL, M_ELEM, M_VBUF, M_VBUF_DATA = 0x5000, 0x5100, 0x5110, 0x5200, 0x5210
M_LOD_LEVEL, M_LOD_USAGE, M_LOD_GENERATED = 0x8000, 0x8100, 0x8120
M_EDGE_LISTS = 0xB000
VES_POSITION, VES_NORMAL, VES_TANGENT = 1, 4, 8
VET_FLOAT3, VET_FLOAT4 = 2, 3


def _chunks(d, p, end):
    while p + 6 <= end:
        cid, ln = struct.unpack_from("<HI", d, p)
        if ln < 6 or p + ln > end:
            return
        yield cid, p, p + ln
        p += ln


def _str_end(d, p):
    return d.index(b"\n", p) + 1


def _geometry(d, s, e):
    count = struct.unpack_from("<I", d, s + 6)[0]
    elems, bufs = [], {}
    for cid, cs, ce in _chunks(d, s + 10, e):
        if cid == M_DECL:
            for eid, es, _ in _chunks(d, cs + 6, ce):
                if eid == M_ELEM:
                    elems.append(struct.unpack_from("<5H", d, es + 6))  # source type sem off idx
        elif cid == M_VBUF:
            bind, size = struct.unpack_from("<2H", d, cs + 6)
            for did, ds, _ in _chunks(d, cs + 10, ce):
                if did == M_VBUF_DATA:
                    bufs[bind] = (ds + 6, size)
    return {"count": count, "elems": elems, "bufs": bufs}


def _attr(d, g, sem):
    """(file offsets (count,), float count, values (count,n)) of a float attribute or None."""
    for src, vtype, s, off, idx in g["elems"]:
        if s == sem and idx == 0:
            if vtype not in (VET_FLOAT3, VET_FLOAT4):
                raise ValueError(f"semantic {sem} has non-float type {vtype}")
            n = 3 if vtype == VET_FLOAT3 else 4
            base, stride = g["bufs"][src]
            offs = base + off + stride * np.arange(g["count"])
            vals = np.array([struct.unpack_from(f"<{n}f", d, o) for o in offs], np.float64)
            return offs, n, vals
    return None


def _indices(d, p, n, idx32):
    dt = "<u4" if idx32 else "<u2"
    return {"off": p, "idx32": idx32, "idx": np.frombuffer(d, dt, n, p).astype(np.int64)}


def parse(d):
    if struct.unpack_from("<H", d, 0)[0] != M_HEADER:
        raise ValueError("not an Ogre mesh")
    p = _str_end(d, 2)
    cid, ms, me = next(_chunks(d, p, len(d)))
    if cid != M_MESH:
        raise ValueError("no M_MESH")
    shared, subs, lods, edges = None, [], [], False
    for cid, cs, ce in _chunks(d, ms + 7, me):
        if cid == M_GEOMETRY:
            shared = _geometry(d, cs, ce)
        elif cid == M_SUBMESH:
            q = _str_end(d, cs + 6)
            material = d[cs + 6:q - 1].decode("latin-1")
            use_shared = d[q] != 0
            n = struct.unpack_from("<I", d, q + 1)[0]
            idx32 = d[q + 5] != 0
            ib = _indices(d, q + 6, n, idx32)
            q += 6 + n * (4 if idx32 else 2)
            geom, op = None, 4
            for sid, ss, se in _chunks(d, q, ce):
                if sid == M_GEOMETRY:
                    geom = _geometry(d, ss, se)
                elif sid == M_SUBMESH_OPERATION:
                    op = struct.unpack_from("<H", d, ss + 6)[0]
            subs.append({"material": material, "shared": use_shared, "geom": geom, "op": op, "ib": ib})
        elif cid == M_LOD_LEVEL:
            q = _str_end(d, cs + 6) + 2  # strategy name, u16 level count
            for uid, us, ue in _chunks(d, q, ce):
                if uid != M_LOD_USAGE:
                    continue
                level, si = [], 0
                for gid, gs, ge in _chunks(d, us + 10, ue):
                    if gid == M_LOD_GENERATED:
                        n = struct.unpack_from("<I", d, gs + 6)[0]
                        level.append((si, _indices(d, gs + 11, n, d[gs + 10] != 0)))
                        si += 1
                lods.append(level)
        elif cid == M_EDGE_LISTS:
            edges = True
    for s in subs:
        s["g"] = shared if s["shared"] else s["geom"]
    return subs, lods, edges


def _reversed(pos, nrm, tri):
    fn, area2 = face_normals(pos, tri)
    dots = np.einsum("fkj,fj->fk", nrm[tri], fn)
    return (dots < 0).all(axis=1) & (area2 > 1e-12)


def fix(data, recompute=None, skip=None, flip=True):
    d = bytearray(data)
    subs, lods, edges = parse(bytes(d))
    report = {"submeshes": [], "edge_lists": edges, "lod_levels": len(lods)}
    groups = {}  # geometry -> submesh indices
    for i, s in enumerate(subs):
        groups.setdefault(id(s["g"]), []).append(i)
    for members in groups.values():
        g = subs[members[0]]["g"]
        if g is None:
            continue
        P, N = _attr(d, g, VES_POSITION), _attr(d, g, VES_NORMAL)
        if P is None or N is None:
            continue
        pos, (noffs, _, nrm), T = P[2], N, _attr(d, g, VES_TANGENT)
        nrm = nrm[:, :3]
        used = [i for i in members if subs[i]["op"] == 4 and not (skip and skip.search(subs[i]["material"]))]
        tris, flipped = {}, {}
        for i in members:
            s = subs[i]
            tri = s["ib"]["idx"].reshape(-1, 3) if s["op"] == 4 else np.zeros((0, 3), np.int64)
            rev = _reversed(pos, nrm, tri) if i in used and flip else np.zeros(len(tri), bool)
            tri = tri.copy()
            tri[rev] = tri[rev][:, [0, 2, 1]]
            tris[i], flipped[i] = tri, rev
            bad = ~np.isfinite(nrm).all(1) | (np.abs(np.linalg.norm(nrm, axis=1) - 1) > 0.05)
            report["submeshes"].append({
                "material": s["material"], "tris": int(len(tri)), "skipped": i not in used,
                "reversed": int(rev.sum()), "degenerate_normals": int(bad[np.unique(tri)].sum()) if len(tri) else 0})
        if not used:
            continue
        verts = np.unique(np.concatenate([tris[i].reshape(-1) for i in used]))
        new = nrm.copy()
        alltri = np.concatenate([tris[i] for i in used])
        if recompute is not None:
            corner = smooth_normals(pos, alltri, recompute)
            acc = np.zeros_like(nrm)
            np.add.at(acc, alltri.reshape(-1), corner)
            ln = np.linalg.norm(acc, axis=1)
            ok = ln > 1e-8
            new[ok] = acc[ok] / ln[ok, None]
            # blended: a corner more than 30 deg from the vertex's single normal
            dev = np.einsum("ij,ij->i", corner, new[alltri.reshape(-1)])
            worst = np.ones(len(nrm))
            np.minimum.at(worst, alltri.reshape(-1), dev)
            report.setdefault("blended_vertices", 0)
            report["blended_vertices"] += int((worst[verts] < np.cos(np.radians(30))).sum())
            change = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", new[verts], nrm[verts]
                                                         / np.maximum(np.linalg.norm(nrm[verts], axis=1), 1e-12)[:, None]), -1, 1)))
            report.setdefault("normal_change_deg", [])
            report["normal_change_deg"].append({"median": float(np.median(change)), "p95": float(np.percentile(change, 95)),
                                                "max": float(change.max()), "verts": int(len(verts))})
        else:
            bad = ~np.isfinite(nrm).all(1) | (np.abs(np.linalg.norm(nrm, axis=1) - 1) > 0.05)
            if bad[verts].any():
                corner = smooth_normals(pos, alltri, 60)
                acc = np.zeros_like(nrm)
                np.add.at(acc, alltri.reshape(-1), corner)
                fixv = bad & (np.linalg.norm(acc, axis=1) > 1e-8)
                new[fixv] = acc[fixv] / np.linalg.norm(acc[fixv], axis=1)[:, None]
        for v in verts:
            struct.pack_into("<3f", d, int(noffs[v]), *new[v])
        if T is not None:
            toffs, tn, tan = T
            t = tan[:, :3] - new * np.einsum("ij,ij->i", tan[:, :3], new)[:, None]
            ln = np.linalg.norm(t, axis=1)
            for v in verts:
                if ln[v] > 1e-8:
                    struct.pack_into("<3f", d, int(toffs[v]), *(t[v] / ln[v]))
        for i in used:
            ib = subs[i]["ib"]
            fmt = "<%d%s" % (ib["idx"].size, "I" if ib["idx32"] else "H")
            struct.pack_into(fmt, d, ib["off"], *tris[i].reshape(-1).tolist())
            # generated LODs: same test against the corrected normals
            for level in lods:
                for si, lib in level:
                    if si != i or not lib["idx"].size:
                        continue
                    lt = lib["idx"].reshape(-1, 3).copy()
                    rev = _reversed(pos, new, lt) if flip else np.zeros(len(lt), bool)
                    lt[rev] = lt[rev][:, [0, 2, 1]]
                    report.setdefault("lod_reversed", 0)
                    report["lod_reversed"] += int(rev.sum())
                    fmt = "<%d%s" % (lt.size, "I" if lib["idx32"] else "H")
                    struct.pack_into(fmt, d, lib["off"], *lt.reshape(-1).tolist())
    assert len(d) == len(data)
    return bytes(d), report


# --- split mode: duplicate vertices that span a hard edge -------------------

M_SUBMESH_BA, M_MESH_BA = 0x4100, 0x7000


def _node(d, s, e):
    """Container chunks on the vertex-data path -> dict tree; anything else stays raw bytes."""
    cid = struct.unpack_from("<H", d, s)[0]
    if cid == M_MESH:
        body_end = s + 7
    elif cid == M_SUBMESH:
        q = _str_end(d, s + 6)
        n, idx32 = struct.unpack_from("<I", d, q + 1)[0], d[q + 5] != 0
        body_end = q + 6 + n * (4 if idx32 else 2)
    elif cid == M_GEOMETRY:
        body_end = s + 10
    elif cid == M_VBUF:
        body_end = s + 10
    else:
        return {"id": cid, "raw": bytes(d[s:e])}
    return {"id": cid, "body": bytearray(d[s + 6:body_end]),
            "kids": [_node(d, cs, ce) for _, cs, ce in _chunks(d, body_end, e)]}


def _emit(n):
    if "raw" in n:
        return n["raw"]
    payload = bytes(n["body"]) + b"".join(_emit(k) for k in n["kids"])
    return struct.pack("<HI", n["id"], 6 + len(payload)) + payload


def _geom_info(g):
    """Decode a geometry node: count, element list, {source: (vbuf node, stride)}."""
    count = struct.unpack_from("<I", g["body"], 0)[0]
    elems, bufs = [], {}
    for k in g["kids"]:
        if k["id"] == M_DECL:
            raw = k["raw"]
            for eid, es, _ in _chunks(raw, 6, len(raw)):
                if eid == M_ELEM:
                    elems.append(struct.unpack_from("<5H", raw, es + 6))
        elif k["id"] == M_VBUF:
            bind, stride = struct.unpack_from("<2H", k["body"], 0)
            bufs[bind] = (k, stride)
    return count, elems, bufs


def _vbuf_array(vb, stride, count):
    data = next(k for k in vb["kids"] if k["id"] == M_VBUF_DATA)
    arr = np.frombuffer(data["raw"], np.uint8, count * stride, 6).reshape(count, stride).copy()
    return data, arr


def _get(arrs, elems, sem, n_max=4):
    for src, vtype, s, off, idx in elems:
        if s == sem and idx == 0:
            n = 3 if vtype == VET_FLOAT3 else 4 if vtype == VET_FLOAT4 else None
            if n is None:
                raise ValueError(f"semantic {sem} has non-float type {vtype}")
            a = arrs[src]
            return src, off, n, a[:, off:off + 4 * n].copy().view("<f4").reshape(-1, n).astype(np.float64)
    return None


def _put(arrs, src, off, vals):
    a = arrs[src]
    a[:, off:off + 4 * vals.shape[1]] = vals.astype("<f4").view(np.uint8).reshape(len(a), -1)


def split_fix(data, smooth_deg, skip=None, flip=True, cluster_deg=8.0):
    d = bytes(data)
    subs_info, lods, edges = parse(d)
    p = _str_end(d, 2)
    _, ms, me = next(_chunks(d, p, len(d)))
    mesh = _node(d, ms, me)
    report = {"submeshes": [], "lod_levels": len(lods)}
    if edges:
        # stencil-shadow edge lists index the old vertices; Ogre rebuilds them on demand
        mesh["kids"] = [k for k in mesh["kids"] if k["id"] != M_EDGE_LISTS]
        report["edge_lists_dropped"] = True
    sub_nodes = [k for k in mesh["kids"] if k["id"] == M_SUBMESH]
    shared_node = next((k for k in mesh["kids"] if k["id"] == M_GEOMETRY), None)

    # group submeshes by the geometry they draw from
    groups = {}
    for i, (si, sn) in enumerate(zip(subs_info, sub_nodes)):
        if si["shared"]:
            groups.setdefault("shared", []).append(i)
        else:
            groups[i] = [i]
    cos_cluster = np.cos(np.radians(cluster_deg))
    for key, members in groups.items():
        if key == "shared":
            gnode, ba_parent, ba_id = shared_node, mesh, M_MESH_BA
        else:
            sn = sub_nodes[members[0]]
            gnode = next((k for k in sn["kids"] if k["id"] == M_GEOMETRY), None)
            ba_parent, ba_id = sn, M_SUBMESH_BA
        if gnode is None:
            continue
        count, elems, bufs = _geom_info(gnode)
        arr_nodes, arrs = {}, {}
        for src, (vb, stride) in bufs.items():
            arr_nodes[src], arrs[src] = _vbuf_array(vb, stride, count)
        P, N, T = _get(arrs, elems, VES_POSITION), _get(arrs, elems, VES_NORMAL), _get(arrs, elems, VES_TANGENT)
        if P is None or N is None:
            continue
        pos, nrm = P[3], N[3][:, :3]
        used = [i for i in members if subs_info[i]["op"] == 4
                and not (skip and skip.search(subs_info[i]["material"]))]
        tris = {}
        for i in members:
            tri = subs_info[i]["ib"]["idx"].reshape(-1, 3).copy() if subs_info[i]["op"] == 4 else None
            rev = _reversed(pos, nrm, tri) if (i in used and flip) else np.zeros(0 if tri is None else len(tri), bool)
            if tri is not None:
                tri[rev] = tri[rev][:, [0, 2, 1]]
            tris[i] = tri
            report["submeshes"].append({"material": subs_info[i]["material"], "skipped": i not in used,
                                        "tris": 0 if tri is None else int(len(tri)), "reversed": int(rev.sum())})
        if not used:
            continue
        alltri = np.concatenate([tris[i] for i in used])
        corner = smooth_normals(pos, alltri, smooth_deg)
        cv = alltri.reshape(-1)
        remap = cv.copy()
        new_n = {}  # vertex index -> normal
        dup_src = []
        order = np.argsort(cv, kind="stable")
        bounds = np.flatnonzero(np.diff(cv[order])) + 1
        for grp in np.split(order, bounds):
            v = int(cv[grp[0]])
            reps, members_c, zero = [], [], []
            for c in grp:
                n = corner[c]
                if n @ n < 0.5:  # corner of a degenerate triangle: no normal of its own
                    zero.append(c)
                    continue
                for j, r in enumerate(reps):
                    if r @ n >= cos_cluster:
                        members_c[j].append(c)
                        break
                else:
                    reps.append(n)
                    members_c.append([c])
            if not members_c:
                remap[zero] = v  # only degenerate faces use it: keep the stored normal
                continue
            members_c[0] += zero
            for j, cs in enumerate(members_c):
                m = corner[cs].sum(0)
                m /= max(np.linalg.norm(m), 1e-20)
                if j == 0:
                    tgt = v
                else:
                    tgt = count + len(dup_src)
                    dup_src.append(v)
                remap[cs] = tgt
                new_n[tgt] = m
        new_count = count + len(dup_src)
        idx32_all = all(subs_info[i]["ib"]["idx32"] for i in used)
        if new_count > 65535 and not idx32_all:
            raise ValueError(f"split needs {new_count} vertices; 16-bit indices")
        dup_src = np.array(dup_src, np.int64)
        for src in arrs:
            arrs[src] = np.concatenate([arrs[src], arrs[src][dup_src]]) if len(dup_src) else arrs[src]
        # normals (and tangents) for every vertex the fixed submeshes use
        Nsrc, Noff, Nn, Nvals = _get(arrs, elems, VES_NORMAL)
        ids = np.array(sorted(new_n), np.int64)
        Nvals[ids, :3] = np.array([new_n[i] for i in ids])
        _put(arrs, Nsrc, Noff, Nvals)
        T = _get(arrs, elems, VES_TANGENT)
        if T is not None:
            Tsrc, Toff, Tn, Tv = T
            nn = Nvals[:, :3]
            t = Tv[:, :3] - nn * np.einsum("ij,ij->i", Tv[:, :3], nn)[:, None]
            ln = np.linalg.norm(t, axis=1)
            ok = np.zeros(len(t), bool); ok[ids] = True; ok &= ln > 1e-8
            Tv[ok, :3] = t[ok] / ln[ok, None]
            _put(arrs, Tsrc, Toff, Tv)
        for src, dn in arr_nodes.items():
            dn["raw"] = struct.pack("<HI", M_VBUF_DATA, 6 + arrs[src].size) + arrs[src].tobytes()
        struct.pack_into("<I", gnode["body"], 0, new_count)
        # index buffers of the fixed submeshes
        at = 0
        for i in used:
            n = len(tris[i])
            new_tri = remap[at * 3:(at + n) * 3]
            at += n
            body = sub_nodes[i]["body"]  # the index list ends the submesh body
            fmt = "<%d%s" % (new_tri.size, "I" if subs_info[i]["ib"]["idx32"] else "H")
            struct.pack_into(fmt, body, len(body) - struct.calcsize(fmt), *new_tri.tolist())
        # bone assignments for the copies
        if len(dup_src):
            copies = {}
            for j, v in enumerate(dup_src):
                copies.setdefault(int(v), []).append(count + j)
            kids, extra = ba_parent["kids"], []
            last = -1
            for k_i, k in enumerate(kids):
                if k["id"] == ba_id:
                    last = k_i
                    vi, bone, w = struct.unpack_from("<IHf", k["raw"], 6)
                    for nv in copies.get(vi, ()):
                        extra.append({"id": ba_id, "raw": struct.pack("<HIIHf", ba_id, 16, nv, bone, w)})
            if extra:
                kids[last + 1:last + 1] = extra
            report["bone_assignment_copies"] = report.get("bone_assignment_copies", 0) + len(extra)
        report.setdefault("geometries", []).append({"vertices": int(count), "added": int(len(dup_src))})
    out = d[:ms] + _emit(mesh) + d[me:]
    return out, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path, nargs="?")
    ap.add_argument("--recompute", type=float, metavar="DEG", help="rederive all normals, split angle DEG")
    ap.add_argument("--skip-material", default="glass|headlight", help="regex; matching submeshes are untouched")
    ap.add_argument("--split", type=float, metavar="DEG",
                    help="rederive normals and duplicate vertices that span edges sharper than DEG")
    ap.add_argument("--keep-winding", action="store_true")
    ap.add_argument("--report", action="store_true", help="measure only, write nothing")
    a = ap.parse_args()
    skip = re.compile(a.skip_material, re.I) if a.skip_material else None
    if a.split is not None:
        out, rep = split_fix(a.src.read_bytes(), a.split, skip, not a.keep_winding)
    else:
        out, rep = fix(a.src.read_bytes(), a.recompute, skip, not a.keep_winding)
    print(json.dumps(rep, indent=1))
    if a.dst and not a.report:
        a.dst.write_bytes(out)
        print("wrote", a.dst)


if __name__ == "__main__":
    main()
