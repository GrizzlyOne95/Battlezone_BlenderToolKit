# Advanced GEO/VDF Semantic Authoring

This document describes the advanced semantic authoring layer for legacy
Battlezone 98 / Battlezone 98 Redux GEO and VDF content: engine part classes,
`ObjectFlags`, VLOC part injection, damage representations, authored bounds,
eyepoints, bridge/floor decks, unknown-data preservation, and the validation
rules built around them.

**Confidence vocabulary.** Everything below is labeled:

- **CONFIRMED** — verified in decompiled `bzone.exe` 1.5 code with PDB symbols,
  cross-checked against Redux where noted. Sources:
  `GEO_TYPES_RESEARCH.md`, `GEO_FLAGS_RESEARCH.md`,
  `GEO_FLAGS_RESEARCH_REVIEW.md`.
- **INFERRED** — consistent with the evidence but not directly observed.
- **UNKNOWN / preserved verbatim** — not understood; the toolkit keeps the raw
  bytes so nothing is lost.

Implementation layers (kept separate on purpose):

```
parser/model     bz98tools/vdf_classes.py, bz98tools/vdf_file.py   (no Blender)
semantic model   bz98tools/semantics.py                            (no Blender)
Blender adapter  import_vdf.py, export_vdf.py, __init__.py, validation.py
```

The pure layers are covered by `tests/run_tests.py` and do not require Blender.

---

## 1. Part types (engine CLASS_ID_* values)

The `GEO Type` selector lists confirmed engine class names. Key facts that
changed how the toolkit presents them:

| Value | Name | Engine behavior |
|---:|---|---|
| 8 | BRIDGE | **CONFIRMED**: root class turns *every* part's upward-facing collision polys into hover deck. BRIDGE entities are never stamped into the pathing grid. |
| 9 | FLOOR | **CONFIRMED**: per-part drivable-floor opt-in. Needs collision geometry; only faces within ~66° of horizontal count. Zero pathing role. |
| 15 | SPINNER | **CONFIRMED**: continuous rotator seeded from record tail ints; halts while ObjectFlags bit 0x200 is set. |
| 40 | EYEPOINT | **CONFIRMED**: POV camera origin; transform basis rows ×25 drive view offset/shake, so scale matters. |
| 66/67/68 | ROTOR/NACELLE/FIN | **CONFIRMED**: animated every frame by `HoverCraft::UpdateGimbals`. |
| 75/76/77 | FLAME/SMOKE/DUST emitters | **CONFIRMED**: geometry never loaded on vehicles; smoke >8 per craft overflows a fixed engine array (validated). |
| 1 / 3 / 6 | HELICOPTER/POWERUP/VEHICLE | **CONFIRMED crash cause**: triggers a `<partname>.odf` remap whose failure NULL-dereferences. |
| 33 / 34 | LGT/RADAR | **CONFIRMED phantoms**: no such engine constants exist; values render like ordinary parts. Kept in the picker labeled as such so imported files display honestly. |

Unknown imported values are shown as `Unknown (NN / 0x00XX)` and preserved
exactly — the exporter writes the raw number, never an enum lookup.

## 2. ObjectFlags (the record's trailing int)

The VDF/SDF record's last int is **not** a render flag: it seeds the live
`_OBJ76.flags` state bitfield (**CONFIRMED**, full-dword copy in `NewObj`).
Known fields now have dedicated controls under
*Battlezone GEO → Advanced Semantics*:

| Control | Bits | Confidence / notes |
|---|---|---|
| Keep Authored Bounds | 0x0001 | CONFIRMED. `SetObjBbox` skips recompute; volumes then feed broadphase/hitbox queries. Also forces the collision nibble to non-collidable for that part. |
| View Render Test | 0x0010 | CONFIRMED (`AnimSprite::Render`). |
| Spawn Destroyed | 0x0200 | CONFIRMED. Engine believes the object is dead from spawn (path blocking skipped, spinners halt, liveness checks break). Advanced use only. |
| Light Attached | 0x0800 | CONFIRMED (set by LOBJ at runtime). |
| Collision Class | 0xF000 nibble | CONFIRMED (`obj_get/set_collision`). NONCOLLIDABLE class forces 0x1000. Undocumented nibble values display as "Reserved" and round-trip. |
| Team Seed | bits 16–19 | CONFIRMED (`get_obj_team`). |
| Unknown bits | remainder | Preserved exactly. Editing any known checkbox only flips its own bits (e.g. disabling a bit of `0x…8124` yields `0x…8104`-style results, never a masked rewrite). |

