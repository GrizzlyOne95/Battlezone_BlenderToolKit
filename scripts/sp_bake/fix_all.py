"""Run fix_mesh_normals.split_fix over every stock model.

Where an override folder (e.g. Campaign Reimagined's Assets/ModelFixes) has a
mesh of the same name, that version is fixed instead of the stock one.

  python fix_all.py --out D:/meshfix [--override CR/Assets/ModelFixes] [--only avapc abbarr]

Writes <out>/orig/<name>.mesh (the source used), <out>/fixed/<name>.mesh and
<out>/fix_report.json. Meshes the fixer rejects (edge lists, 16-bit index
overflow, parse errors) are listed with the reason and left out of fixed/.
"""

import argparse
import json
import re
import shutil
from pathlib import Path

from fix_mesh_normals import split_fix

STOCK = Path(r"C:\Program Files (x86)\GOG Galaxy\Games\Battlezone 98 Redux\BZ_ASSETS\common\models")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stock", type=Path, default=STOCK)
    ap.add_argument("--override", type=Path, action="append", default=[],
                    help="folder whose meshes replace stock ones of the same name")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--split", type=float, default=60.0)
    ap.add_argument("--min-area", type=float, default=1e-4)
    ap.add_argument("--skip-material", default="glass|headlight")
    a = ap.parse_args()

    sources = {m.stem.lower(): m for m in sorted(a.stock.rglob("*.mesh"))}
    for folder in a.override:
        for m in folder.glob("*.mesh"):
            if m.stem.lower() in sources:
                sources[m.stem.lower()] = m
    if a.only:
        wanted = {s.lower() for s in a.only}
        sources = {k: v for k, v in sources.items() if k in wanted}

    skip = re.compile(a.skip_material, re.I) if a.skip_material else None
    orig, fixed = a.out / "orig", a.out / "fixed"
    orig.mkdir(parents=True, exist_ok=True)
    fixed.mkdir(parents=True, exist_ok=True)
    rows = []
    for stem, src in sorted(sources.items()):
        row = {"mesh": src.stem, "source": str(src),
               "override": not src.is_relative_to(a.stock)}
        shutil.copyfile(src, orig / src.name)
        try:
            out, rep = split_fix(src.read_bytes(), a.split, skip, min_area_frac=a.min_area)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            print(f"! {src.stem}: {row['error']}")
            rows.append(row)
            continue
        (fixed / src.name).write_bytes(out)
        row.update(bytes_in=src.stat().st_size, bytes_out=len(out), report=rep)
        print(f"{src.stem}: {src.stat().st_size} -> {len(out)} bytes")
        rows.append(row)

    (a.out / "fix_report.json").write_text(json.dumps(rows, indent=1))
    bad = [r for r in rows if "error" in r]
    print(f"\n{len(rows) - len(bad)} fixed, {len(bad)} rejected -> {a.out}")


if __name__ == "__main__":
    main()
