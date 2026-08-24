# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""
Runtime test-kit generator (local validation helper; bpy-free).

Takes any stock vehicle .vdf and produces two MODIFIED copies for manual
in-game verification of the advanced semantics implemented by this toolkit:

  <name>_vlocsmoke.vdf
      Injects one VLOC chunk (generic kind, payload class 76 = SMOKE_EMITTER)
      parented at an obvious offset from the hull. CONFIRMED mechanics under
      test: the vehicle loader creates the invisible emitter node before
      Craft::FindSmokeSource runs, so a damaged vehicle should emit a second,
      distant smoke column in addition to its normal one.

  <name>_dmgload.vdf
      Fills VGEO damage-state bands 1-3 for every primary part with the same
      geometry names (visual no-op). Under test: the loader caches all 28
      bands without crashing once a driver eventually selects states - i.e.
      band plumbing round-trips through real engine load code.

Usage:
    python scripts/make_runtime_test_kit.py <source.vdf> <output_dir>

Generated files stay OUT of the repository; copy them next to their source
.geo dependencies inside a local addon/campaign folder to test.
"""

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_TESTS = os.path.join(_REPO_ROOT, "tests")
for _path in (_REPO_ROOT, _HERE, _TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools import semantics, vdf_classes, vdf_file  # noqa: E402


def make_vloc_smoke(parsed):
    chunk = semantics.VLOCChunk()
    chunk.kind_value = 76  # payload dword doubles as injected class id
    chunk.class_id = 76
    # Identity basis, posit offset front(+y)/up(+z) of the hull in engine axes.
    chunk.matrix = [
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
        0.0, 0.0, 1.0,
        0.0, 6.0, 4.0,
    ]
    chunk.opaque_payload = b""
    chunk.preserve_raw = False
    parsed.vlocs.append(chunk)
    while sum(1 for kind, _ in parsed.plan if kind == "vloc") < len(parsed.vlocs):
        parsed.plan.append(
            ("vloc", sum(1 for kind, _ in parsed.plan if kind == "vloc"))
        )


def make_damage_load(parsed):
    table = semantics.DamageVariantTable()
    table.capture_band_records(parsed.records, parsed.geocount)
    for slot in range(parsed.geocount):
        raw = table.base_records.get(slot)
        if raw is None:
            continue
        name = raw[:8].split(b"\0")[0].decode("ascii", "ignore").strip().lower()
        if not name or name.startswith("null"):
            continue
        for state in semantics.AUTHORED_DAMAGE_STATES:
            table.set_variant_name(slot, state, name)
    parsed.records = [
        table.build_band_record(slot, band)
        for band in range(semantics.VGEO_BAND_COUNT)
        for slot in range(parsed.geocount)
    ]


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, outdir = argv[1], argv[2]
    os.makedirs(outdir, exist_ok=True)
    data = open(src, "rb").read()

    stem = os.path.splitext(os.path.basename(src))[0]

    # --- VLOC smoke variant -------------------------------------------
    parsed = vdf_file.parse_vdf(data)
    make_vloc_smoke(parsed)
    out1 = os.path.join(outdir, f"{stem}_vlocsmoke.vdf")
    open(out1, "wb").write(vdf_file.serialize_vdf(parsed))
    print(f"wrote {out1}")

    # --- damage-load variant ------------------------------------------
    parsed = vdf_file.parse_vdf(data)
    make_damage_load(parsed)
    out2 = os.path.join(outdir, f"{stem}_dmgload.vdf")
    open(out2, "wb").write(vdf_file.serialize_vdf(parsed))
    print(f"wrote {out2}")

    print(
        "\nIn-game checklist (see docs/RUNTIME_TEST_KIT.md):\n"
        f"  1. Copy {stem}*.vdf next to the original {stem}.vdf's .geo files\n"
        "     inside your local addon/campaign folder.\n"
        "  2. Load the campaign, spawn/use this vehicle.\n"
        f"  3. {stem}_dmgload: vehicle loads and behaves normally "
        "(band plumbing regression check).\n"
        f"  4. {stem}_vlocsmoke: damage the vehicle; expect a second smoke\n"
        "     column about 6m ahead / 4m above the hull (injected class-76 node).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
