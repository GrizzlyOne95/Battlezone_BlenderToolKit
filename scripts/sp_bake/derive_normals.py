"""Derive _N normal maps for BZR materials that ship with flat_N.dds.

  python derive_normals.py --bake D:/bzr_bake --assets "<BZR>/BZ_ASSETS" [--extra <addon>] [--only abbarr]

Reads <bake>/manifest.json from ogre_to_obj.py and, for every material whose
normal map is flat_N (or missing), builds a tangent-space normal map from two
sources:

1. Rounded hard edges: bevel_bake.py bakes the mesh against itself with
   Cycles' Bevel shader (radius = --bevel x bounding-box diagonal), so every
   crease picks up a small highlight bevel the low-poly mesh can't have.
2. Surface detail from the diffuse: with --relief recess (default) only texels
   darker than their surroundings sink, so painted notches, seams and holes
   become shallow recesses while bright decals (stars, stripes) stay flat.
   This is a heuristic; dark grime sinks too.  --detail sets the strength.

The two are merged with reoriented normal mapping and written in the BZBase
convention, (-dh/du, -dh/drow, 1) (green = DirectX), as
<out>/<diffuse stem>_N.dds (DXT1 + mips, like stock) plus a PNG preview.

Overlapping UVs are a limit: stacked islands share one texel, so the bevel
bake keeps whichever island Cycles writes last.  Per-material overlap is
reported in derive_report.json.  A diffuse shared by several meshes is baked
on each; if their bevels disagree on more than --shared-tol of edge texels
(different models on one kit atlas) the bevel is dropped and only the paint
relief is kept, otherwise the strongest edge per texel wins.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from ogre_to_obj import index_textures

HERE = Path(__file__).resolve().parent
BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")


def obj_extent(obj: Path) -> float:
    pts = np.array([list(map(float, ln.split()[1:4])) for ln in obj.read_text().splitlines()
                    if ln.startswith("v ")])
    return float(np.linalg.norm(pts.max(0) - pts.min(0)))


def uv_overlap(obj: Path, material: str, res: int = 256) -> float:
    """Fraction of a material's covered UV texels hit by more than one triangle."""
    vt, faces, cur = [], [], None
    for ln in obj.read_text().splitlines():
        if ln.startswith("vt "):
            vt.append(tuple(map(float, ln.split()[1:3])))
        elif ln.startswith("usemtl "):
            cur = ln[7:].strip()
        elif ln.startswith("f ") and cur == material:
            faces.append([int(c.split("/")[1]) - 1 for c in ln.split()[1:]])
    hits = np.zeros((res, res), np.int32)
    vt = np.asarray(vt)
    for f in faces:
        for k in range(1, len(f) - 1):
            tri = vt[[f[0], f[k], f[k + 1]]] * res
            lo = np.floor(tri.min(0)).astype(int).clip(0, res - 1)
            hi = np.ceil(tri.max(0)).astype(int).clip(0, res - 1)
            xs, ys = np.meshgrid(np.arange(lo[0], hi[0] + 1) + 0.5, np.arange(lo[1], hi[1] + 1) + 0.5)
            p = np.stack([xs.ravel(), ys.ravel()], 1)
            a, b, c = tri
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-12:
                continue
            l1 = ((b[1] - c[1]) * (p[:, 0] - c[0]) + (c[0] - b[0]) * (p[:, 1] - c[1])) / d
            l2 = ((c[1] - a[1]) * (p[:, 0] - c[0]) + (a[0] - c[0]) * (p[:, 1] - c[1])) / d
            inside = (l1 >= 0) & (l2 >= 0) & (l1 + l2 <= 1)
            q = p[inside].astype(int)
            hits[q[:, 1], q[:, 0]] += 1
    covered = (hits > 0).sum()
    return float((hits > 1).sum() / covered) if covered else 0.0


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur with wrap-around edges (UV atlases tile)."""
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    for axis in (0, 1):
        a = sum(w * np.roll(a, i - r, axis) for i, w in enumerate(k))
    return a


def detail_normal(diffuse: Image.Image, size, strength: float, sigmas, mode: str = "recess") -> np.ndarray:
    """Diffuse-derived height -> BZBase normal (-dh/du, -dh/drow, 1).

    "recess": only texels darker than their surroundings sink (painted gaps,
    notches, seams, holes); brighter paint such as stars and stripes stays
    flat, since on BZR atlases that is almost always a decal.
    "luminance": plain high-pass, bright = raised (embosses decals too).
    """
    lum = diffuse.convert("L").resize(size, Image.LANCZOS)
    L = np.asarray(lum, np.float32) / 255.0
    h = np.zeros_like(L)
    for sigma in sigmas:
        r = sigma * size[0] / 1024
        if mode == "recess":
            # local level ignoring brighter paint, so a decal casts no dip around itself
            base = blur(L, r)
            for _ in range(4):  # converge on the lower envelope so bright paint can't lift it
                base = blur(np.minimum(L, base), r)
            h += np.minimum(L - base, 0)
        else:
            h += L - blur(L, r)
    if mode == "recess":
        # solid bright blobs (decals, not thin rim highlights) and their painted outline stay flat
        s = size[0] / 1024
        blob = blur((L - blur(L, 16 * s) > 0.12).astype(np.float32), 3 * s) > 0.5
        h *= 1 - np.clip(blur(blob.astype(np.float32), 3 * s) * 3, 0, 1)
    h = blur(h, 0.7)  # soften single-texel noise so DXT1 doesn't turn it into blocks
    dhdrow, dhdu = np.gradient(h)
    k = strength * size[0] / 1024
    n = np.dstack([-k * dhdu, -k * dhdrow, np.ones_like(h)])
    return n / np.linalg.norm(n, axis=2, keepdims=True)


def rnm(base: np.ndarray, detail: np.ndarray) -> np.ndarray:
    """Reoriented normal mapping: detail laid over base, both unit tangent normals."""
    t = base + np.array([0, 0, 1], np.float32)
    u = detail * np.array([-1, -1, 1], np.float32)
    r = t * np.sum(t * u, axis=2, keepdims=True) / t[..., 2:3] - u
    return r / np.linalg.norm(r, axis=2, keepdims=True)


def write_dds_dxt1(rgb: np.ndarray, path: Path):
    """DXT1 with a full mip chain (Pillow encodes one level per save)."""
    img = Image.fromarray(rgb, "RGB")
    levels = []
    while True:
        buf = io.BytesIO()
        img.save(buf, "DDS", pixel_format="DXT1")
        levels.append(buf.getvalue())
        if img.width == 1 and img.height == 1:
            break
        img = img.resize((max(1, img.width // 2), max(1, img.height // 2)), Image.BOX)
    head = bytearray(levels[0][:128])
    head[8:12] = (int.from_bytes(head[8:12], "little") | 0x20000).to_bytes(4, "little")  # MIPMAPCOUNT
    head[28:32] = len(levels).to_bytes(4, "little")
    head[108:112] = (0x1000 | 0x8 | 0x400000).to_bytes(4, "little")  # TEXTURE|COMPLEX|MIPMAP
    path.write_bytes(bytes(head) + b"".join(lv[128:] for lv in levels))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bake", type=Path, required=True, help="ogre_to_obj.py / run_bake.ps1 output folder")
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--extra", type=Path, action="append", default=[])
    ap.add_argument("--out", type=Path, help="default <bake>/derived_normals")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--bevel", type=float, default=0.004, help="bevel radius, fraction of bbox diagonal")
    ap.add_argument("--detail", type=float, default=6.0, help="diffuse relief strength (0 = off)")
    ap.add_argument("--sigma", type=float, nargs="+", default=[4.0, 16.0],
                    help="high-pass radii in px at 1024 (several = multi-scale)")
    ap.add_argument("--relief", choices=["recess", "luminance"], default="recess",
                    help="recess: only dark gaps sink, decals stay flat")
    ap.add_argument("--max-size", type=int, default=1024)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--shared-tol", type=float, default=0.2,
                    help="drop the bevel of a shared diffuse when this fraction of edge texels disagree")
    ap.add_argument("--rebake", action="store_true", help="redo bevel bakes already in <out>/work")
    ap.add_argument("--blender", type=Path, default=BLENDER)
    a = ap.parse_args()
    out = a.out or a.bake / "derived_normals"
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((a.bake / "manifest.json").read_text())
    textures = index_textures([a.assets, *a.extra][::-1])  # later --extra wins
    wanted = {s.lower() for s in a.only} if a.only else None
    report = []
    # pass 1: bevel-bake every (mesh, material) that uses a flat_N material
    stems: dict[str, dict] = {}
    for entry in manifest:
        mesh = entry["mesh"]
        if wanted and mesh.lower() not in wanted:
            continue
        obj = Path(entry["obj"])
        targets = {}
        for mat, info in entry["materials"].items():
            nalias = (info.get("normal_alias") or "").lower()
            dalias = info.get("diffuse_alias")
            if nalias not in ("", "flat_n.dds") or not dalias:
                continue
            stem = re.sub(r"_d$", "", Path(dalias).stem, flags=re.I)
            src = textures.get(dalias.lower())
            if src is None:
                report.append({"mesh": mesh, "material": mat, "error": f"diffuse {dalias} not found"})
                continue
            if stem.lower() not in stems:
                w, h = info.get("size") or Image.open(src).size
                scale = min(1.0, a.max_size / max(w, h))
                stems[stem.lower()] = {"stem": stem, "diffuse": str(src), "users": [],
                                       "size": [max(4, int(w * scale)), max(4, int(h * scale))]}
            s = stems[stem.lower()]
            npy = str(work / f"{mesh}__{re.sub(r'[^A-Za-z0-9_]', '_', mat)}.npy")
            targets[mat] = {"npy": npy, "size": s["size"]}
            s["users"].append({"mesh": mesh, "material": mat, "obj": str(obj), "npy": npy})
        if not targets:
            continue
        radius = a.bevel * obj_extent(obj)
        for t in targets.values():
            t["radius"] = radius
        job = work / f"{mesh}.json"
        job.write_text(json.dumps({"obj": str(obj), "radius": radius, "samples": a.samples,
                                   "targets": targets}))
        if a.rebake or not all(Path(t["npy"]).exists() for t in targets.values()):
            r = subprocess.run([str(a.blender), "-b", "--factory-startup", "-P", str(HERE / "bevel_bake.py"),
                                "--", str(job)], capture_output=True, text=True)
            if not all(Path(t["npy"]).exists() for t in targets.values()):
                print(f"! {mesh}: bevel bake failed: {r.stderr[-300:]}")

    # pass 2: one map per diffuse.  A diffuse shared by several meshes keeps its
    # bevel only where their bakes agree; otherwise one mesh's edges would be
    # drawn across another's surface, so the bevel is dropped (detail only).
    for s in stems.values():
        baked = [u for u in s["users"] if Path(u["npy"]).exists()]
        row = {"texture": s["stem"] + "_N.dds", "size": s["size"],
               "users": [f"{u['mesh']}/{u['material']}" for u in s["users"]]}
        if not baked:
            row["error"] = "bevel bake failed"
            report.append(row)
            continue
        bases, cover = [], []
        for u in baked:
            raw = np.load(u["npy"]).astype(np.float32)
            cover.append(raw.sum(axis=2) > 0.01)  # texels this bake wrote (islands + margin)
            b = raw * 2 - 1
            b[..., 1] *= -1  # Blender +V up -> BZBase -dh/drow
            b = b / np.maximum(np.linalg.norm(b, axis=2, keepdims=True), 1e-6)
            b[~cover[-1]] = (0, 0, 1)
            bases.append(b)
        dev = [np.linalg.norm(b[..., :2], axis=2) for b in bases]
        # compare only texels two bakes both cover, where either has an edge
        hits = tot = 0
        for i in range(len(bases)):
            for j in range(i + 1, len(bases)):
                m = cover[i] & cover[j] & ((dev[i] > 0.1) | (dev[j] > 0.1))
                tot += m.sum()
                hits += (np.linalg.norm(bases[i] - bases[j], axis=2)[m] > 0.15).sum()
        disagree = float(hits / tot) if tot else 0.0
        row["bevel_disagree"] = round(disagree, 3)
        if disagree > a.shared_tol:
            base = np.dstack([np.zeros(s["size"][::-1]), np.zeros(s["size"][::-1]), np.ones(s["size"][::-1])])
            row["bevel"] = "dropped"
        else:  # per texel, keep the strongest edge any user baked
            pick = np.argmax(dev, axis=0)
            base = np.take_along_axis(np.stack(bases), pick[None, ..., None], 0)[0]
            row["bevel"] = "kept"
        n = base.astype(np.float32)
        if a.detail > 0:
            n = rnm(n, detail_normal(Image.open(s["diffuse"]), tuple(s["size"]), a.detail, a.sigma, a.relief))
        rgb = np.clip(n * 127.5 + 127.5, 0, 255).astype(np.uint8)
        write_dds_dxt1(rgb, out / row["texture"])
        Image.fromarray(rgb).save(out / (s["stem"] + "_N.png"))
        row["uv_overlap"] = round(uv_overlap(Path(baked[0]["obj"]), baked[0]["material"]), 3)
        report.append(row)
        print(f"{row['texture']} {s['size']} users {len(s['users'])} bevel {row['bevel']} "
              f"(disagree {disagree:.0%}) overlap {row['uv_overlap']:.0%}")
    (out / "derive_report.json").write_text(json.dumps(report, indent=1))
    print(f"{sum('texture' in r and 'error' not in r for r in report)} maps -> {out}")


if __name__ == "__main__":
    main()
