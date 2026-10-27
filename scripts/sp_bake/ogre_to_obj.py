"""Convert BZR Ogre .mesh files to OBJ for Substance Painter baking, and audit normals.

Each mesh becomes <out>/obj/<mesh>.obj with one `usemtl` group per Ogre
material, so Painter creates one texture set per material.  A manifest.json
lists every mesh with its materials and the tangent-space normal map each
material uses (resolved from the .material files' NormalMap alias and
converted from DDS to PNG so Painter can import it).

The normal audit compares the stored vertex normals against the geometric
face normals of the triangles that use them:
  * flipped   - stored normal points away from the face (dot < 0)
  * skewed    - stored normal is > --skew-deg off the face normal
  * degenerate- zero/NaN normal, or non-unit length
Results go to normals_audit.json / .csv.  --fix-normals writes recomputed
smooth normals (angle-split at --smooth-deg) into the OBJ for meshes that
fail the audit, instead of the stored ones.

Usage:
  python -I ogre_to_obj.py --assets "<BZR>/BZ_ASSETS" --out D:/bzr_bake [--only avapc]
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]


def _load_reader():
    # bz98tools/__init__.py boots the Blender addon (imports bpy); register a
    # bare package so the pure submodules import without running it.
    pkg = types.ModuleType("bz98tools")
    pkg.__path__ = [str(REPO / "bz98tools")]
    sys.modules["bz98tools"] = pkg
    from bz98tools.ogrefast.pure import import_compat as ic
    from bz98tools.bzrmodelporter.ogremesh_serializer import MeshSerializer
    return ic, MeshSerializer


ic, MeshSerializer = _load_reader()


# ---------------------------------------------------------------- materials

ALIAS_RE = re.compile(r"set_texture_alias\s+(\w+)\s+(\S+)")
MAT_RE = re.compile(r"^\s*material\s+(\S+)", re.M)


def parse_materials(material_dirs: list[Path]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for folder in material_dirs:
        for path in folder.rglob("*.material"):
            text = path.read_text(errors="replace")
            # split on material headers; aliases belong to the preceding header
            heads = list(MAT_RE.finditer(text))
            for i, m in enumerate(heads):
                body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
                out.setdefault(m.group(1), {}).update(
                    {k: v for k, v in ALIAS_RE.findall(body)})
    return out


def index_textures(texture_dirs: list[Path]) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for folder in texture_dirs:
        for path in folder.rglob("*"):
            if path.suffix.lower() in (".dds", ".png", ".tga"):
                idx.setdefault(path.name.lower(), path)
    return idx


def dds_size(path: Path):
    """(width, height) from a DDS/PNG header, or None."""
    try:
        with path.open("rb") as fh:
            head = fh.read(24)
        if head[:4] == b"DDS ":
            return int.from_bytes(head[16:20], "little"), int.from_bytes(head[12:16], "little")
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except OSError:
        pass
    return None


def dds_to_png(src: Path, dst: Path) -> bool:
    if dst.exists():
        return True
    try:
        from PIL import Image
        Image.open(src).save(dst)
        return True
    except Exception as exc:  # BC5/ATI2 etc. need texconv
        print(f"  ! could not convert {src.name}: {exc}")
        return False


# --------------------------------------------------------------- geometry

def read_mesh(path: Path):
    with path.open("rb") as stream:
        src = MeshSerializer(stream).read()
    shared = (ic._wire_geometry_from_legacy(src.shared_vertex_data)
              if src.shared_vertex_data is not None else None)
    parts = []
    for i, sm in enumerate(src.submesh_list):
        if int(sm.operation_type) != int(ic.OT.TRIANGLE_LIST):
            print(f"  ! {path.name} submesh {i}: op {sm.operation_type} skipped")
            continue
        geo = shared if sm.use_shared_vertices else (
            ic._wire_geometry_from_legacy(sm.vertex_data) if sm.vertex_data is not None else None)
        if geo is None:
            continue
        pos = ic._decode_semantic(geo, {"POSITION"})
        nrm = ic._decode_semantic(geo, {"NORMAL"})
        uv = ic._decode_semantic(geo, {"TEXCOORD"})
        tri = np.asarray(sm.get_index_array(), dtype=np.int64).reshape(-1, 3)
        parts.append({
            "name": sm.get_name() or f"SubMesh{i}",
            "material": sm.material_name or f"{path.stem}_{i}",
            "pos": pos[:, :3].astype(np.float64),
            "nrm": None if nrm is None else nrm[:, :3].astype(np.float64),
            "uv": None if uv is None else uv[:, :2].astype(np.float64),
            "tri": tri,
        })
    return parts


def face_normals(pos, tri):
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    n = np.cross(b - a, c - a)
    area2 = np.linalg.norm(n, axis=1)
    return n / np.maximum(area2, 1e-20)[:, None], area2


def audit_part(part, skew_deg):
    pos, nrm, tri = part["pos"], part["nrm"], part["tri"]
    res = {"tris": len(tri), "verts": len(pos), "has_normals": nrm is not None}
    if nrm is None or not len(tri):
        return res
    fn, area2 = face_normals(pos, tri)
    live = area2 > 1e-12
    length = np.linalg.norm(nrm, axis=1)
    bad_len = ~np.isfinite(length) | (np.abs(length - 1) > 0.05)
    un = nrm / np.maximum(length, 1e-20)[:, None]
    # one sample per triangle corner: stored normal vs that triangle's normal
    dots = np.einsum("ij,ij->i", un[tri].reshape(-1, 3), np.repeat(fn, 3, axis=0))
    dots = dots[np.repeat(live, 3)]
    cos_skew = np.cos(np.radians(skew_deg))
    res.update({
        "degenerate_normals": int(bad_len.sum()),
        "flipped_corners": int((dots < 0).sum()),
        "skewed_corners": int(((dots >= 0) & (dots < cos_skew)).sum()),
        "corners": int(dots.size),
        "worst_deg": float(np.degrees(np.arccos(np.clip(dots.min(), -1, 1)))) if dots.size else 0.0,
    })
    return res


def smooth_normals(pos, tri, smooth_deg):
    """Angle-weighted normals, split where faces differ by more than smooth_deg.
    Returns per-corner normals (len(tri)*3, 3)."""
    fn, area2 = face_normals(pos, tri)
    # weld by position so seams in the Ogre buffer don't break smoothing
    key = np.round(pos / 1e-4).astype(np.int64)
    _, weld = np.unique(key, axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    corners = weld[tri]  # (F,3)
    p = pos[tri]
    ang = np.empty((len(tri), 3))
    for k in range(3):
        e1 = p[:, (k + 1) % 3] - p[:, k]
        e2 = p[:, (k + 2) % 3] - p[:, k]
        cosv = np.einsum("ij,ij->i", e1, e2) / np.maximum(
            np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1), 1e-20)
        ang[:, k] = np.arccos(np.clip(cosv, -1, 1))
    cos_lim = np.cos(np.radians(smooth_deg))
    out = np.zeros((len(tri), 3, 3))
    # group corners by welded vertex, then accumulate only compatible faces
    flat_v = corners.reshape(-1)
    flat_f = np.repeat(np.arange(len(tri)), 3)
    flat_k = np.tile(np.arange(3), len(tri))
    order = np.argsort(flat_v, kind="stable")
    bounds = np.flatnonzero(np.diff(flat_v[order])) + 1
    for group in np.split(order, bounds):
        f, k = flat_f[group], flat_k[group]
        n = fn[f]
        w = ang[f, k][:, None]
        compat = (n @ n.T) >= cos_lim  # (g,g)
        acc = compat @ (n * w)
        acc /= np.maximum(np.linalg.norm(acc, axis=1), 1e-20)[:, None]
        out[f, k] = acc
    return out.reshape(-1, 3)


def reversed_faces(part) -> np.ndarray:
    """Triangles whose winding disagrees with all three stored normals."""
    if part["nrm"] is None or not len(part["tri"]):
        return np.zeros(len(part["tri"]), bool)
    fn, area2 = face_normals(part["pos"], part["tri"])
    dots = np.einsum("fkj,fj->fk", part["nrm"][part["tri"]], fn)
    return (dots < 0).all(axis=1) & (area2 > 1e-12)


def write_obj(path: Path, parts, fix: bool, smooth_deg: float, fix_winding: bool = True):
    lines = [f"# {path.stem} from BZR Ogre mesh\n"]
    vo = vt = vn = 0
    for part in parts:
        pos, uv, tri = part["pos"], part["uv"], part["tri"]
        if fix_winding and not fix:
            # Painter raytraces against the winding; make it match the normals
            tri = tri.copy()
            flip = reversed_faces(part)
            tri[flip] = tri[flip][:, ::-1]
        lines.append(f"o {part['name']}\nusemtl {part['material']}\n")
        lines += [f"v {x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in pos]
        if uv is not None:
            # Ogre UV origin is top-left, OBJ bottom-left
            lines += [f"vt {u:.6f} {1.0 - v:.6f}\n" for u, v in uv]
        if fix or part["nrm"] is None:
            cn = smooth_normals(pos, tri, smooth_deg)
            lines += [f"vn {x:.5f} {y:.5f} {z:.5f}\n" for x, y, z in cn]
            nidx = np.arange(len(cn)).reshape(-1, 3) + vn + 1
            vn += len(cn)
        else:
            lines += [f"vn {x:.5f} {y:.5f} {z:.5f}\n" for x, y, z in part["nrm"]]
            nidx = tri + vn + 1
            vn += len(pos)
        for f, (a, b, c) in enumerate(tri + 1):
            na, nb, nc = nidx[f]
            if uv is not None:
                lines.append(f"f {a+vo}/{a+vt}/{na} {b+vo}/{b+vt}/{nb} {c+vo}/{c+vt}/{nc}\n")
            else:
                lines.append(f"f {a+vo}//{na} {b+vo}//{nb} {c+vo}//{nc}\n")
        vo += len(pos)
        if uv is not None:
            vt += len(uv)
    path.write_text("".join(lines))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", type=Path, required=True, help="BZ_ASSETS root (or an addon folder)")
    ap.add_argument("--extra", type=Path, action="append", default=[],
                    help="extra roots searched for materials/textures (e.g. BZ_ASSETS_CORE)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", help="mesh stems to process (default all)")
    ap.add_argument("--skew-deg", type=float, default=75.0)
    ap.add_argument("--fail-ratio", type=float, default=0.02,
                    help="fraction of bad corners that marks a mesh as failing")
    ap.add_argument("--fix-normals", nargs="*", default=None, metavar="MESH",
                    help="recompute smooth normals for these meshes ('auto' = every failing mesh)")
    ap.add_argument("--keep-winding", action="store_true",
                    help="don't reverse triangles whose winding opposes their stored normals")
    ap.add_argument("--smooth-deg", type=float, default=45.0)
    args = ap.parse_args()

    roots = [args.assets, *args.extra]
    materials = parse_materials(roots)
    textures = index_textures(roots)
    obj_dir, nrm_dir = args.out / "obj", args.out / "normal"
    obj_dir.mkdir(parents=True, exist_ok=True)
    nrm_dir.mkdir(parents=True, exist_ok=True)

    meshes = sorted(args.assets.rglob("*.mesh"))
    if args.only:
        wanted = {s.lower() for s in args.only}
        meshes = [m for m in meshes if m.stem.lower() in wanted]

    manifest, audit_rows = [], []
    for mesh in meshes:
        try:
            parts = read_mesh(mesh)
        except Exception as exc:
            print(f"! {mesh.name}: {exc}")
            audit_rows.append({"mesh": mesh.stem, "error": str(exc)})
            continue
        if not parts:
            continue
        reports = [audit_part(p, args.skew_deg) for p in parts]
        corners = sum(r.get("corners", 0) for r in reports)
        bad = sum(r.get("flipped_corners", 0) + r.get("skewed_corners", 0) for r in reports)
        degen = sum(r.get("degenerate_normals", 0) for r in reports)
        failing = degen > 0 or (corners and bad / corners > args.fail_ratio)
        for part, r in zip(parts, reports):
            audit_rows.append({"mesh": mesh.stem, "submesh": part["name"],
                               "material": part["material"], **r})
        fix_list = {s.lower() for s in (args.fix_normals or [])}
        fix = mesh.stem.lower() in fix_list or ("auto" in fix_list and failing)
        rev = int(sum(reversed_faces(p).sum() for p in parts))
        write_obj(obj_dir / f"{mesh.stem}.obj", parts, fix, args.smooth_deg,
                  fix_winding=not args.keep_winding)

        mats = {}
        for part in parts:
            aliases = materials.get(part["material"], {})
            alias = aliases.get("NormalMap")
            diffuse = textures.get(aliases.get("DiffuseMap", "").lower())
            png = None
            if alias and not alias.lower().startswith("flat_n"):
                src = textures.get(alias.lower())
                if src is not None:
                    dst = nrm_dir / (Path(alias).stem + ".png")
                    png = str(dst) if dds_to_png(src, dst) else None
            mats[part["material"]] = {
                "normal": png, "normal_alias": alias,
                "diffuse_alias": aliases.get("DiffuseMap"),
                "size": dds_size(diffuse) if diffuse is not None else None}
        manifest.append({"mesh": mesh.stem, "source": str(mesh),
                         "obj": str(obj_dir / f"{mesh.stem}.obj"),
                         "materials": mats, "normals_failing": bool(failing),
                         "normals_fixed": bool(fix), "reversed_faces": rev})
        flag = " NORMALS FAIL" if failing else ""
        flag += " (normals recomputed)" if fix else ""
        flag += f" {rev} reversed tris" if rev else ""
        print(f"{mesh.stem}: {len(parts)} submesh, {bad}/{corners} bad corners{flag}")

    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (args.out / "normals_audit.json").write_text(json.dumps(audit_rows, indent=1))
    keys = sorted({k for r in audit_rows for k in r})
    with (args.out / "normals_audit.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(audit_rows)
    print(f"\n{len(manifest)} meshes -> {args.out}; "
          f"{sum(m['normals_failing'] for m in manifest)} fail the normal audit")


if __name__ == "__main__":
    main()
