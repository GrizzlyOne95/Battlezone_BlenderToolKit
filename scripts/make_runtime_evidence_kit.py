# Battlezone 98R Blender ToolKit
# Copyright (C) 2024-2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""
Runtime evidence-kit generator (local validation helper; bpy-free).

Builds the synthetic Phase A-F assets defined by docs/RUNTIME_SEMANTIC_
VERIFICATION.md together with the evidence chain that document mandates:

  authored state -> exported bytes -> Redux runtime observation

Outputs (all into --out, never committed):
  <case>.vdf                    one synthetic asset per experiment
  evidence_manifest.json        session template + per-asset SHA-256/bytes +
                                authored property summary for every case
  RUNTIME_SESSION_RECORD.md     observation sheet with INCONCLUSIVE defaults,
                                bracketing checklists, and the session
                                identity controls pre-listed

Filename discipline is enforced here: every directly referenced asset stem
is <=8 characters (strict BZ runtime limit) and the generator fails closed
instead of truncating.

Usage:
    python scripts/make_runtime_evidence_kit.py --out C:\temp\bz_evidence
"""

import argparse
import hashlib
import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_TESTS = os.path.join(_REPO_ROOT, "tests")
for _path in (_REPO_ROOT, _HERE, _TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _bootstrap  # noqa: E402

_bootstrap.ensure_package()

from bz98tools import semantics, vdf_classes  # noqa: E402

_TESTS_MOD = os.path.join(_TESTS)
if _TESTS_MOD not in sys.path:
    sys.path.insert(0, _TESTS_MOD)

import fixtures_builder as fb  # noqa: E402

IDENTITY = list(fb.IDENTITY)

MAX_STEM_LEN = 8  # strict BZ runtime filename limit for referenced assets


def _fail(msg):
    raise SystemExit("evidence-kit: %s" % msg)


def enforce_stem(filename):
    stem = filename.split(".")[0]
    if len(stem) > MAX_STEM_LEN:
        # Fail closed: silent truncation would silently change what the game
        # resolves and invalidate the authored->bytes->runtime chain.
        _fail("asset stem %r exceeds %d characters" % (stem, MAX_STEM_LEN))
    return filename


class Case:
    def __init__(self, case_id, phase, purpose, filename, build, authored):
        self.case_id = case_id
        self.phase = phase
        self.purpose = purpose
        self.filename = enforce_stem(filename)
        self.build = build
        self.authored = authored


# ---------------------------------------------------------------------------
# VLOC payload helper (kind dword + 12 floats, matching stock layout).
# ---------------------------------------------------------------------------


def vloc_payload(class_id, matrix):
    return struct.pack("<I", class_id) + struct.pack("<12f", *matrix)


def add_vloc(out_bytes, class_id, matrix):
    return fb.add_chunk(out_bytes, b"VLOC", vloc_payload(class_id, matrix))


# ---------------------------------------------------------------------------
# Matrix builders implementing the normative transform controls.
# ---------------------------------------------------------------------------


def rot_x(deg):
    import math

    a = math.radians(deg)
    return [
        1.0, 0.0, 0.0,
        0.0, math.cos(a), -math.sin(a),
        0.0, math.sin(a), math.cos(a),
    ]


def rot_z(deg):
    import math

    a = math.radians(deg)
    return [
        math.cos(a), -math.sin(a), 0.0,
        math.sin(a), math.cos(a), 0.0,
        0.0, 0.0, 1.0,
    ]


def composed(rotation9, translation):
    m = list(rotation9) + list(translation)
    return m


# ---------------------------------------------------------------------------
# Phase case tables.
# ---------------------------------------------------------------------------


def ordinary_base(name="eb01"):
    """Single ordinary part vehicle used as the carrier for VLOC cases."""
    records = fb.grid({0: fb.make_record(name)}, 1)
    return fb.build_vdf(records, 1, name)


def phase_a_cases():
    """VLOC class 38 headlight: presence/translation/rotation/scale."""
    cases = []
    t = lambda x, y, z: IDENTITY[:9] + [x, y, z]  # noqa: E731

    cases.append(Case(
        "A38-T01", "A", "presence: no class-38 record",
        "a38t01.vdf", lambda: ordinary_base(),
        {"vloc38": None}))
    cases.append(Case(
        "A38-T02", "A", "presence: identity transform at origin",
        "a38t02.vdf", lambda: add_vloc(ordinary_base(), 38, t(0, 0, 0)),
        {"vloc38": {"matrix": t(0, 0, 0)}}))
    # Control 9: signed / fractional / deliberately non-round translations.
    cases.append(Case(
        "A38-T03", "A", "signed+fractional translation (-2.75, 0.85, 1.137)",
        "a38t03.vdf",
        lambda: add_vloc(ordinary_base(), 38, t(-2.75, 0.85, 1.137)),
        {"vloc38": {"matrix": t(-2.75, 0.85, 1.137)}}))
    cases.append(Case(
        "A38-T04", "A", "large positive translation (4.5, 6.25, -3.5)",
        "a38t04.vdf",
        lambda: add_vloc(ordinary_base(), 38, t(4.5, 6.25, -3.5)),
        {"vloc38": {"matrix": t(4.5, 6.25, -3.5)}}))
    # Control 10: non-90-degree rotations per axis.
    cases.append(Case(
        "A38-R01", "A", "rotation Z +37 degrees (non-orthogonal)",
        "a38r01.vdf",
        lambda: add_vloc(ordinary_base(), 38, composed(rot_z(37), [0, 0, 0])),
        {"vloc38": {"rotation_deg_z": 37}}))
    cases.append(Case(
        "A38-R02", "A", "rotation X +23 degrees (non-orthogonal)",
        "a38r02.vdf",
        lambda: add_vloc(ordinary_base(), 38, composed(rot_x(23), [0, 0, 0])),
        {"vloc38": {"rotation_deg_x": 23}}))
    # Control 11: combined translation + rotation.
    cases.append(Case(
        "A38-C01", "A", "combined rotation Z 41deg + translation (1.5, 2.25, 0.75)",
        "a38c01.vdf",
        lambda: add_vloc(
            ordinary_base(), 38, composed(rot_z(41), [1.5, 2.25, 0.75])),
        {"vloc38": {"rotation_deg_z": 41, "translation": [1.5, 2.25, 0.75]}}))
    # Scale probe: uniform scale in basis columns.
    scaled = [2.0, 0, 0, 0, 2.0, 0, 0, 0, 2.0, 0.0, 1.1, 0.35]
    cases.append(Case(
        "A38-S01", "A", "uniform scale 2x in basis columns",
        "a38s01.vdf", lambda: add_vloc(ordinary_base(), 38, scaled),
        {"vloc38": {"basis_scale": 2.0}}))
    return cases


def phase_b_cases():
    """VLOC class 40 eyepoint/POV: offsets incl. parented variants."""
    cases = []

    def pov_vehicle(pov_matrix, parent_chain_identity=True, two_parts=False):
        if two_parts:
            records = fb.grid(
                {
                    0: fb.make_record("eb11"),
                    1: fb.make_record("ebp11", "eb11", type_=40,
                                      matrix=list(IDENTITY)),
                },
                2)
            out = fb.build_vdf(records, 2, "ebpov")
        else:
            out = ordinary_base()
        return add_vloc(out, 40, pov_matrix)

    t = lambda x, y, z: IDENTITY[:9] + [x, y, z]  # noqa: E731
    cases.append(Case(
        "B40-T01", "B", "POV baseline identity at origin",
        "b40t01.vdf", lambda: pov_vehicle(t(0, 0, 0)),
        {"vloc40": {"matrix": t(0, 0, 0)}}))
    # Three known authored offsets for the x25 relationship measurement.
    cases.append(Case(
        "B40-T02", "B", "POV offset (0, 0.7, 0.4)",
        "b40t02.vdf", lambda: pov_vehicle(t(0, 0.7, 0.4)),
        {"vloc40": {"matrix": t(0, 0.7, 0.4)}}))
    cases.append(Case(
        "B40-T03", "B", "POV offset (0, 1.4, 0.8) = 2x T02",
        "b40t03.vdf", lambda: pov_vehicle(t(0, 1.4, 0.8)),
        {"vloc40": {"matrix": t(0, 1.4, 0.8)}}))
    cases.append(Case(
        "B40-T04", "B", "POV signed fractional offset (-1.75, 2.1, -0.65)",
        "b40t04.vdf", lambda: pov_vehicle(t(-1.75, 2.1, -0.65)),
        {"vloc40": {"matrix": t(-1.75, 2.1, -0.65)}}))
    # Control 10+11: non-90-degree rotation and combination.
    cases.append(Case(
        "B40-R01", "B", "POV rotation Z 130 degrees (non-orthogonal)",
        "b40r01.vdf",
        lambda: pov_vehicle(composed(rot_z(130), [0, 0, 0])),
        {"vloc40": {"rotation_deg_z": 130}}))
    cases.append(Case(
        "B40-C01", "B", "POV rotation X -33deg + translation (0.5, 1.125, 0.25)",
        "b40c01.vdf",
        lambda: pov_vehicle(composed(rot_x(-33), [0.5, 1.125, 0.25])),
        {"vloc40": {"rotation_deg_x": -33,
                    "translation": [0.5, 1.125, 0.25]}}))
    # Control 12: non-identity PARENT transform (helper part under rotated
    # root) with identity VLOC matrix - isolates parent composition.
    tilted_parent = composed(rot_z(30), [0.0, 0.0, 0.5])
    records = fb.grid(
        {
            0: fb.make_record("ebr11", matrix=tilted_parent),
            1: fb.make_record("ebp21", "ebr11", type_=40),
        },
        2)
    cases.append(Case(
        "B40-P01", "B", "parented POV under rotated parent, VLOC identity",
        "b40p01.vdf", lambda: fb.build_vdf(records, 2, "ebp21"),
        {"parent_matrix": tilted_parent, "vloc40": "part-class record"}))
    return cases


def phase_c_cases():
    """VLOC class 42 opaque probes."""
    cases = []
    t = lambda x, y, z: IDENTITY[:9] + [x, y, z]  # noqa: E731
    cases.append(Case(
        "C42-T01", "C", "absence baseline (no class-42 record)",
        "c42t01.vdf", lambda: ordinary_base(),
        {"vloc42": None}))
    cases.append(Case(
        "C42-T02", "C", "class-42 present, identity transform",
        "c42t02.vdf", lambda: add_vloc(ordinary_base(), 42, t(0, 0, 0)),
        {"vloc42": {"matrix": t(0, 0, 0)}}))
    cases.append(Case(
        "C42-T03", "C", "class-42 extreme valid translation (60, -45, 90)",
        "c42t03.vdf", lambda: add_vloc(ordinary_base(), 42, t(60, -45, 90)),
        {"vloc42": {"matrix": t(60, -45, 90)}}))
    return cases


def phase_d_cases():
    """Damage bands: unmistakable per-state geometry names.

    Threshold bracketing itself is a runtime-session control (health sweeps
    at fixed distance, distance sweeps at fixed health); the assets provide
    unambiguous state identities plus LOD-distance A/B pairs.
    """
    cases = []

    def dmg(state_names, lod_dists=(50.0, 120.0, 250.0, 400.0, 600.0)):
        base = {0: fb.make_record(state_names[0], radius=2.0)}
        variants = {}
        for state in (1, 2, 3):
            variants[(0, state)] = fb.make_record(state_names[state],
                                                  radius=2.0)
        records = fb.grid(base, 1, variants)
        out_bytes = bytearray()
        out_bytes += vdf_classes.serialize_section(vdf_classes.VDFHeader())
        vdfc = vdf_classes.VDFCHeader()
        vdfc.name = state_names[0][:5]
        vdfc.vehicletype = 1
        vdfc.vehiclesize = 2
        (vdfc.lod1dist, vdfc.lod2dist, vdfc.lod3dist, vdfc.lod4dist,
         vdfc.lod5dist) = lod_dists
        vdfc.mass = 4.0
        vdfc.multiplyer = 1.0
        vdfc.drag = 0.9
        out_bytes += vdf_classes.serialize_section(vdfc)
        out_bytes += vdf_classes.serialize_section(vdf_classes.EXITSection())
        vg = vdf_classes.VGEOHeader()
        vg.sectionlength = len(records) * 100 + vg.binlength
        vg.geocount = 1
        out_bytes += vdf_classes.serialize_section(vg)
        out_bytes += b"".join(records)
        out_bytes += vdf_classes.serialize_section(vdf_classes.EXITSection())
        colp = vdf_classes.COLPSection()
        colp.data = [3.0, 2.5, -2.5, -3.0, 1.5, 1.2,
                     -1.2, -1.5, 1.0, 0.9, -0.9, -1.0]
        out_bytes += vdf_classes.serialize_section(colp)
        out_bytes += vdf_classes.serialize_section(vdf_classes.EXITSection())
        return bytes(out_bytes)

    cases.append(Case(
        "DDM-A01", "D",
        "distinct names per damage state (default LOD ladder)",
        "ddma01.vdf",
        lambda: dmg(("dv00", "dv01", "dv02", "dv03")),
        {"states": ["dv00", "dv01", "dv02", "dv03"],
         "lod_dists": [50.0, 120.0, 250.0, 400.0, 600.0]}))
    cases.append(Case(
        "DDM-B01", "D",
        "same asset, compressed LOD ladder for near-field bracketing",
        "ddmb01.vdf",
        lambda: dmg(("dw00", "dw01", "dw02", "dw03"),
                    lod_dists=(20.0, 35.0, 55.0, 80.0, 110.0)),
        {"states": ["dw00", "dw01", "dw02", "dw03"],
         "lod_dists": [20.0, 35.0, 55.0, 80.0, 110.0]}))
    cases.append(Case(
        "DDM-M01", "D",
        "missing intermediate bands: only states 0 and 3 populated",
        "ddmm01.vdf",
        lambda: dmg(("dx00", "dx00", "dx00", "dx03")),
        {"states_populated": ["dx00", "dx03"], "fallback_probe": True}))
    return cases


def phase_e_cases():
    """ObjectFlags one-bit-at-a-time XOR pairs, scoped per asset class."""
    cases = []

    def flag_pair(case_prefix, filename_a, filename_b, xor_bits,
                  asset_class="ordinary"):
        base_flags = int(semantics.encode_object_flags())
        flags_a = base_flags | xor_bits
        flags_b = base_flags
        if asset_class == "bridge":
            rec_a = fb.make_record("efr11", type_=8, flags=flags_a)
            rec_b = fb.make_record("efr11", type_=8, flags=flags_b)
        else:
            rec_a = fb.make_record("efe11", flags=flags_a)
            rec_b = fb.make_record("efe11", flags=flags_b)
        return [
            Case(case_prefix + "-A", "E", "flag pair A (bit set): 0x%08X"
                 % xor_bits, filename_a,
                 lambda r=rec_a: fb.build_vdf(fb.grid({0: r}, 1), 1,
                                              filename_a.split(".")[0]),
                 {"xor_bits": "0x%08X" % xor_bits, "variant": "set"}),
            Case(case_prefix + "-B", "E", "flag pair B (bit clear): 0x%08X"
                 % xor_bits, filename_b,
                 lambda r=rec_b: fb.build_vdf(fb.grid({0: r}, 1), 1,
                                              filename_b.split(".")[0]),
                 {"xor_bits": "0x%08X" % xor_bits, "variant": "clear"}),
        ]

    for bit, tag, cls in (
        (0x00000001, "E01", "ordinary"),   # keep bounds
        (0x00000010, "E02", "ordinary"),   # view-related
        (0x00000200, "E03", "ordinary"),   # destroyed-state-related
        (0x00000800, "E04", "ordinary"),   # light-related
        (0x00000200, "E05", "bridge"),     # destroyed-state on BRIDGE root
    ):
        cases.extend(flag_pair(tag, "%sa.vdf" % tag.lower(),
                               "%sb.vdf" % tag.lower(), bit, cls))

    def team_pair(team):
        fa = int(semantics.encode_object_flags(team=team))
        return [
            Case("E06-%d" % team, "E", "team=%d ordinary" % team,
                 "etm%d.vdf" % team,
                 lambda f=fa, t=team: fb.build_vdf(
                     fb.grid({0: fb.make_record("eft11", flags=f)}, 1), 1,
                     "etm%d" % t),
                 {"team": team}),
        ]

    cases.extend(team_pair(1))
    cases.extend(team_pair(2))
    return cases


def phase_f_cases():
    """BRIDGE/FLOOR: same geometry, different classification."""
    cases = []

    def trio(part_type, collision_nibble, filename):
        flags = (collision_nibble << 12)
        base = {
            0: fb.make_record("efq11", type_=part_type, flags=flags),
            1: fb.make_record("efd11", "efq11", type_=9),
        }
        return fb.build_vdf(fb.grid(base, 2), 2, filename.split(".")[0])

    cases.append(Case(
        "FBR-N01", "F", "identical geometry classified NONE (type 60 root)",
        "fbrn01.vdf", lambda: trio(60, 0, "fbrn01.vdf"),
        {"root_type": 60}))
    cases.append(Case(
        "FBR-B02", "F", "identical geometry classified BRIDGE (type 8)",
        "fbrb02.vdf", lambda: trio(8, int(semantics.COLLISION_STRUCTURE),
                                   "fbrb02.vdf"),
        {"root_type": 8}))
    return cases


def all_cases():
    cases = []
    cases.extend(phase_a_cases())
    cases.extend(phase_b_cases())
    cases.extend(phase_c_cases())
    cases.extend(phase_d_cases())
    cases.extend(phase_e_cases())
    cases.extend(phase_f_cases())
    seen = set()
    for case in cases:
        if case.filename in seen:
            _fail("duplicate asset filename %s" % case.filename)
        seen.add(case.filename)
    return cases


# ---------------------------------------------------------------------------
# Manifest + session record emission.
# ---------------------------------------------------------------------------

SESSION_CONTROLS = [
    "redux-exe-sha256 recorded",
    "renderer/backend recorded",
    "dll/mod inventory recorded",
    "mission/test-map revision recorded",
    "unmodified Redux (UNPATCHED) canonical baseline; patched runtime labeled separately",
    "per-asset SHA-256 manifest",
    "unique filename / full-restart reload discipline",
    "asset stems <= 8 characters",
    "signed/fractional/non-round translations included",
    "non-90-degree rotations included",
    "combined translation+rotation included",
    "non-identity parent transform included",
    "damage thresholds bracketed both directions; health/distance independent",
    "ObjectFlags/team conclusions scoped per asset class",
    "AI pathing evidence distinct from player traversal",
    "INCONCLUSIVE default where variance prevents proof",
]


def write_session_record(cases, path):
    lines = [
        "# Runtime Session Record",
        "",
        "Fill every field. Any row without complete controls is",
        "**INCONCLUSIVE** by definition (see",
        "`docs/RUNTIME_SEMANTIC_VERIFICATION.md` - Experimental controls).",
        "",
        "## Session identity",
        "",
        "| Field | Value |",
        "|---|---|",
        "| redux exe sha256 | |",
        "| redux version string | |",
        "| ogremain.dll sha256 | |",
        "| renderer/backend | |",
        "| winmm shim + version | |",
        "| other dll/mod inventory | |",
        "| mission/map file + sha256 | |",
        "| runtime label | UNPATCHED (canonical) / PATCHED:<label> |",
        "",
        "## Observation rows",
        "",
        ("Verdict vocabulary: VERIFIED (runtime) / DISPROVED / INCONCLUSIVE."
         ),
        "",
        "Reload discipline: changing any asset requires a NEW filename or a",
        "full game restart before the next observation (caches persist across",
        "menu reloads). Re-hash the deployed copy after copying.",
        "",
        "| case id | phase | asset sha256 | expected | observed | repeat n | "
        "verdict | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        lines.append("| %s | %s | | | | 1 | INCONCLUSIVE | |" %
                     (case.case_id, case.phase))
    lines += [
        "",
        "## Damage threshold bracketing checklist (phase D rows)",
        "",
        "- sweep health downward through each candidate boundary at FIXED view"
        " distance",
        "- sweep health upward through each boundary (return transition) at"
        " FIXED distance",
        "- sweep view distance through each LOD boundary at FIXED health",
        "- record exact health/distance pairs for every observed transition",
        "",
        "## Evidence-chain reminder",
        "",
        "For every verdict above, attach: authored property values (see",
        "manifest `authored`), exported byte hash, and the runtime",
        "observation. All three layers must agree before VERIFIED.",
        "",
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True,
                        help="output directory (never inside the repo)")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    cases = all_cases()

    assets = []
    for case in cases:
        data = case.build()
        path = os.path.join(args.out, case.filename)
        with open(path, "wb") as handle:
            handle.write(data)
        digest = hashlib.sha256(data).hexdigest()
        assets.append({
            "case": case.case_id,
            "phase": case.phase,
            "purpose": case.purpose,
            "file": case.filename,
            "bytes": len(data),
            "sha256": digest,
            "authored": case.authored,
        })
        print("wrote %s (%d bytes, sha256 %s)" %
              (path, len(data), digest[:16]))

    manifest = {
        "kit_version": 1,
        "discipline": {
            "filename_stem_limit": MAX_STEM_LEN,
            "reload_rule": "new content requires new filename OR full game "
                           "restart before the next observation",
            "runtime_labels": ["UNPATCHED", "PATCHED"],
            "verdicts": ["VERIFIED-runtime", "DISPROVED", "INCONCLUSIVE"],
        },
        "controls": SESSION_CONTROLS,
        "assets": assets,
    }
    manifest_path = os.path.join(args.out, "evidence_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    print("wrote %s (%d assets)" % (manifest_path, len(assets)))

    record_path = os.path.join(args.out, "RUNTIME_SESSION_RECORD.md")
    write_session_record(cases, record_path)
    print("wrote %s" % record_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
