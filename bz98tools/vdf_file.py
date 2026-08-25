# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""
Tolerant binary VDF container parser/serializer (pure Python, no Blender).

Goals:
    * Parse every stock section exactly like the legacy path.
    * Capture VLOC injection chunks as first-class semantic entries.
    * Preserve unknown/undocumented chunks verbatim (non-destructive edits).
    * Remember section ORDER so untouched files can round-trip byte-exactly.

This module is the "parser/model" layer; import_vdf/export_vdf are the Blender
adapters on top of it.
"""

import struct

from . import semantics
from . import vdf_classes

VDF_RECORD_SIZE = vdf_classes.GEOData().binlength  # 100


class RawChunk:
    """An unrecognized chunk preserved byte-for-byte."""

    def __init__(self, tag, full_bytes):
        self.tag = tag  # 4 bytes
        self.full_bytes = full_bytes  # includes 8-byte chunk header

    @property
    def declared_length(self):
        if len(self.full_bytes) >= 8:
            return struct.unpack_from("<i", self.full_bytes, 4)[0]
        return 0


class ParsedVDF:
    def __init__(self):
        # header
        self.bwd_header = b"BWD2"
        self.bwd_version = 8
        self.rev_header = b"REV\0"
        self.section_length = 12
        self.header_tail = 7
        self.header_matches_canonical = True

        # VDFC
        self.vdfc_name = ""
        self.vdfc_vehicletype = 0
        self.vdfc_vehiclesize = 0
        self.vdfc_lod_dists = [0.0] * 5
        self.vdfc_mass = 0.0
        self.vdfc_multiplyer = 0.0
        self.vdfc_drag = 0.0
        self.vdfc_null = 0

        # VGEO
        self.geocount = 0
        self.records = []  # flat list of 100-byte records, 28 bands * geocount

        # optional sections
        self.anim_present = False
        self.anim_header = None  # vdf_classes.ANIMHeader
        self.anim_elements = []
        self.anim_orientations = []
        self.anim_rotations = []
        self.anim_translations2 = []
        self.anim_positions = []

        self.colp_data = None  # 12 floats or None
        self.scps_tag = None  # "SPCS"/"SCPS" or None
        self.scps_data = None  # 3 ints

        self.vlocs = []  # semantics.VLOCChunk list
        self.raw_chunks = []  # RawChunk list

        self.trailing_garbage = b""

        # serialization plan: ordered section descriptors preserving file layout
        self.plan = []

    # -- convenience -------------------------------------------------------

    def band_records(self, band):
        start = band * self.geocount
        return self.records[start : start + self.geocount]

    def iter_part_records(self):
        """Yield (slot_index, record_bytes) for the base (damage 0, lod 0) band."""
        for slot, raw in enumerate(self.band_records(0)):
            yield slot, raw


def _read_record(raw):
    geo = vdf_classes.GEOData()
    geo.Read(raw, 0)
    return geo


def parse_vdf(file_content):
    data = bytes(file_content)
    parsed = ParsedVDF()
    pos = 0

    def need(count):
        return pos + count <= len(data)

    # ---- header ----
    header = vdf_classes.VDFHeader()
    if not need(header.binlength):
        raise ValueError("File too small to be a VDF")
    header.Read(data, pos)
    pos += header.binlength
    parsed.bwd_header = header.BWDHeader
    parsed.bwd_version = header.BWDVersion
    parsed.rev_header = header.REVHeader
    parsed.section_length = header.SectionLength
    parsed.header_tail = header.Unknown
    parsed.header_matches_canonical = (
        header.BWDHeader == b"BWD2"
        and header.REVHeader == b"REV\0"
        and header.BWDVersion == 8
        and header.SectionLength == 12
        and header.Unknown == 7
    )
    if header.BWDHeader != b"BWD2" or header.REVHeader != b"REV\0":
        raise ValueError("Not a VDF file (bad BWD2/REV magic)")

    # ---- VDFC ----
    vdfc = vdf_classes.VDFCHeader()
    vdfc.Read(data, pos)
    pos += vdfc.binlength
    parsed.vdfc_name = vdfc.name
    parsed.vdfc_vehicletype = vdfc.vehicletype
    parsed.vdfc_vehiclesize = vdfc.vehiclesize
    parsed.vdfc_lod_dists = [
        vdfc.lod1dist,
        vdfc.lod2dist,
        vdfc.lod3dist,
        vdfc.lod4dist,
        vdfc.lod5dist,
    ]
    parsed.vdfc_mass = vdfc.mass
    parsed.vdfc_multiplyer = vdfc.multiplyer
    parsed.vdfc_drag = vdfc.drag
    parsed.vdfc_null = int(getattr(vdfc, "null", 0))
    parsed.plan.append(("vdfc", None))

    # ---- EXIT terminator for the VDFC section ----
    exit_chunk = vdf_classes.EXITSection()
    exit_chunk.Read(data, pos)
    pos += exit_chunk.binlength
    parsed.plan.append(("exit", None))

    # ---- VGEO ----
    vgeo = vdf_classes.VGEOHeader()
    vgeo.Read(data, pos)
    pos += vgeo.binlength
    parsed.geocount = max(0, int(vgeo.geocount))
    total_records = 28 * parsed.geocount
    needed = total_records * VDF_RECORD_SIZE
    if pos + needed > len(data):
        raise ValueError(
            f"VGEO section truncated: expected {needed} bytes of records, "
            f"have {len(data) - pos}"
        )
    parsed.records = []
    for _ in range(total_records):
        parsed.records.append(data[pos : pos + VDF_RECORD_SIZE])
        pos += VDF_RECORD_SIZE
    parsed.plan.append(("vgeo", None))

    # ---- tolerant chunk walk ----
    exit_chunk = vdf_classes.EXITSection()
    while True:
        if pos + 8 > len(data):
            if pos < len(data):
                parsed.trailing_garbage = data[pos:]
            break
        tag_bytes = data[pos : pos + 4]

        if tag_bytes == b"EXIT":
            exit_chunk.Read(data, pos)
            pos += exit_chunk.binlength
            parsed.plan.append(("exit", None))
            continue

        if tag_bytes == b"ANIM":
            block, pos = _parse_anim(data, pos)
            (
                parsed.anim_present,
                parsed.anim_header,
                parsed.anim_elements,
                parsed.anim_orientations,
                parsed.anim_rotations,
                parsed.anim_translations2,
                parsed.anim_positions,
            ) = block
            parsed.plan.append(("anim", None))
            continue

        if tag_bytes == b"COLP":
            colp = vdf_classes.COLPSection()
            pos = colp.Read(data, pos)
            parsed.colp_data = [float(v) for v in colp.data]
            parsed.plan.append(("colp", None))
            continue

        if tag_bytes in (b"SPCS", b"SCPS"):
            scps = vdf_classes.SCPSSection()
            scps.Read(data, pos)
            parsed.scps_tag = scps.headername or tag_bytes.decode("ascii", "ignore")
            parsed.scps_data = [int(v) for v in scps.data]
            pos += scps.binlength
            parsed.plan.append(("scps", None))
            continue

        if tag_bytes == b"VLOC":
            declared = struct.unpack_from("<i", data, pos + 4)[0]
            body = max(0, declared - 8)
            full = data[pos : pos + max(8, declared)]
            payload = data[pos + 8 : pos + 8 + body]
            chunk = semantics.parse_vloc_payload(payload)
            chunk.raw_full_bytes = bytes(full)
            parsed.vlocs.append(chunk)
            pos += max(8, declared)
            parsed.plan.append(("vloc", len(parsed.vlocs) - 1))
            continue

        # Unknown chunk: preserve verbatim using its declared length when sane.
        declared = struct.unpack_from("<i", data, pos + 4)[0]
        if declared >= 8 and pos + declared <= len(data) and declared <= len(data):
            full = data[pos : pos + declared]
            pos += declared
        else:
            # Untrustworthy length: keep the remainder opaquely and stop.
            full = data[pos:]
            pos = len(data)
        parsed.raw_chunks.append(RawChunk(tag_bytes, full))
        parsed.plan.append(("raw", len(parsed.raw_chunks) - 1))

    return parsed


def _parse_anim(data, pos):
    header = vdf_classes.ANIMHeader()
    pos = header.Read(data, pos)

    elements = []
    for _ in range(max(0, header.elementscount)):
        element = vdf_classes.ANIMElement()
        pos = element.Read(data, pos)
        elements.append(element)

    orientations = []
    for _ in range(max(0, header.orientationscount)):
        orientation = vdf_classes.ANIMOrientation()
        pos = orientation.Read(data, pos)
        orientations.append(orientation)

    rotations = []
    for _ in range(max(0, header.rotationcount)):
        rotation = vdf_classes.ANIMRotation()
        pos = rotation.Read(data, pos)
        rotations.append(rotation)

    translations2 = []
    for _ in range(max(0, header.translation2count)):
        translation = vdf_classes.ANIMTranslation2()
        pos = translation.Read(data, pos)
        translations2.append(translation)

    positions = []
    for _ in range(max(0, header.positioncount)):
        position = vdf_classes.ANIMPosition()
        pos = position.Read(data, pos)
        positions.append(position)

    return (
        True,
        header,
        elements,
        orientations,
        rotations,
        translations2,
        positions,
    ), pos


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _serialize_vdfc(parsed):
    vdfc = vdf_classes.VDFCHeader()
    vdfc.name = parsed.vdfc_name
    vdfc.vehicletype = parsed.vdfc_vehicletype
    vdfc.vehiclesize = parsed.vdfc_vehiclesize
    (
        vdfc.lod1dist,
        vdfc.lod2dist,
        vdfc.lod3dist,
        vdfc.lod4dist,
        vdfc.lod5dist,
    ) = parsed.vdfc_lod_dists
    vdfc.mass = parsed.vdfc_mass
    vdfc.multiplyer = parsed.vdfc_multiplyer
    vdfc.drag = parsed.vdfc_drag
    vdfc.null = getattr(parsed, "vdfc_null", 0)
    return vdf_classes.serialize_section(vdfc)


def _serialize_anim_block(parsed):
    header = parsed.anim_header or vdf_classes.ANIMHeader()
    header.elementscount = len(parsed.anim_elements)
    header.orientationscount = len(parsed.anim_orientations)
    header.rotationcount = len(parsed.anim_rotations)
    header.translation2count = len(parsed.anim_translations2)
    header.positioncount = len(parsed.anim_positions)
    out = bytearray(vdf_classes.serialize_section(header))
    for element in parsed.anim_elements:
        out += vdf_classes.serialize_section(element)
    for orientation in parsed.anim_orientations:
        out += vdf_classes.serialize_section(orientation)
    for rotation in parsed.anim_rotations:
        out += vdf_classes.serialize_section(rotation)
    for translation in parsed.anim_translations2:
        out += vdf_classes.serialize_section(translation)
    for position in parsed.anim_positions:
        out += vdf_classes.serialize_section(position)
    return bytes(out)


def serialize_vdf(parsed, records=None):
    """Serialize a ParsedVDF back to bytes.

    records overrides parsed.records when provided (export path supplies the
    rebuilt 28-band grid).
    """
    if records is None:
        records = parsed.records

    out = bytearray()

    def put(chunk):
        out.extend(chunk)

    exit_bytes = vdf_classes.serialize_section(vdf_classes.EXITSection())

    # File header. Canonical layout; imported values preserved verbatim when
    # they match the canonical pattern, otherwise standard constants are
    # written (deviations are surfaced by import diagnostics).
    out.extend(
        struct.pack(
            "=4si4sii",
            parsed.bwd_header if parsed.header_matches_canonical else b"BWD2",
            8,
            parsed.rev_header if parsed.header_matches_canonical else b"REV\0",
            12,
            7,
        )
    )

    for kind, index in parsed.plan:
        if kind == "vdfc":
            put(_serialize_vdfc(parsed))
        elif kind == "vgeo":
            vgeo = vdf_classes.VGEOHeader()
            vgeo.sectionlength = ((len(records) // 28 * 100) * 28) + vgeo.binlength if len(records) else vgeo.binlength
            vgeo.geocount = parsed.geocount
            put(vdf_classes.serialize_section(vgeo))
            for raw in records:
                if len(raw) != VDF_RECORD_SIZE:
                    raw = raw.ljust(VDF_RECORD_SIZE, b"\0")[:VDF_RECORD_SIZE]
                put(raw)
        elif kind == "anim":
            if parsed.anim_present:
                put(_serialize_anim_block(parsed))
        elif kind == "colp":
            colp = vdf_classes.COLPSection()
            colp.data = parsed.colp_data or [0.0] * 12
            put(vdf_classes.serialize_section(colp))
        elif kind == "scps":
            if parsed.scps_tag is not None:
                scps = vdf_classes.SCPSSection()
                scps.headername = parsed.scps_tag
                scps.tag = (parsed.scps_tag or "SPCS").encode("ascii", "ignore")[:4].ljust(4, b"\0")
                scps.data = parsed.scps_data or [0, 0, 0]
                put(vdf_classes.serialize_section(scps))
        elif kind == "vloc":
            chunk = parsed.vlocs[index]
            raw = getattr(chunk, "raw_full_bytes", None)
            if raw and getattr(chunk, "preserve_raw", True):
                put(raw)
            else:
                payload = semantics.build_vloc_payload(chunk)
                put(b"VLOC" + struct.pack("<i", 8 + len(payload)))
                put(payload)
        elif kind == "raw":
            put(parsed.raw_chunks[index].full_bytes)
        elif kind == "exit":
            put(exit_bytes)

    if parsed.trailing_garbage:
        put(parsed.trailing_garbage)

    return bytes(out)


def new_empty_plan():
    """Plan for freshly authored vehicles (no imported layout to preserve).

    Mirrors the stock chunk order including the EXIT terminators the parser
    and the engine expect after each section.
    """
    return [
        ("vdfc", None),
        ("exit", None),
        ("vgeo", None),
        ("exit", None),
        ("colp", None),
        ("exit", None),
        ("scps", None),
        ("exit", None),
    ]
