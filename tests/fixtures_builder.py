# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""
Synthetic, non-copyrighted VDF fixture builder.

Generates minimal binary vehicles exercising every advanced semantic feature:
ordinary parts, special part classes, known/unknown ObjectFlags, unknown part
types, custom bounds, eyepoints, bridge/floor decks, damage-variant bands,
VLOC injections of every documented kind, an unknown chunk, and an ANIM block
with non-zero tail dwords.

Run directly to regenerate tests/fixtures/*.vdf:
    python tests/fixtures_builder.py
"""

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools import semantics, vdf_classes, vdf_file  # noqa: E402

FIXTURE_DIR = os.path.join(_HERE, "fixtures")

IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]


def make_record(
    name,
    parent="WORLD",
    matrix=None,
    center=(0.0, 0.0, 0.0),
    radius=1.0,
    half=(0.5, 0.5, 0.5),
    type_=60,
    flags=0,
):
    geo = vdf_classes.GEOData()
    geo.name = name[:8]
    geo.parent = parent[:8]
    geo.matrix = list(matrix or IDENTITY)
    geo.geocenter = list(center)
    geo.sphereradius = radius
    geo.boxhalfheight = list(half)
    geo.type = int(type_)
    geo.geoflags = int(flags)
    return vdf_classes.serialize_section(geo)


def build_vdf(records, geocount, vdfc_name="fixture", anim_block=None):
    out = bytearray()
    out += vdf_classes.serialize_section(vdf_classes.VDFHeader())

    vdfc = vdf_classes.VDFCHeader()
    vdfc.name = vdfc_name
    vdfc.vehicletype = 1
    vdfc.vehiclesize = 2
    (
        vdfc.lod1dist,
        vdfc.lod2dist,
        vdfc.lod3dist,
        vdfc.lod4dist,
        vdfc.lod5dist,
    ) = (50.0, 120.0, 250.0, 400.0, 600.0)
    vdfc.mass = 4.0
    vdfc.multiplyer = 1.0
    vdfc.drag = 0.9
    out += vdf_classes.serialize_section(vdfc)
    out += vdf_classes.serialize_section(vdf_classes.EXITSection())

    vg = vdf_classes.VGEOHeader()
    vg.sectionlength = len(records) * 100 + vg.binlength
    vg.geocount = geocount
    out += vdf_classes.serialize_section(vg)
    out += b"".join(records)
    out += vdf_classes.serialize_section(vdf_classes.EXITSection())

    if anim_block is not None:
        header, elements, orientations, rotations, translations2, positions = anim_block
        header.elementscount = len(elements)
        header.orientationscount = len(orientations)
        header.rotationcount = len(rotations)
        header.translation2count = len(translations2)
        header.positioncount = len(positions)
        header.sectionlength = (
            header.binlength
            + len(elements) * 148
            + len(orientations) * 132
            + len(rotations) * 20
            + len(translations2) * 16
            + len(positions) * 16
        )
        out += vdf_classes.serialize_section(header)
        for element in elements:
            out += vdf_classes.serialize_section(element)
        for orientation in orientations:
            out += vdf_classes.serialize_section(orientation)
        for rotation in rotations:
            out += vdf_classes.serialize_section(rotation)
        for translation in translations2:
            out += vdf_classes.serialize_section(translation)
        for position in positions:
            out += vdf_classes.serialize_section(position)
        out += vdf_classes.serialize_section(vdf_classes.EXITSection())

    colp = vdf_classes.COLPSection()
    colp.data = [3.0, 2.5, -2.5, -3.0, 1.5, 1.2, -1.2, -1.5, 1.0, 0.9, -0.9, -1.0]
    out += vdf_classes.serialize_section(colp)
    out += vdf_classes.serialize_section(vdf_classes.EXITSection())
    return bytes(out)


def add_chunk(out_bytes, tag, payload):
    return bytes(out_bytes) + tag + struct.pack("<i", 8 + len(payload)) + payload


NULL_RECORD = b"\x00" * 100


def grid(base_records, geocount, variants=None):
    """28-band record grid from {slot: record} plus optional variant fills."""
    records = [NULL_RECORD] * (28 * geocount)
    for slot, raw in base_records.items():
        records[slot] = raw
    for (slot, band), raw in (variants or {}).items():
        records[band * geocount + slot] = raw
    return records


# ---------------------------------------------------------------------------
# Individual fixtures
# ---------------------------------------------------------------------------


def fixture_ordinary():
    """1. ordinary part."""
    records = grid({0: make_record("fix11")}, 1)
    return build_vdf(records, 1, "fxord")


def fixture_special_types():
    """2. known special part types (rotor/nacelle/fin/flame/dust/emitters)."""
    parts = [
        ("fxt11", 66),  # ROTOR_GEOMETRY
        ("fxn11", 67),  # NACELLE_GEOMETRY
        ("fxf11", 68),  # FIN_GEOMETRY
        ("fxe11", 76),  # SMOKE_EMITTER
    ]
    base = {}
    for slot, (name, type_) in enumerate(parts):
        parent = "WORLD" if slot == 0 else "fxt11"
        base[slot] = make_record(name, parent, type_=type_)
    return build_vdf(grid(base, 4), 4, "fxspec")


def fixture_known_flags():
    """3. known flags only (keep bounds + structure collision nibble)."""
    raw = semantics.encode_object_flags(
        keep_bounds=True, collision_class=0x3000, team=2
    )
    records = grid({0: make_record("fxk11", flags=raw)}, 1)
    return build_vdf(records, 1, "fxflag")


def fixture_unknown_flags():
    """4. unknown flag bits preserved (task example shape)."""
    raw = 0x00081234  # known nibble/team bits plus undocumented high bits
    records = grid({0: make_record("fxu11", flags=raw)}, 1)
    return build_vdf(records, 1, "fxuflg")


def fixture_unknown_type():
    """5. unknown part type value."""
    records = grid({0: make_record("fxx11", type_=0x2A, flags=0x8100)}, 1)
    return build_vdf(records, 1, "fxunkn")


def fixture_custom_bounds():
    """9. authored bounds far from geometry defaults."""
    records = grid(
        {
            0: make_record(
                "fxb11",
                center=(12.5, -3.25, 7.75),
                radius=33.0,
                half=(9.0, 4.0, 6.0),
                flags=int(semantics.OBJFLAG_KEEP_BOUNDS),
            )
        },
        1,
    )
    return build_vdf(records, 1, "fxbnds")


def fixture_eyepoint():
    """10. eyepoint part with transform."""
    pov_matrix = [
        0.9, 0.1, 0.0,
        0.0, 1.0, 0.1,
        0.1, 0.0, 0.9,
        0.0, 0.85, 0.45,
    ]
    records = grid(
        {
            0: make_record("fxh11"),
            1: make_record("fxp11", "fxh11", matrix=pov_matrix, type_=40),
        },
        2,
    )
    return build_vdf(records, 2, "fxpov")


def fixture_bridge_floor():
    """8. BRIDGE root + FLOOR deck parts."""
    base = {
        0: make_record("fxr11", type_=8, flags=int(semantics.COLLISION_STRUCTURE) << 12),
        1: make_record("fxd11", "fxr11", type_=9),
        2: make_record("fxw11", "fxr11", type_=60),
    }
    return build_vdf(grid(base, 3), 3, "fxbrdg")


def fixture_damage_rep():
    """7. damage representations in lod-slot-0 bands 1..3 with odd filler."""
    base = {
        0: make_record("fxv11", radius=2.0),
        1: make_record("fxt11", "fxv11", type_=65),
    }
    variants = {}
    for state in (1, 2, 3):
        variants[(0, band := state)] = make_record(f"fxvd{state}", radius=2.0)
    # turr keeps its name in state 1 (explicit no-op entry with filler byte)
    keep = bytearray(make_record("fxt11", "fxv11", type_=65))
    keep[42] = 0x7F  # undocumented filler inside an ignored field
    variants[(1, 1)] = bytes(keep)
    return build_vdf(grid(base, 2, variants), 2, "fxdmge")


def fixture_combination():
    """11. everything at once."""
    base = {
        0: make_record(
            "cxa11",
            center=(1.0, 2.0, 3.0),
            radius=9.0,
            half=(2.0, 3.0, 4.0),
            type_=60,
            flags=0x0010_3000 | 0x8100,
        ),
        1: make_record("cxp11", "cxa11", type_=40),
        2: make_record("cxr11", "cxa11", type_=66),
        3: make_record("cxd11", "cxa11", type_=9),
    }
    variants = {(0, 1): make_record("cxad1", center=(1.0, 2.0, 3.0), radius=9.0)}
    records = grid(base, 4, variants)

    anim_header = vdf_classes.ANIMHeader()
    anim_header.name = "."
    element = vdf_classes.ANIMElement()
    element.index = 0
    element.unknowngeoflag = [(i * 3) % 97 for i in range(32)]
    element.start, element.length, element.loop, element.speed = 1, 10, 2, 30.0
    orientation = vdf_classes.ANIMOrientation()
    orientation.name = "cxa11"
    orientation.unknown = 5
    orientation.matrix1 = [1.0] * 12
    orientation.matrix2 = list(IDENTITY)
    orientation.rotationindex, orientation.rotationcount = 0, 0
    orientation.translation2index, orientation.translation2count = 0, 0
    orientation.positionindex, orientation.positioncount = 0, 0
    anim_block = (anim_header, [element], [orientation], [], [], [])

    out = build_vdf(records, 4, "fxcombo", anim_block=anim_block)

    payload_headlight = struct.pack("<I", 38) + struct.pack(
        "<12f", *(IDENTITY[:9] + [0.0, 1.1, 0.35])
    )
    out = add_chunk(out, b"VLOC", payload_headlight)
    payload_pov = struct.pack("<I", 40) + struct.pack(
        "<12f", *(IDENTITY[:9] + [0.0, 0.7, 0.4])
    )
    out = add_chunk(out, b"VLOC", payload_pov)
    payload_generic = struct.pack("<I", 77) + struct.pack(
        "<12f", *(IDENTITY[:9] + [0.5, 0.0, 0.1])
    )
    out = add_chunk(out, b"VLOC", payload_generic)
    payload_idsizes = struct.pack("<12I", 42, 0, 0, 0, 0, 0, 7, 256, 9, 512, 0, 0)
    out = add_chunk(out, b"VLOC", payload_idsizes)
    unknown_payload = b"\xDE\xAD\xBE\xEF" * 6
    out = add_chunk(out, b"SCHK", unknown_payload)
    return out


ANIM_FIXTURES = {
    "ordinary.vdf": fixture_ordinary,
    "special_types.vdf": fixture_special_types,
    "known_flags.vdf": fixture_known_flags,
    "unknown_flags.vdf": fixture_unknown_flags,
    "unknown_type.vdf": fixture_unknown_type,
    "custom_bounds.vdf": fixture_custom_bounds,
    "eyepoint.vdf": fixture_eyepoint,
    "bridge_floor.vdf": fixture_bridge_floor,
    "damage_rep.vdf": fixture_damage_rep,
    "combination.vdf": fixture_combination,
}


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    for filename, builder in ANIM_FIXTURES.items():
        path = os.path.join(FIXTURE_DIR, filename)
        data = builder()
        with open(path, "wb") as handle:
            handle.write(data)
        print(f"wrote {path} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