Stock assets ship `ObjectFlags = 0` in all scanned records (1,243 VDF records;
re-verified against 235 local addon VDFs).

## 3. Authored bounds (GeoCenter / SphereRadius / BoxHalfHeight)

Behavior (**CONFIRMED**): record bounds are recomputed from LOD0 vertices at
load unless ObjectFlags bit 0x1 is set — or unless the part is class 11
(SCROUNGE) or 81 (PARKING_LOT), which keep disk values unconditionally.

Authoring model:

- Imported parts default to **Bounds Mode = Preserve Imported/Custom**, so a
  parse → export → parse cycle reproduces the original bytes even when
  Blender's mesh-derived bounds differ.
- **Auto (legacy)** keeps the historical behavior for newly authored parts
  (generate from geometry while *Auto Generate SDF Collision Data* is on).
- **Recalculate From Geometry** forces derivation at export.
- The *Advanced Semantics* panel shows stored vs derived center/radius side by
  side and explains whether the stored values are currently authoritative.

Validation flags zero/negative radii, negative half-extents, extreme values,
and stored-vs-derived mismatches — always explaining the engine consequence,
never hard-blocking intentional overrides.

## 4. Eyepoint

Class-40 parts get first-class handling:

- Import creates a `SINGLE_ARROW` empty when the referenced `.geo` file is
  absent (mirroring the spinner-helper convention), so POVs are visible,
  movable, and exportable without dummy meshes.
- Validation warns about duplicate eyepoints, POVs outside the LOD1 set, and
  object scale ≠ 1 (scale feeds the ×25 camera math).
- The exact coordinate-space semantics of the file are preserved: transforms
  round-trip through the same YZX remap used by all records.

Craft without any eyepoint auto-create one named `eyepoint`
(**CONFIRMED**), so absence degrades gracefully but placement control is lost.

## 5. Damage representations

The VGEO section holds 28 bands per vehicle: **7 LOD slots × 4 damage states**,
laid out as `band = lod_slot * 4 + damage_state`.

> Layout note / correction: stock assets populate only bands 0, 4 and 8
> (verified across 235 local addon VDFs plus the earlier 99-file census). Under
> the transposed "7 damage × 4 LOD" reading, band 4 would be damage-state 1 and
> stock vehicles would show wreck meshes at full health. The lod-major layout
> above is the only reading consistent with stock data; it refines section 5.11
> of `GEO_TYPES_RESEARCH.md`. All other mechanics documented there (per-part
> filename donation, LOD-0 retry fallback, dead `SelectRep` caller) are
> unaffected by the axis naming.

What this means practically (**CONFIRMED** unless noted):

- Only band 0 (lod 0, damage 0) defines names/hierarchy/transforms. Every other
  populated slot donates a filename for its `(part, state)` cache key.
- Stock engines never select damage states above 0 — nothing calls
  `ObjTree_SelectRep` in 1.5 or Redux. Authored variants are inert until an
  external driver exists; they are forward-compatible content.
- Structures cannot carry damage variants: SDF registration reuses the d0 name
  for every band (**CONFIRMED**).

Authoring workflow: set `Damaged 1 / Heavily Damaged / Wreck Mesh` names on a
part (Advanced Semantics panel). Export synthesizes band records 1–3 from the
base record with only the name replaced. Fill every state you intend to use —
the runtime fallback retries only LOD-0 of the current state, so sparse fills
can blank a part once a driver activates swapping.

Preservation: variant records captured from imported files are compared against
that reconstruction; anything different (undocumented filler bytes, per-band
differences) is stored opaquely and re-emitted byte-for-byte. Content in bands
outside 1–3 is likewise preserved verbatim.

Validation covers references to nonexistent parts, self-references, unfilled
state gaps, and vehicles-only enforcement.

## 6. VLOC part injection

VLOC is an engine-wired chunk in the vehicle loader ("vhclload") that injects
new parts at load time (**CONFIRMED**, `Process_VLOC_Chunk`; zero uses across
all scanned stock VDFs):

| Payload dword 0 | Effect |
|---:|---|
| 38 | Night-only synthetic `hdlt_msk` headlight-mask part; loads fixed `hdlv_msk.geo`. The only kind that renders geometry. |
| 40 | Custom POV: class-40 node into the eyepoint craft-state slot (+0xF4). Invisible transform node. |
| 42 | Two id/size pairs copied into craft state; semantics undocumented → payload preserved opaquely. |
| other | Generic injection: dword 0 doubles as the new part's class id + authored matrix. Invisible transform node. |

Workflow: the *Battlezone → VLOC Injection* side panel lists entries; add
Headlight/POV/Generic kinds, optionally bind a Blender object to author the
matrix from its local transform (same axis math as GEO records), or leave
imported payloads untouched for byte-exact preservation.

