"""Stitch compare_preview.py tiles into labelled before/after review pages.

  python contact_sheets.py <tile_dir> <out_dir> [--per-page 6] [--report fix_report.json]

Each mesh is a block: before row on top, after row below, name (and vertices
added / reversed tris fixed, from fix_all.py's report) in the header.
"""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tiles", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--per-page", type=int, default=6)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    notes = {}
    if a.report and a.report.exists():
        for r in json.loads(a.report.read_text()):
            rep = r.get("report", {})
            added = sum(g["added"] for g in rep.get("geometries", []))
            rev = sum(s.get("reversed", 0) for s in rep.get("submeshes", []))
            tag = f"+{added} verts"
            if rev:
                tag += f", {rev} tris flipped"
            if r.get("override"):
                tag += ", CR override"
            if rep.get("edge_lists_dropped"):
                tag += ", edge lists dropped"
            notes[r["mesh"].lower()] = tag

    stems = sorted({p.stem.rsplit("_", 1)[0] for p in a.tiles.glob("*_a0.png")})
    tile = Image.open(a.tiles / f"{stems[0]}_b0.png").size[0]
    head = 34
    font = ImageFont.truetype("arial.ttf", 22)
    small = ImageFont.truetype("arial.ttf", 16)
    bw, bh = tile * 3, head + tile * 2
    rows = -(-a.per_page // a.cols)
    pages = [stems[i:i + a.per_page] for i in range(0, len(stems), a.per_page)]
    for pi, page in enumerate(pages, 1):
        sheet = Image.new("RGB", (bw * a.cols + 8 * (a.cols - 1), bh * rows + 8 * (rows - 1)), (30, 30, 30))
        draw = ImageDraw.Draw(sheet)
        for k, stem in enumerate(page):
            x0, y0 = (k % a.cols) * (bw + 8), (k // a.cols) * (bh + 8)
            draw.text((x0 + 8, y0 + 5), stem, fill=(255, 220, 120), font=font)
            draw.text((x0 + 8 + draw.textlength(stem, font) + 14, y0 + 10),
                      notes.get(stem.lower(), ""), fill=(190, 190, 190), font=small)
            for r, tag in enumerate("ba"):
                for i in range(3):
                    p = a.tiles / f"{stem}_{tag}{i}.png"
                    if p.exists():
                        sheet.paste(Image.open(p).convert("RGB"), (x0 + i * tile, y0 + head + r * tile))
                draw.text((x0 + 6, y0 + head + r * tile + 4), "before" if tag == "b" else "after",
                          fill=(255, 80, 80) if tag == "b" else (80, 220, 80), font=small)
        path = a.out / f"normals_ab_{pi:02d}.png"
        sheet.save(path, optimize=True)
        print(path.name, ", ".join(page))


if __name__ == "__main__":
    main()
