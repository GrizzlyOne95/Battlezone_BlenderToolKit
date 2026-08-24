# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""
Advanced GEO/VDF semantic model.

This module is intentionally free of any Blender imports so the format-level
semantics can be unit tested with a plain Python interpreter.

Layers:
    parser/model   (vdf_classes, vdf_file)
    semantic model (this module)
    Blender adapter/UI (__init__.py, import_*/export_*)

Confidence vocabulary used everywhere:
    CONFIRMED - backed by decompiled bzone.exe 1.5 code / PDB symbols and
                cross-checked against Redux where noted.
    INFERRED  - consistent with evidence but not directly observed.
    UNKNOWN   - not understood; preserve raw bytes.

Sources: docs/GEO_TYPES_RESEARCH.md, docs/GEO_FLAGS_RESEARCH.md,
docs/GEO_FLAGS_RESEARCH_REVIEW.md, docs/EXPERIMENTAL_BINARY_FIELDS.md.
"""

import struct

# ---------------------------------------------------------------------------
# Part types (engine CLASS_ID_* values)
# ---------------------------------------------------------------------------

CONFIRMED = "CONFIRMED"
INFERRED = "INFERRED"
UNKNOWN = "UNKNOWN"

# behavior notes are kept short; they are surfaced as tooltips/diagnostics.
PART_TYPES = {
    0: ("NONE", CONFIRMED, "Passive rendered part; plain hierarchy node."),
    1: (
        "HELICOPTER",
        CONFIRMED,
        "DANGEROUS as VDF/SDF part: triggers <name>.odf remap; missing ODF crashes the engine.",
    ),
    2: ("STRUCTURE1", CONFIRMED, "Gameplay entity class; passive as a part. Entity footprint blocks pathing when used as ODF class."),
    3: (
        "POWERUP",
        CONFIRMED,
        "DANGEROUS as VDF/SDF part: triggers <name>.odf remap; missing ODF crashes the engine.",
    ),
    4: ("PERSON", CONFIRMED, "World entity class; passive as a part."),
    5: ("SIGN", CONFIRMED, "Gameplay entity class; passive as a part. Sign-class entities block only a perimeter ring in pathing."),
    6: (
        "VEHICLE",
        CONFIRMED,
        "DANGEROUS as VDF/SDF part: triggers <name>.odf remap; missing ODF crashes the engine.",
    ),
    7: ("SCRAP", CONFIRMED, "Scrap entity class; passive as a part. Scrap-class entities do not stamp path blocking."),
    # 8/9 are the drivable-deck controls (GEO_TYPES_RESEARCH.md section 5.1).
    8: (
        "BRIDGE",
        CONFIRMED,
        "Root class: every part's upward-facing collision polys become hover deck. BRIDGE entities are never path-stamped.",
    ),
    9: (
        "FLOOR",
        CONFIRMED,
        "Per-part drivable-floor opt-in on any entity. Requires collision geometry; only faces within ~66 deg of horizontal count.",
    ),
    10: ("STRUCTURE2", CONFIRMED, "Metal structure entity class; passive as a part."),
    11: (
        "SCROUNGE",
        CONFIRMED,
        "Camera-facing scrap/special. Keeps disk-authored bounding sphere (no recompute).",
    ),
    15: (
        "SPINNER",
        CONFIRMED,
        "Continuous rotator. Rate/ddr seeded from record tail ints. Stops while ObjectFlags bit 0x200 (destroyed) is set.",
    ),
    38: (
        "HEADLIGHT_MASK",
        CONFIRMED,
        "Invisible marker; headlight cone attaches here (Redux keys on bone name). Geometry never loaded.",
    ),
    40: (
        "EYEPOINT",
        CONFIRMED,
        "First-person camera origin (POV). Transform basis rows x25 drive view offset/shake, so scale matters. Geometry never loaded.",
    ),
    42: ("COM", CONFIRMED, "Defined in engine enum but no consumer found in 1.5; renders normally. 'Center of mass' label unverified."),
    50: ("WEAPON", CONFIRMED, "Weapon geometry role; weapon-parented parts get an extra damage-state rep slot."),
    51: ("ORDNANCE", CONFIRMED, "Projectile geometry role (assigned at runtime)."),
    52: ("EXPLOSION", CONFIRMED, "Explosion effect geometry role (runtime)."),
    53: ("CHUNK", CONFIRMED, "Debris chunk geometry role (runtime)."),
    54: ("SORT_OBJECT", CONFIRMED, "Defined in enum; zero references in 1.5 code. Renders normally."),
    55: (
        "NONCOLLIDABLE",
        CONFIRMED,
        "Forces the collision nibble of ObjectFlags to 0x1000 (no collision), same as flags bit 0x1 does.",
    ),
    60: ("VEHICLE_GEOMETRY", CONFIRMED, "Ordinary vehicle part. Near-root parts of this class are skipped in explosion-chunk generation."),
    61: ("STRUCTURE_GEOMETRY", CONFIRMED, "Defined in enum; no consumer found. Renders normally."),
    63: (
        "WEAPON_GEOMETRY",
        CONFIRMED,
        "Render-cache special case: treated as not-moving unless ObjectFlags bit 0x200000 is set.",
    ),
    64: ("ORDNANCE_GEOMETRY", INFERRED, "No direct branch found; plausibly in the static moving-objects class list."),
    65: (
        "TURRET_GEOMETRY",
        CONFIRMED,
        "Turret rotator. Name char 7: X/x = yaw slot, Y/y = pitch slot, anything else warns and assumes Y.",
    ),
    66: ("ROTOR_GEOMETRY", CONFIRMED, "Live per-frame gimbal: rotors roll with forward speed/throttle on hover craft."),
    67: (
        "NACELLE_GEOMETRY",
        CONFIRMED,
        "Live per-frame gimbal: nacelles pitch with throttle/steering; auto-parents flame emitters when none exist.",
    ),
    68: ("FIN_GEOMETRY", CONFIRMED, "Live per-frame gimbal: fins roll proportional to yaw rate."),
    69: ("COCKPIT_GEOMETRY", CONFIRMED, "Defined in enum; cockpit handling uses LOD naming + POV instead. Renders normally."),
    70: ("WEAPON_HARDPOINT", CONFIRMED, "Invisible attach point (name chars 5-7 matched). Producers collect these; more than 8 overflows a fixed array."),
    71: ("CANNON_HARDPOINT", CONFIRMED, "Invisible cannon muzzle slot (name chars 5-7)."),
    72: ("ROCKET_HARDPOINT", CONFIRMED, "Invisible rocket pod slot (name chars 5-7)."),
    73: ("MORTAR_HARDPOINT", CONFIRMED, "Invisible mortar tube slot (name chars 5-7)."),
    74: ("SPECIAL_HARDPOINT", CONFIRMED, "Special/hitch point; producer eject point. Keep unit scale: ancestor scale multiplies build throw speed."),
    75: (
        "FLAME_EMITTER",
        CONFIRMED,
        "Jet-thruster effect. Geometry not loaded on vehicles; animated spinning flame quad above ~10% throttle.",
    ),
    76: (
        "SMOKE_EMITTER",
        CONFIRMED,
        "Smoke source. Geometry not loaded on vehicles. More than 8 in one craft overflows a fixed engine array.",
    ),
    77: (
        "DUST_EMITTER",
        CONFIRMED,
        "Hover dust source. Geometry not loaded on vehicles; auto-created when missing (D3D option dependent).",
    ),
    81: (
        "PARKING_LOT",
        CONFIRMED,
        "Supply-pad/hangar effect center. Disk-authored bounding sphere/center stay authoritative for this class.",
    ),
}

# Values once listed by the toolkit that do NOT exist in the engine enum.
PHANTOM_PART_TYPES = {
    33: "LGT (legacy guess)",
    34: "RADAR",
}

# Classes whose .geo is never loaded on vehicles (AddReps exclusion list).
VEHICLE_INVISIBLE_CLASSES = frozenset({38, 40, 70, 71, 72, 73, 74, 75, 76, 77})
# Structure loader excludes fewer classes (emitters keep their geometry).
STRUCTURE_INVISIBLE_CLASSES = frozenset({38, 40, 70, 71, 72, 73, 74})

CRAFT_ODF_REMAP_CLASSES = frozenset({1, 3, 6})

EMITTER_SMOKE_CLASS = 76
EMITTER_DUST_CLASS = 77
HARDPOINT_WEAPON_CLASS = 70
ENGINE_FIXED_EMITTER_LIMIT = 8

EYEPOINT_CLASS = 40
BRIDGE_CLASS = 8
FLOOR_CLASS = 9
SPINNER_CLASS = 15


def is_known_part_type(value):
    return int(value) in PART_TYPES


def part_type_label(value):
    """Human readable label; unknown values keep their raw number visible."""
    value = int(value)
    if value in PART_TYPES:
        name, confidence, _note = PART_TYPES[value]
        return f"{value} {name}"
    if value in PHANTOM_PART_TYPES:
        return f"{value} {PHANTOM_PART_TYPES[value]} (NOT AN ENGINE CLASS)"
    return f"Unknown ({value} / 0x{value:04X})"


def part_type_note(value):
    value = int(value)
    if value in PART_TYPES:
        return PART_TYPES[value][2]
    if value in PHANTOM_PART_TYPES:
        return (
            "Not an engine class; nothing in the binary references it. "
            "Renders/collides like an ordinary part."
        )
    return (
        "Not in the engine enum; behaves exactly like type 0 (visible, collidable, "
        "no special handling). Raw value preserved for round trips."
    )


def part_type_confidence(value):
    value = int(value)
    if value in PART_TYPES:
        return PART_TYPES[value][1]
    return UNKNOWN


# ---------------------------------------------------------------------------
# ObjectFlags codec (VDF/SDF record trailing int -> _OBJ76.flags seed)
# ---------------------------------------------------------------------------

OBJFLAG_KEEP_BOUNDS = 0x000001  # SetObjBbox keeps authored volumes (CONFIRMED)
OBJFLAG_VIEW_RENDER = 0x000010  # AnimSprite::Render view test (CONFIRMED)
OBJFLAG_DESTROYED = 0x000200  # destroyed seed; IsAlive false (CONFIRMED)
OBJFLAG_LIGHT_ATTACHED = 0x000800  # light source attached (set by LOBJ) (CONFIRMED)

OBJFLAG_COLLISION_MASK = 0x00F000  # collision class nibble (CONFIRMED)
OBJFLAG_TEAM_MASK = 0x000F0000  # team id bits 16-19 (CONFIRMED)

COLLISION_NONE = 0x0000
COLLISION_NONCOLLIDABLE = 0x1000
COLLISION_DEFAULT = 0x2000
COLLISION_STRUCTURE = 0x3000

COLLISION_CLASS_CHOICES = (
    (COLLISION_NONE, "Unset (0)", "Collision nibble 0; stock assets ship this."),
    (
        COLLISION_NONCOLLIDABLE,
        "Non-collidable (0x1000)",
        "Same effect as NONCOLLIDABLE class / flags bit 0x1: part becomes non-collidable.",
    ),
    (
        COLLISION_DEFAULT,
        "Entity default (0x2000)",
        "Default for sign/scrap/spinner-class entities.",
    ),
    (
        COLLISION_STRUCTURE,
        "Structure (0x3000)",
        "Default for bridge/structure entities.",
    ),
)

KNOWN_OBJFLAG_MASK = (
    OBJFLAG_KEEP_BOUNDS
    | OBJFLAG_VIEW_RENDER
    | OBJFLAG_DESTROYED
    | OBJFLAG_LIGHT_ATTACHED
    | OBJFLAG_COLLISION_MASK
    | OBJFLAG_TEAM_MASK
)


def decode_object_flags(raw):
    """Split an ObjectFlags int into known fields plus the unknown residue."""
    raw = int(raw) & 0xFFFFFFFF
    # treat bit 31 as sign-preserved unknown data
    return {
        "keep_bounds": bool(raw & OBJFLAG_KEEP_BOUNDS),
        "view_render": bool(raw & OBJFLAG_VIEW_RENDER),
        "destroyed": bool(raw & OBJFLAG_DESTROYED),
        "light_attached": bool(raw & OBJFLAG_LIGHT_ATTACHED),
        "collision_class": raw & OBJFLAG_COLLISION_MASK,
        "team": (raw & OBJFLAG_TEAM_MASK) >> 16,
        "unknown": raw & ~KNOWN_OBJFLAG_MASK & 0xFFFFFFFF,
    }


def encode_object_flags(
    *,
    keep_bounds=None,
    view_render=None,
    destroyed=None,
    light_attached=None,
    collision_class=None,
    team=None,
    unknown=0,
):
    value = int(unknown) & ~KNOWN_OBJFLAG_MASK & 0xFFFFFFFF
    if keep_bounds:
        value |= OBJFLAG_KEEP_BOUNDS
    if view_render:
        value |= OBJFLAG_VIEW_RENDER
    if destroyed:
        value |= OBJFLAG_DESTROYED
    if light_attached:
        value |= OBJFLAG_LIGHT_ATTACHED
    if collision_class is not None:
        value |= int(collision_class) & OBJFLAG_COLLISION_MASK
    if team is not None:
        value |= (int(team) << 16) & OBJFLAG_TEAM_MASK
    return value


def apply_flag_bit(raw, mask, enabled):
    """Set/clear exactly the bits in mask, preserving every other bit."""
    raw = int(raw) & 0xFFFFFFFF
    mask = int(mask) & 0xFFFFFFFF
    if enabled:
        return raw | mask
    return raw & (~mask & 0xFFFFFFFF)


def unknown_flag_bits(raw):
    return int(raw) & ~KNOWN_OBJFLAG_MASK & 0xFFFFFFFF


def format_hex(value):
    return f"0x{int(value) & 0xFFFFFFFF:08X}"


# ---------------------------------------------------------------------------
# Authored bounds (GeoCenter/SphereRadius/BoxHalfHeight)
# ---------------------------------------------------------------------------

# Bounds authored in the record are only authoritative when ObjectFlags bit 0x1
# is set (or for terrain specials of class 11/81). Otherwise SetObjBbox
# recomputes them from LOD0 vertices at load. See GEO_TYPES_RESEARCH.md 5.10.


def bounds_are_authoritative(object_flags, part_type):
    if int(object_flags) & OBJFLAG_KEEP_BOUNDS:
        return True
    return int(part_type) in (11, 81)


def evaluate_authored_bounds(center, radius, half_extents):
    """Pure checks shared by validation/UI. Returns list of (severity, message)."""
    issues = []
    cx, cy, cz = (float(v) for v in center)
    radius = float(radius)
    hx, hy, hz = (float(v) for v in half_extents)

    if radius <= 0.0:
        issues.append(("WARNING", "Sphere radius is zero or negative; gibs/effects relying on it disappear."))
    if hx < 0.0 or hy < 0.0 or hz < 0.0:
        issues.append(("ERROR", "Negative box half-extent (inverted bounds); export would write a malformed volume."))

    def _extreme(v):
        return abs(v) > 100000.0

    if any(_extreme(v) for v in (cx, cy, cz, radius, hx, hy, hz)):
        issues.append(("WARNING", "Bound values are extreme (>100k); check for placeholder/garbage data."))
    return issues


def compare_bounds_to_geometry(stored_center, stored_radius, derived_center, derived_radius):
    """Heuristic comparison between authored values and mesh-derived bounds."""
    results = []
    sc = [float(v) for v in stored_center]
    dc = [float(v) for v in derived_center]
    sr = float(stored_radius)
    dr = float(derived_radius)

    offset = sum((a - b) ** 2 for a, b in zip(sc, dc)) ** 0.5
    if dr > 1e-6:
        ratio = sr / dr
        if sr > 0 and ratio > 4.0:
            results.append(
                (
                    "WARNING",
                    f"Authored sphere radius is {ratio:.1f}x the geometry-derived radius; "
                    "with ObjectFlags bit 0x1 this inflates broadphase/hitbox queries.",
                )
            )
        elif 0.0 < sr < dr * 0.25:
            results.append(
                (
                    "WARNING",
                    "Authored sphere radius is much smaller than the visible geometry; "
                    "with bit 0x1 set, projectiles/AI may pass through visually solid parts.",
                )
            )
    if offset > max(dr, 1.0) * 2.0 and offset > 0.5:
        results.append(
            (
                "INFO",
                f"Authored bound center sits {offset:.2f} units from the geometry-derived center.",
            )
        )
    return results


# ---------------------------------------------------------------------------
# VLOC chunk (vehicle-load part injection)
# ---------------------------------------------------------------------------

VLOC_TAG = b"VLOC"
VLOC_CONTEXT = "vhclload"

# Payload dword 0 dispatch (Process_VLOC_Chunk @ 00526CBE):
VLOC_HEADLIGHT = 38  # night-only synthetic "hdlt_msk" part; loads fixed hdlv_msk.geo
VLOC_POV = 40  # custom POV: payload class doubles as new part class -> craft state +0xF4
VLOC_IDSIZES = 42  # two {IDType,size} pairs copied into craft state +0xE8/+0xF0

VLOC_KIND_LABELS = {
    VLOC_HEADLIGHT: (
        "Headlight Mask Injection",
        CONFIRMED,
        "Night-only synthetic 'hdlt_msk' part parented to root; engine loads fixed hdlv_msk.geo at band (0,4).",
    ),
    VLOC_POV: (
        "Custom POV Injection",
        CONFIRMED,
        "Creates a class-40 transform node stored into the eyepoint craft-state slot (+0xF4). Invisible transform node.",
    ),
    VLOC_IDSIZES: (
        "ID/Size Pairs",
        CONFIRMED,
        "Copies payload dwords 6-9 into craft state; semantics beyond that are undocumented.",
    ),
}


class VLOCChunk:
    """One VLOC injection entry.

    kind_value is the raw first payload dword. For 38/40/generic the next
    12 floats are an engine-space MAT_3D_FILE matrix (right/up/front/posit).
    For 42 the payload is preserved opaquely (two id/size pairs live at
    dwords 6-9 but their meaning is UNDOCUMENTED).
    """

    def __init__(self):
        self.kind_value = 40
        self.class_id = 0  # only meaningful for generic kinds (payload dword 0 doubles as class)
        self.matrix = [0.0] * 12  # confirmed field for kinds 38/40/generic
        self.opaque_payload = b""  # full original payload bytes (preservation)
        self.label = ""

    @property
    def kind_key(self):
        if self.kind_value == VLOC_HEADLIGHT:
            return "HEADLIGHT"
        if self.kind_value == VLOC_POV:
            return "POV"
        if self.kind_value == VLOC_IDSIZES:
            return "IDSIZES"
        return "GENERIC"

    @property
    def confidence(self):
        if self.kind_value in VLOC_KIND_LABELS:
            return VLOC_KIND_LABELS[self.kind_value][1]
        return INFERRED  # generic injection path is confirmed; per-class effects vary

    def describe(self):
        if self.kind_value in VLOC_KIND_LABELS:
            return VLOC_KIND_LABELS[self.kind_value][0]
        return f"Generic Part Injection (class {self.kind_value})"


def parse_vloc_payload(payload):
    """Decode a VLOC payload. Unknown bytes stay available via opaque_payload."""
    chunk = VLOCChunk()
    chunk.opaque_payload = bytes(payload)
    if len(payload) >= 4:
        chunk.kind_value = struct.unpack_from("<I", payload, 0)[0]
    if len(payload) >= 52 and chunk.kind_value != VLOC_IDSIZES:
        floats = struct.unpack_from("<12f", payload, 4)
        chunk.matrix = [float(v) for v in floats]
        chunk.class_id = chunk.kind_value
    elif len(payload) >= 52:
        floats = struct.unpack_from("<12f", payload, 4)
        chunk.matrix = [float(v) for v in floats]
    return chunk


def build_vloc_payload(chunk):
    """Serialize a VLOC payload.

    For untouched entries (opaque_payload present and matrix untouched flag)
    we simply re-emit the captured bytes so round trips are byte-exact.
    """
    if chunk.opaque_payload and getattr(chunk, "preserve_raw", True):
        return bytes(chunk.opaque_payload)

    if chunk.kind_value == VLOC_IDSIZES:
        # No confirmed authoring model for id/size pairs; emit what we have.
        out = bytearray(chunk.opaque_payload or b"\x00" * 40)
        return bytes(out[: len(out) // 4 * 4])

    payload = bytearray()
    payload += struct.pack("<I", int(chunk.kind_value) & 0xFFFFFFFF)
    payload += struct.pack("<12f", *(float(v) for v in chunk.matrix))
    return bytes(payload)


def vloc_runtime_notes(kind_value):
    """Engine caveats worth surfacing in UI/validation (all CONFIRMED unless noted)."""
    notes = []
    if kind_value != VLOC_HEADLIGHT:
        notes.append("Injected part loads no .geo: invisible transform node.")
    if kind_value == VLOC_POV:
        notes.append(
            "If the VGEO hierarchy already contains a class-40 part, obj_find_class may resolve to that one instead depending on tree order (INFERRED)."
        )
    if kind_value == SPINNER_CLASS:
        notes.append("Injected spinners bypass NewObj: rate stays zero, so they never rotate.")
    if kind_value in (70, 71, 72, 73, 74):
        names_note = "Injected hardpoints have empty names; FindHardpoint matches by name suffix, so weapons cannot bind to them."
        notes.append(names_note)
    if kind_value in (75, 76, 77):
        notes.append(
            "Injected emitters exist before smoke-source collection counts slots; they consume the fixed 8-slot budget."
        )
    notes.append("Requires the root object to already have craft class handlers (class_ptr allocated).")
    return notes


# ---------------------------------------------------------------------------
# Damage representation bands (VGEO band grid)
# ---------------------------------------------------------------------------

# Band layout (stock-layout confirmed):
#   band = lod_slot * 4 + damage_state   with 7 lod slots x 4 damage states.
# Stock vehicles exercise lod slots 0/1/2 (toolkit LOD1/LOD2/LOD3) at bands
# 0/4/8 with damage state 0. Damage variants for a part's primary geometry
# live in bands 1-3 (lod slot 0, damage states 1-3). This refines
# GEO_TYPES_RESEARCH.md section 5.11, which describes the grid as
# "7 damage states x 4 LODs"; the transposed reading would put damage block 1
# on top of stock LOD2 content, contradicting every stock file. All other
# mechanics documented there (per-part filename donation, LOD-0 retry
# fallback, dead SelectRep caller) are unaffected by the axis naming.

DAMAGE_STATE_COUNT = 4
LOD_SLOT_COUNT = 7
VGEO_BAND_COUNT = DAMAGE_STATE_COUNT * LOD_SLOT_COUNT  # 28

RECORD_SIZE_VDF = 100

AUTHORED_DAMAGE_STATES = (1, 2, 3)


def band_index(lod_slot, damage_state):
    """Band order used by stock assets: band = lod_slot * 4 + damage_state."""
    return int(lod_slot) * DAMAGE_STATE_COUNT + int(damage_state)


def band_coords(index):
    return divmod(int(index), DAMAGE_STATE_COUNT)


def is_base_band(index):
    return int(index) == band_index(0, 0)


def synthesize_variant_record(base_record_bytes, new_name):
    """Return a 100-byte damage-band record copied from the d0l0 record with
    only the 8-byte name replaced (the documented authoring recipe)."""
    buf = bytearray(base_record_bytes[:RECORD_SIZE_VDF])
    name_bytes = new_name.encode("ascii", errors="ignore")[:8]
    buf[0:8] = name_bytes.ljust(8, b"\0")
    return bytes(buf)


def records_equal_modulo_name(a_bytes, b_bytes):
    if len(a_bytes) != RECORD_SIZE_VDF or len(b_bytes) != RECORD_SIZE_VDF:
        return False
    return a_bytes[8:] == b_bytes[8:]


class DamageVariantTable:
    """Raw preservation + authoring model for VGEO damage bands.

    - base_records: dict slot_index -> 100-byte band-0 record (lod0, damage0)
    - variant_records: dict (slot_index, band) -> raw 100-byte record captured
      from file (preserves filler fields we do not model)
    - authored_names: dict (slot_index, damage_state) -> geo name override for
      the primary (lod slot 0) geometry; states 1..3.
    """

    def __init__(self):
        self.base_records = {}
        self.variant_records = {}
        self.authored_names = {}

    def capture_band_records(self, records, geocount):
        """records: flat list of 100-byte records for all 28*geocount slots.

        Fills base_records (band 0) and variant_records keyed by
        (slot_within_band, flat_band_index).
        """
        self.base_records.clear()
        self.variant_records.clear()
        for idx, raw in enumerate(records):
            band, slot = divmod(idx, max(1, int(geocount)))
            if band == 0:
                self.base_records[slot] = raw
            else:
                self.variant_records[(slot, band)] = raw

    def get_variant_name(self, slot, damage_state):
        return self.authored_names.get((int(slot), int(damage_state)))

    def set_variant_name(self, slot, damage_state, name):
        if name:
            self.authored_names[(int(slot), int(damage_state))] = str(name)[:8]
        else:
            self.authored_names.pop((int(slot), int(damage_state)), None)

    def has_damage_content(self):
        """True when any lod-slot-0 damage state band (1..3) carries a name.

        Other populated bands are LOD slots or unmodeled content, not
        damage variants; a 235-file stock scan shows only bands 0/4/8 used.
        """
        if self.authored_names:
            return True
        for (_slot, band), raw in self.variant_records.items():
            if band < 1 or band > 3:
                continue
            name_bytes = raw[:8].split(b"\0")[0]
            if not name_bytes:
                continue
            name = name_bytes.decode("ascii", errors="ignore").strip().lower()
            if name and not name.startswith("null"):
                return True
        return False

    def build_band_record(self, slot, band, geocount=None):
        """Record to write for (slot, band).

        Priority:
          1. authored name override (lod slot 0 only) -> synthesized from base
          2. captured raw record                      -> emitted verbatim
          3. otherwise                                -> zeros (NULL slot)
        """
        lod_slot, damage_state = band_coords(band)
        if lod_slot == 0 and damage_state != 0:
            name_override = self.authored_names.get((int(slot), int(damage_state)))
            if name_override and int(slot) in self.base_records:
                return synthesize_variant_record(
                    self.base_records[int(slot)], name_override
                )
        key = (int(slot), int(band))
        raw = self.variant_records.get(key)
        if raw is not None:
            return raw
        return b"\0" * RECORD_SIZE_VDF

    def diff_against_reconstruction(self, slot, band, geocount=None):
        """True if the stored raw record differs from what we would synthesize
        from the base record + authored names (i.e. real unknown filler)."""
        lod_slot, damage_state = band_coords(band)
        name_override = self.authored_names.get((int(slot), int(damage_state)))
        base = self.base_records.get(int(slot))
        raw = self.variant_records.get((int(slot), int(band)))
        if raw is None:
            return False
        if base is None:
            return any(raw)
        candidate = (
            synthesize_variant_record(base, name_override)
            if name_override
            else synthesize_variant_record(base, raw[:8].split(b"\0")[0].decode("ascii", errors="ignore"))
        )
        return not records_equal_modulo_name(candidate, raw)


# ---------------------------------------------------------------------------
# Deck / floor face analysis (pure helper fed from Blender mesh data)
# ---------------------------------------------------------------------------

DECK_NORMAL_THRESHOLD = 0.4  # world normal y > 0.4 => within ~66 deg of horizontal


def classify_deck_faces(upward_components):
    """Given an iterable of face up-axis components (Blender +Z after the
    addon's standard axis mapping equals engine +Y), return (total, drivable).
    Faces with component > DECK_NORMAL_THRESHOLD can become hover deck."""
    total = 0
    drivable = 0
    for comp in upward_components:
        total += 1
        if comp > DECK_NORMAL_THRESHOLD:
            drivable += 1
    return total, drivable


def emitter_overflow(count, limit=ENGINE_FIXED_EMITTER_LIMIT):
    return int(count) > int(limit)
