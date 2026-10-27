"""Pack the Painter ACT bakes into one shippable <material>_ACT.dds per material.

  python act_pack.py --bake <run_bake.ps1 out> [--bake <another>] --out <dir>
                     [--materials <dir of .material files>] [--size 512]

ACT = R ambient occlusion, G curvature (127 = flat), B thickness, on UV0.
Painter writes one map per (mesh, material), so a material used by several
meshes has several bakes of the same texture space.  Per texel:

  * coverage comes from rasterising that mesh's UV triangles (Painter leaves
    the rest black, which would read as full occlusion);
  * UV islands whose mean AO is under --enclosed are geometry sealed inside
    the hull (cockpit interiors, buried headlights, LOD stand-ins).  Their
    bake is near black, which would kill all ambient light in the cockpit
    view, so they go neutral;
  * co-covered texels take the per-texel median over the users, so one
    mesh that buries a shared kit part cannot darken it everywhere;
  * uncovered texels are filled from the nearest covered one, so mips and
    bilinear filtering never pull black into island borders.

The result is box-filtered to --size and written as DXT1 with mips.  Shaders
read a neutral (255, 128, 255) default for materials without a map; the
script also writes that as cr_act_neutral.dds.  --materials limits the
output to materials defined there and lists the rest in the report.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from derive_normals import write_dds_dxt1

NEUTRAL = (255, 128, 255)


def uv_coverage(obj: Path, material: str, res: int) -> np.ndarray:
    """Texels (top-down rows, like the PNG) covered by a material's UV triangles."""
    vt, faces, cur = [], [], None
    for ln in obj.read_text().splitlines():
        if ln.startswith("vt "):
            vt.append(tuple(map(float, ln.split()[1:3])))
        elif ln.startswith("usemtl "):
            cur = ln[7:].strip()
        elif ln.startswith("f ") and cur == material:
            faces.append([int(c.split("/")[1]) - 1 for c in ln.split()[1:]])
    cov = np.zeros((res, res), bool)
    if not faces:
        return cov
    vt = np.asarray(vt) % 1.0
    vt[:, 1] = 1.0 - vt[:, 1]  # OBJ v runs up, image rows run down
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
            q = p[(l1 >= 0) & (l2 >= 0) & (l1 + l2 <= 1)].astype(int)
            cov[q[:, 1], q[:, 0]] = True
    # conservative: include texels the bake's own edge padding touches
    return ndimage.binary_dilation(cov, iterations=1)


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bake", type=Path, action="append", required=True,
                    help="run_bake.ps1 output folder (manifest.json + maps/); repeatable")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--materials", type=Path, help="only materials defined in these .material files")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--enclosed", type=float, default=0.2,
                    help="UV islands with mean AO below this are treated as sealed inside and go neutral")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()

    users: dict[str, list[tuple[Path, Path]]] = {}
    for bake in args.bake:
        for e in json.loads((bake / "manifest.json").read_text()):
            for mat in e["materials"]:
                png = bake / "maps" / e["mesh"] / f"{safe(mat)}_ACT.png"
                if png.exists():
                    users.setdefault(mat, []).append((Path(e["obj"]), png))
    defined = None
    if args.materials:
        defined = set()
        for mf in args.materials.glob("*.material"):
            defined |= set(re.findall(r"^\s*material\s+(\S+)", mf.read_text(errors="replace"), re.M))
    args.out.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), NEUTRAL).save(args.out / "neutral.png")
    write_dds_dxt1(np.full((4, 4, 3), NEUTRAL, np.uint8), args.out / "cr_act_neutral.dds")

    report = {"written": [], "not_in_materials": [], "no_coverage": []}
    for mat in sorted(users, key=str.lower):
        if args.only and mat not in args.only:
            continue
        if defined is not None and mat not in defined:
            report["not_in_materials"].append(mat)
            continue
        stack, shape, enclosed = [], None, 0
        for obj, png in users[mat]:
            px = np.asarray(Image.open(png).convert("RGB")).astype(np.float32) / 255.0
            if shape is None:
                shape = px.shape
            if px.shape != shape:
                continue  # mismatched bake size; first user's size wins
            cov = uv_coverage(obj, mat, shape[0]) if obj.exists() else np.ones(shape[:2], bool)
            lab, nlab = ndimage.label(cov)
            if nlab:
                means = np.asarray(ndimage.mean(px[..., 0], lab, np.arange(1, nlab + 1)))
                sealed = np.isin(lab, np.flatnonzero(means < args.enclosed) + 1)
                px[sealed] = np.array(NEUTRAL, np.float32) / 255.0
                enclosed += int((means < args.enclosed).sum())
            layer = np.where(cov[..., None], px, np.nan)
            stack.append(layer)
        covered = np.zeros(shape[:2], bool) if shape else None
        if stack:
            covered = ~np.isnan(np.stack(stack)[..., 0]).all(0)
        if covered is None or not covered.any():
            report["no_coverage"].append(mat)
            continue
        act = np.nanmedian(np.stack(stack), 0) if len(stack) > 1 else stack[0]
        # fill uncovered texels from the nearest covered one
        _, (iy, ix) = ndimage.distance_transform_edt(~covered, return_indices=True)
        act = act[iy, ix]
        img = Image.fromarray((act.clip(0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
        if img.width > args.size:
            img = img.resize((args.size, args.size * img.height // img.width), Image.BOX)
        img.save(args.out / f"{safe(mat)}_ACT.png")
        write_dds_dxt1(np.asarray(img), args.out / f"{safe(mat)}_ACT.dds")
        report["written"].append({"material": mat, "users": len(users[mat]),
                                  "coverage": round(float(covered.mean()), 3),
                                  "enclosed_islands": enclosed,
                                  "ao_mean": round(float(act[..., 0][covered].mean()), 3)})
        print(f"{mat}: {len(users[mat])} user(s), coverage {covered.mean():.2f}")
    (args.out / "act_report.json").write_text(json.dumps(report, indent=1))
    print(f"written {len(report['written'])}, not in materials {len(report['not_in_materials'])}, "
          f"no coverage {len(report['no_coverage'])}")


if __name__ == "__main__":
    main()