Caveats surfaced in tooltips/validation (**CONFIRMED** unless noted): injected
spinner rates stay zero (bypasses `NewObj` seeding); injected hardpoints cannot
bind weapons (name lookup needs chars 5–7); injected emitters consume the fixed
8-slot budgets; root must already have craft class handlers; if the VGEO tree
already contains a class-40 part, POV resolution order is INFERRED.

## 7. Bridge / floor / drivable decks

Separation of concerns (**all CONFIRMED**):

- Part semantics: BRIDGE(8) root collects every part's upward collision polys;
  FLOOR(9) opts individual parts in otherwise. Faces steeper than ~66° never
  become deck.
- Entity blocking: path blocking is stamped per *entity* by ODF class — only
  STRUCTURE1(2)/STRUCTURE2(10)/TURR-signatures block their footprint; SIGN(5)
  blocks a perimeter ring; SCRAP(7)/BRIDGE(8) entities never stamp. Destroyed
  objects stop blocking entirely (bit 0x200 seed therefore disables it).
- Terrain: geometric cliffs (adjacent height step > 101 units) and lava markers
  are the only blocked terrain; no entity class can change cliff tiles.
- Material behavior: terrain materials do not apply to GEO decks.

Validation explains these relationships where they can bite: FLOOR parts
without near-horizontal faces, flags-bit-0x1 traps on deck parts (strips
collision → removes floor faces; flagging everything yields a map-sized rect),
destroyed-seed usage, and BRIDGE-root pathing implications. Intentional unusual
setups remain allowed — these are warnings/info with reasons, not errors.

## 8. Unknown-data preservation

The toolkit now behaves as a non-destructive editor for VDF containers:

- Section order observed at import is remembered and replayed at export.
- Unrecognized chunks (any tag) are captured verbatim and written back
  unchanged. Stock corpora contain none, so this only activates for
  third-party or future content.
- VDFC trailing int, the ANIM element's `meshIndex[32]` dwords 1-32
  (layout VERIFIED in both engines' code; values unread at runtime - see
  `EXPERIMENTAL_BINARY_FIELDS.md`), `tagANIMOBJ_MESH.flags`, SPCS/SCPS spelling,
  COLP floats, and VGEO filler bytes are all preserved.
- Known limitation: exact interleaving of preserved chunks relative to
  regenerated sections follows the recorded plan; if a file interleaves
  unknown chunks *inside* what the toolkit treats as one section, those bytes
  still round-trip (captured whole) but their position within the section is
  not independently editable.
- Legacy exports wrote 31 record bands while declaring 28; the writer now emits
  exactly 28. Old files' trailing pad is captured as opaque bytes and restored,
  so re-exports of untouched imports remain byte-exact.

If meaningful edits make byte-exactness impossible (e.g. an edited matrix),
everything else still preserves; this is stated per-field in tooltips rather
than silently diverging.

## 9. Validation summary

New checks (severity / rationale shown inline in the report panel):

- ERROR: smoke-emitter count > 8 (fixed-array overflow); negative half-extents;
  damage variant referencing a nonexistent part; damage variants on SDF.
- WARNING: unknown ObjectFlags bits present; destroyed-seed set; oversized /
  undersized authored bounds vs geometry; suspicious eyepoint scale/placement/
  duplicates; FLOOR parts with no drivable faces; flags-bit-0x1 on deck parts;
  unfilled damage-state gaps; weapon-hardpoint count > 8; VLOC entries during
  SDF export.
- INFO: unknown part types preserved opaquely; phantom classes; authored
  bounds being decorative without bit 0x1; BRIDGE-root pathing behavior;
  FLOOR/pathing separation; inert damage states; preserved chunks/bands.

Run via *Quick Tools → Validate Vehicle / presets*, or automatically before
export. Results list the offending Blender object for each entry.

## 10. Testing

`python tests/run_tests.py` runs 33 bpy-free tests covering: flag codec
preservation (including the unknown-bits-survive-known-edit case), part-type
metadata, VLOC payload round trips for all documented kinds, damage-table
synthesis/preservation/filler survival, deck-face classification, and full
parse → serialize → parse cycles over ten synthetic fixtures
(`tests/fixtures/*.vdf`, generated by `tests/fixtures_builder.py`) asserting
byte-exact round trips and semantic equality. No copyrighted game assets are
included; real-file validation was performed locally against a retail Redux
install (235 addon VDFs: 100% parse success, 100% byte-exact re-serialization,
band census confirming §5's layout).
