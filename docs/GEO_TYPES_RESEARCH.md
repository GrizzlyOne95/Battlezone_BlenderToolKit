# GEO Types (Class IDs) — Engine Behavior Report

**Question:** The toolkit exposes a `GEOType` int on every VDF/SDF GEO part (e.g. 40 = POV
eyepoint). Many listed types are labeled "unknown / untested" and some values aren't listed
at all. What does the Battlezone 1.5 engine actually *do* with each value, and what happens
to unknown ones?

**Method:** Same as `GEO_FLAGS_RESEARCH.md` — PDB-symbolized bzone.exe 1.5 decompilation
(`GIT/BZ1_Source/1.5`), plus the LLVM PDB dump in
`BZR-OpenShim/reverse_engineering/badlands/legacy_bz1_exact_full/pdb_reference/llvm/publics.txt`
which preserves the **numeric values** of every `CLASS_ID_*` constant (`S_CONSTANT`,
type `OBJECT_CLASS_T`). All line numbers below are `all_decompiled.c` unless a
`functions/…` path is given.

---

## 1. How the engine consumes the Class int

```
VDF/SDF record (100 B) ──Class int @0x5C──▶ NewObj()
    ├─ classes 1 / 3 / 6  → Craft_GetClass(name): look up <name>.odf and use ITS class
    │                        (GameObjectClass::Find returns NULL on miss → crash)   :341502
    ├─ ClassCreate(obj, class): stores obj->class_id, then                          :288640
    │     idx = ClassIDtoIndex(class)                                               :288470
    │     funk[] table has only 14 slots; miss → returns 0
    │     if (idx > 0): allocate class_ptr via new_fn, register in ObjList/InitList
    │     if (idx == 0): NO handler — object is a passive transformed hierarchy node
    ├─ class == 15 (SPINNER): Spinner_SetRate/Spinner_SetDDR seeded from the record's
    │   Target/ddr ints                                                             :341520
    └─ AddReps/AddStructReps: parts whose class is NOT in the "invisible helper"
        exclusion list get their .geo loaded via GeoCache_AddRep                    :341661/:341783
```

Key structural facts:

- **The dispatch table has exactly 14 slots** (`ClassIDtoIndex` scan bound `uVar1 < 0xe`;
  fast path bound `CLASS_ID_BRIDGE|CLASS_ID_VEHICLE` = 8|6 = 14). Any class id without a
  slot falls back to index 0 = "no class functions".
- **Unknown ids do not crash and are not rejected.** The part is created, parented,
  rendered, and collidable like type 0 — it simply gets no special behavior.
- Several behaviors live **outside** the table: craft/building/effect code explicitly scans
  hierarchies comparing `obj->class_id` (POV, turrets, nacelles, hardpoints, emitters,
  spinners, rotors, fins).

> **Methodology note (important):** many of those out-of-table consumers compare the class
> against **numeric immediates through temp locals** (`if (tmp == 0x42)`), so a grep for
> `CLASS_ID_*` names alone under-reports usage. This report originally mislabeled rotor/
> fin/nacelle/weapon-geometry as "defined only" on exactly that mistake; the table below
> reflects a corrected sweep over hex immediates `0x2A`–`0x51` in comparison context
> (filtering out DirectInput key maps, Lua format chars and loop bounds that share the
> same constants). In raw-offset terms `_OBJ76.class_id` sits at +0xAC
> (e.g. `*(int *)(obj + 0xac) == 0x3f`).

---

## 2. Authoritative enum (from bzint.pdb S_CONSTANT records)

| Value | PDB name | Referenced in 1.5 code? |
|---:|---|---|
| 0 | CLASS_ID_NONE | yes |
| 1 | CLASS_ID_HELICOPTER | yes |
| 2 | CLASS_ID_STRUCTURE1 | yes |
| 3 | CLASS_ID_POWERUP | yes |
| 4 | CLASS_ID_PERSON | yes |
| 5 | CLASS_ID_SIGN | yes |
| 6 | CLASS_ID_VEHICLE | yes |
| 7 | CLASS_ID_SCRAP | yes |
| 8 | CLASS_ID_BRIDGE | yes |
| 9 | CLASS_ID_FLOOR | yes |
| 10 | CLASS_ID_STRUCTURE2 | yes |
| 11 | CLASS_ID_SCROUNGE | yes |
| 12–14 | *(gap — no constants exist)* | — |
| 15 | CLASS_ID_SPINNER | yes |
| 16–37 | *(gap)* | — |
| 38 | CLASS_ID_HEADLIGHT_MASK | yes |
| 39 | *(gap)* | — |
| 40 | CLASS_ID_EYEPOINT | yes |
| 41 | *(gap)* | — |
| 42 | CLASS_ID_COM | **defined only** |
| 43–49 | *(gap)* | — |
| 50 | CLASS_ID_WEAPON | yes |
| 51 | CLASS_ID_ORDNANCE | yes |
| 52 | CLASS_ID_EXPLOSION | yes |
| 53 | CLASS_ID_CHUNK | yes |
| 54 | CLASS_ID_SORT_OBJECT | **defined only** |
| 55 | CLASS_ID_NONCOLLIDABLE | yes |
| 56–59 | *(gap)* | — |
| 60 | CLASS_ID_VEHICLE_GEOMETRY | yes (1 site) |
| 61 | CLASS_ID_STRUCTURE_GEOMETRY | **defined only** |
| 62 | *(gap)* | — |
| 63 | CLASS_ID_WEAPON_GEOMETRY | yes (render-cache special case) |
| 64 | CLASS_ID_ORDNANCE_GEOMETRY | maybe (static class list) |
| 65 | CLASS_ID_TURRET_GEOMETRY | yes |
| 66 | CLASS_ID_ROTOR_GEOMETRY | yes (per-frame gimbal anim) |
| 67 | CLASS_ID_NACELLE_GEOMETRY | yes (gimbal + auto-flame) |
| 68 | CLASS_ID_FIN_GEOMETRY | yes (per-frame gimbal anim) |
| 69 | CLASS_ID_COCKPIT_GEOMETRY | **defined only** |
| 70 | CLASS_ID_WEAPON_HARDPOINT | yes |
| 71 | CLASS_ID_CANNON_HARDPOINT | yes |
| 72 | CLASS_ID_ROCKET_HARDPOINT | yes |
| 73 | CLASS_ID_MORTAR_HARDPOINT | yes |
| 74 | CLASS_ID_SPECIAL_HARDPOINT | yes |
| 75 | CLASS_ID_FLAME_EMITTER | yes |
| 76 | CLASS_ID_SMOKE_EMITTER | yes |
| 77 | CLASS_ID_DUST_EMITTER | yes |
| 81 | CLASS_ID_PARKING_LOT | yes |

Toolkit list vs PDB: all shared names/values match. Two toolkit entries are **phantoms —
they do not exist in the engine enum**: `33 LGT ("legacy guess")` and `34 RADAR`. Nothing
in the binary references such classes; the engine treats those values like any other
unknown id.

---

## 3. Per-type behavior detail

### Invisible-helper exclusion lists

Two slightly different lists decide whether a part's `.geo` file is ever loaded
(no load ⇒ never rendered; transforms/hierarchy still work):

- **AddReps (VDF/XGEO)** excludes: HEADLIGHT_MASK(38), EYEPOINT(40),
  WEAPON/CANNON/ROCKET/MORTAR/SPECIAL hardpoints (70–74), FLAME(75), SMOKE(76),
  DUST(77) emitters — :341661–341666.
- **AddStructReps (SDF/SGEO)** excludes: EYEPOINT(40), hardpoints 70–74,
  HEADLIGHT_MASK(38) — but **not** the three emitters (:341783–341788). On structures an
  emitter-classed part would still load geometry under this code path.

### Type-by-type

| Value | Engine behavior (bzone.exe 1.5) | Evidence |
|---|---|---|
| 0 NONE | Passive rendered part; root/dummy nodes. No handler (index 0). | ClassCreate gate :288650 |
| 1 HELICOPTER, 3 POWERUP, 6 VEHICLE | **Remapped through `<partname>.odf` lookup** (`Craft_GetClass` → `GameObjectClass::Find`). If the name matches no ODF/class, Find returns NULL and reading `->class_id` crashes. This is the engine mechanism behind the toolkit's "type 1/3 crash as VDF/SDF" warnings (6 likewise). | NewObj :341502; Find NULL paths :1889050 area (`TraceError("GameObject \"%s\" not found")`, `"uses unknown class label"`) |
| 2 STRUCTURE1, 10 STRUCTURE2, 5 SIGN, 7 SCRAP | Gameplay-entity classes (`Building::Init`) in the world-object switch; as VDF/SDF *parts*: passive rendered, no part-level consumer. Entity collision default 0x3000 (structures) / 0x2000 (sign/scrap). | :181997, :129213 |
| 4 PERSON | World-entity class (`Person::Init`); passive as a part. | :182003 |
| 8 BRIDGE | As an **entity/root class**: enables whole-model deck collection — every part's upward-facing collision polys become hover floor (§5.1); structure-grade default collision 0x3000; groups with structures in netcode/save switches; distinct explosion-class bucket when hit. | :129209, :130619, :187350, :349035 |
| 9 FLOOR | Per-part drivable-floor opt-in on any entity (the only floor contributor unless the root is BRIDGE). Requires the part to have collision geometry. | :130599 |
| 11 SCROUNGE | Camera-facing scrap/special. Runtime loader assigns class to children of terrain specials (`flags \|= 0x40`); excluded from bounding-sphere recompute (disk GeoCenter/SphereRadius kept); dedicated handlers `Init_/Delete_Scrounge_Object`, `Get_Scrounge_Pos`, `Scrounge_LOD`. | :293429–3434, :337614, function_index 004ed73b… |
| 15 SPINNER | Continuous rotator. Rate/ddr seeded from the record's Target/ddr ints at load (`param_2->Class == 0xf` in NewObj); `CommTower::StartSpinners/StopSpinners` toggle the destroyed bit (0x200) on spinner parts; entity-level default collision 0x2000. | :341520, :152693, :152713, :129215 |
| 38 HEADLIGHT_MASK | Invisible marker (excluded both loaders). Headlight cone attaches here (Redux visual system keys on the bone name). | :341661, :341788 |
| 40 EYEPOINT (POV) | First-person camera origin. Craft auto-creates one named `"eyepoint"` with this class if the model lacks it; `HoverCraft::UpdateEyepoint` consumes it; excluded from geo loading. | :152449–456, :270132, :294202 |
| 42 COM | Defined only — zero references in 1.5 code. "Center of mass" label unverified by any consumer. | absent from decompiled refs |
| 50 WEAPON | Weapon geometry role; in AddReps, weapon-parented parts get an extra rep for damage-state block 2; world-entity Weapon init uses class. | :341672, :376636 |
| 51 ORDNANCE | Projectile geometry role (assigned at runtime by ordnance system). | :363512, :365340 |
| 52 EXPLOSION | Explosion effect geometry role (runtime). | :352489 |
| 53 CHUNK | Debris chunk geometry role (runtime; `Render_Chunk_Object`, `ChunkEffect`). | :238857, :238941, :239334 |
| 54 SORT_OBJECT | Defined only. | absent |
| 55 NONCOLLIDABLE | Forces the collision nibble of `_OBJ76.flags` to 0x1000 (= no collision), same as flags bit 0x1 does. | :128994–996 |
| 60 VEHICLE_GEOMETRY | Exemption in explosion-chunk generation: near-root vehicle-geometry parts are skipped when creating debris chunks. | :239244 |
| 61 STRUCTURE_GEOMETRY | Defined only. | absent |
| 63 WEAPON_GEOMETRY | Render-cache classification: `Cache_Is_Moving_Obj` special-cases class 63 — treated as *not* moving unless `_OBJ76.flags` bit 0x200000 is set. Likely also a member of the static 5-entry `Obj76_Moving_Objects_ID[]` list used by the same check. | :286374–383 (004E7CA1) |
| 64 ORDNANCE_GEOMETRY | No direct branch found; plausibly one of the 5 classes in the `Obj76_Moving_Objects_ID[]` static list (values live in .data, not recoverable from this decomp). | :286374 |
| 65 TURRET_GEOMETRY | `TurretCraft::FindTurret` scans the hierarchy: id ending `X`/`x` → yaw slot array, `Y`/`y` → pitch slot, anything else warns `Unusual turret id "%.8s" … assuming Y`. This is the tx#/ty# rotator mechanism. | :228887–900, :229944 |
| 66 ROTOR_GEOMETRY | **Live per-frame behavior on hover craft:** `HoverCraft::UpdateGimbals` walks the part tree every frame and applies a roll matrix to each rotor part driven by forward speed and throttle (`(-front - k*throttle) * dt * 3.0`) — rotors visibly spin up with thrust/turn input. Numeric compare `== 0x42`. | :193638–661 |
| 67 NACELLE_GEOMETRY | Two consumers: (a) `UpdateGimbals` pitches nacelles with throttle/steering, sign-aware of facing direction; (b) `HoverCraft::HoverCraft` collects nacelle parts at construction and, if no FLAME_EMITTER parts exist, parents an auto-created flame emitter under each nacelle. | :193659–688, :193458–482 |
| 68 FIN_GEOMETRY | **Live per-frame behavior:** `UpdateGimbals` rolls steering fins proportional to yaw rate (`(-front - k*yawRate) * dt * 3.0`). Numeric compare `== 0x44`. | :193688–701 |
| 69 COCKPIT_GEOMETRY | Defined only — cockpit handling in 1.5 is done via LOD naming and POV, not this class. | absent |
| 70 WEAPON_HARDPOINT | Invisible attach point; found by name via `FindHardpoint` (suffixes gc/gr/gm/gs + digit); Carrier hardpoint slots; prod-unit smoke emitter role per toolkit docs. HUD weapon-ring icons are keyed to classes 70–74 inclusive (`class_id` range test indexing RING_MAPS per type). | :341661, :269713, function_index 004950ac |
| 71 CANNON_HARDPOINT | Same invisible attach-point treatment (cannon muzzle slots). | :341663 |
| 72 ROCKET_HARDPOINT | Same (rocket pods). | :341663 |
| 73 MORTAR_HARDPOINT | Same (mortar tubes). | :341664 |
| 74 SPECIAL_HARDPOINT | Special/hitch point: `Carrier::SetHardpoint` marks the special index; `FindHitch` scans for it (tug carrying); Producer eject points for vehicles/powerups are created with this class. | :143368, :227982, :215930, :215969 |
| 75 FLAME_EMITTER | **The jet-thruster effect.** Excluded from geo load on VDFs; also auto-created under nacelles when missing (see 67). `UpdateGimbals` collects flame parts each frame and, when `throttle > 0.1`, the craft isn't dead (`flags & 0x600` clear) and arcade mode is off, animates them as roll matrices spinning at ±5·time — the classic spinning flame quad. | :341665, :193478, :193702, :193740–760 |
| 76 SMOKE_EMITTER | Smoke source; collected by scanning children for the class into smokeList. Excluded from VDF geo load. | :154472, :341666 |
| 77 DUST_EMITTER | Hover dust source; collected by `UpdateGimbals` (when D3D flag `useD3D & 4`) and created dynamically by HoverCraft if missing. Excluded from VDF geo load. | :193496, :193465, :193707, :341666 |
| 81 PARKING_LOT | Supply-pad/hangar effect center: excluded from bounding-sphere recompute so the disk-authored SphereRadius/GeoCenter from the VDF record stay authoritative. | :337614 |
| 12–14, 16–37, 39, 41, 43–49, 56–59, 62, 80, 82+, **incl. toolkit phantoms 33 & 34** | Not in the enum. `ClassIDtoIndex` → 0 → no handler; not in any exclusion list ⇒ **rendered and collidable like an ordinary part**; nothing branches on them anywhere in the executable. | :288470–484 |

---

## 4. Answers to the specific questions

> Does the engine do anything with unused/unknown geo types?

- **Truly unknown values (anything not in §2):** no. They behave exactly like type 0 while
  remaining visible/collidable — the safest possible fallback. No validation, no warning,
  no crash.
- **The craft-part animation classes are very much alive on VDFs** (user-reported and
  engine-confirmed): rotor(66), nacelle(67) and fin(68) are animated every frame by
  `HoverCraft::UpdateGimbals`, and flame(75)/dust(77) emitters drive the visible thruster
  and hover-dust effects. These were initially missed because the comparisons compile to
  numeric immediates (`== 0x42`/`0x43`/`0x44`/`0x4b`/`0x4d`) rather than symbolic names.
- Remaining inert in bzone.exe 1.5: 42 COM, 54 SORT_OBJECT, 61 STRUCTURE_GEOMETRY,
  69 COCKPIT_GEOMETRY (defined, no consumer found), with 64 ORDNANCE_GEOMETRY only
  plausibly referenced via the static moving-objects class list. These render normally
  and are safe to set, but nothing in the shipped executable branches on them.
- **The dangerous values are 1, 3, 6:** they trigger an ODF-name remap whose failure mode
  is a NULL dereference — the long-standing "types 1/3 crash as VDF/SDF" lore, now
  precisely explained.

Confidence labels: architecture and enum values CONFIRMED (decompiled code + PDB
constants); the rotor/nacelle/fin/flame/dust behaviors CONFIRMED at
`HoverCraft::HoverCraft` (0049CE34) and `HoverCraft::UpdateGimbals` (0049D01C region);
"inert" verdicts carry the caveat that static .data tables (e.g. the 5-entry
`Obj76_Moving_Objects_ID[]`) can reference class values without appearing in decompiled
code; Redux-parity INFERRED but unverified (follow-up: diff the Redux binary's exclusion
lists and gimbal code).

## 5. Undocumented mechanics inventory

Features found in the engine that the toolkit does not currently document or expose.

### 5.1 BRIDGE(8) and FLOOR(9) are the drivable-surface controls

The toolkit labels both "likely no extra behavior" — wrong. The hover-height system
builds "floor decks" like this:

```
Floor_InitEntity(entity)                                                    :130615
    isBridge = (entity ROOT part class == CLASS_ID_BRIDGE)
    CollectObjFloor(root, entity, Identity, isBridge)                       :130600
        walk every part (depth-first):
            if part has collision geom AND (NOT isBridge OR part.class == FLOOR)
                CollectFloorFaces(...)
```

- Root typed **BRIDGE(8)** ⇒ **every part's** collision polygons become candidate deck.
- Otherwise, only parts individually typed **FLOOR(9)** contribute.
- `CollectFloorFaces` (:130489) keeps only polygons whose world-space normal has
  **y > 0.4** — i.e. within ≈66° of horizontal. Walls and steep ramps are never
  drivable, even on a bridge.
- Deck data is attached per entity; hover queries (`FindFloor`) sample these lists.

Authoring consequences: mark a structure root as 8 to make the whole model traversable,
or cherry-pick walkable sub-parts as 9; keep deck faces near-horizontal.

### 5.2 Spinner(15) configuration lives in the record tail ints

For class-15 parts `NewObj` seeds state from the record:

```c
if (param_2->Class == 0xf) {
    Spinner_SetRate(p_Var4, param_2->Target);   // Target = VECTOR_3D @ +0x68
    Spinner_SetDDR (p_Var4, param_2->ddr);      // ddr     = int      @ +0x64
}
```

- `Spinner_SetRate` (004EE182) stores `2π · (x,y,z)` — **the record's Target vector is
  revolutions per second about each local axis**. In SDF records this is precisely the
  toolkit's `ddr`(int) + `x`,`y`,`z` float tail; the trailing "time" float is not
  referenced by any spinner code.
- `Spinner_SetDDR` (004EE10A) writes `ddr` into two state dwords (+0xC,+0x10 of
  `class_ptr`).
- `Spinner_Simulate` (004EE051, registered per frame) rotates the part by
  `Spinner(transform, axisRadPerSec, TimeStepLocal())` **only while flags bit 0x200
  (destroyed) is clear** — `CommTower::StartSpinners/StopSpinners` toggle exactly that
  bit (:152693/:152713), so spinners halt when their building dies.
- Caveat resolved: VDF records are only 100 bytes, so for a VDF spinner the engine reads
  `ddr` from the **next record's name bytes 0–3** (as an int) and the Target vector from
  **name bytes 4–7 plus the next record's first two matrix floats** (record layout:
  name@0, matrix@8, parent@56, class@92, flags@96). This is precisely why the toolkit's
  spinner-helper convention places a dummy/helper GEO *after* the target — the helper's
  trailing name chars and leading matrix values encode axis and speed. Byte-exact mapping
  confirmed from `NewObj` + the 100-byte VGEO stride.

### 5.3 Disk bounding spheres stay authoritative for SCROUNGE(11) and PARKING_LOT(81)

`AddTerrainSpecial` (:337607) recomputes `bSphere` origin+radius from mesh geometry for
every terrain special **except** classes 11 and 81, which keep the values authored in the
record (`GeoCenter`/`SphereRadius`/box fields). For all other classes those record fields
are placeholders overwritten at load.

### 5.4 Engines self-heal missing effect parts

- Craft without an eyepoint get one created automatically, named `"eyepoint"`,
  class 40, positioned from ODF data (:152449–457).
- A hover craft with nacelles but **no flame parts** gets a flame emitter auto-parented
  under each nacelle (:193462–482); a missing dust emitter is likewise auto-created
  (D3D-dependent) (:193493–497). Authors can omit them, but then cannot control their
  placement/appearance.

### 5.5 Class-driven presentation effects

- **Radar:** `radar_object()` returns true only for classes 1 and 6 (:292411) — nothing
  else ever blips, regardless of size.
- **Moving-entity classification:** `dynamic_object()` = class ∈ {1,3,4,6} (:292398);
  `Cache_Is_Moving_Obj` gates static/dynamic render-cache placement off a static class
  list with a WEAPON_GEOMETRY(63) exception (:286374).
- **HUD weapon rings:** icons are selected by hardpoint class in the inclusive range
  70–74; out-of-range hardpoint classes fall back to the first ring entry (:269713).
- **Impact explosions vary by victim class**: helicopters, bridge/structure2, and
  everything else pull different `ExplosionClass` entries when hit (:349024–038).
- **Friendly craft collisions are harmless**: same-team craft-vs-craft impacts zero out
  their damage via the team nibble (:340862–868).

### 5.6 VLOC — a live part-injection chunk the stock game never uses

The VDF loader walks a 14-entry chunk table (`ReadBWD2File(…, VDFChunkDefs, 0xe, …)`,
:342663). Stock files exercise only VDFC/VGEO/EXIT (+COLP/SPCS/ANIM where present);
several remaining entries are recognized-but-ignored stubs (see §5.7). One is not:

**`Process_VLOC_Chunk`** (00526CBE, context tag `"vhclload"`) dispatches on the chunk's
first dword and *creates new parts on the vehicle at load time*:

| Value | Behavior |
|---|---|
| `38` | Night-only headlight injection: if `Is_Day_Time()`, writes 0 to craft-state +0x104; otherwise builds a synthetic part named `"hdlt_msk"` (parent = vehicle root, matrix from the 48 payload bytes), attaches it via `AddReps`, registers it in craft state +0x104, and loads a **fixed** rep file `"hdlv_msk.geo"` at band (0,4). |
| `40` | Creates a part via `create_obj_ext` with **class taken from the payload's first dword** and transform from the payload bytes, then stores it into craft state +0xF4 — the same slot `HoverCraft` uses for the eyepoint (:294202). A custom-POV injector. |
| `42` | Copies two id/size pairs from the payload into craft state +0xE8/+0xF0. |
| any other | Same generic path as 40 — an arbitrary new child part of arbitrary class with an authored matrix, injected into the hierarchy at load. |

**Payload layouts** (payload = bytes after the chunk's 8-byte sub-header; MAT_3D_FILE =
12 floats right/up/front/posit):

| Value dword 0 | Rest of payload |
|---|---|
| `38` | dwords 1–12 = matrix for the synthetic `"hdlt_msk"` part |
| `40` or any unmatched value | dword 0 doubles as the new part's **class id**; dwords 1–12 = its matrix |
| `42` | dwords 6–9 = two `{IDType, size}` pairs copied into craft state |

Additional mechanics worth noting before implementing:

- The generic (non-38) path bypasses `NewObj`: no bounds seeding, and **no
  `Spinner_SetRate`** — an injected class-15 node spins at rate zero (inert).
- Injected emitters exist before `Craft::FindSmokeSource`/`Producer::FindSmokeSource`
  run, so they count toward the 8-slot limits in §5.12.
- The 38 part is registered via `AddTerrainSpecial`, so it renders through the
  terrain-special list and gets fresh computed bounds (its class is 0, not 11/81).
- Prerequisite: `root->class_ptr != NULL` — only classes with funk allocations have
  craft state to write into.

Stock corpus check: **zero VLOC chunks across all 99 stock VDFs**, and the toolkit has no
VLOC support. This is a complete, engine-wired modding surface (inject lights, masks,
POVs or extra parts via hex edits) that nothing documents. Caveats: the handler
dereferences the root object's `class_ptr` (so the craft must already have funk handlers),
and payload layout beyond dword 0 / 12 dwords for value 38 follows the ObjectType record
shape.

**Runtime semantics — what each injection actually does:**

- **38 renders something; 40 and generic values do not.** Only the 38 path calls
  `GeoCache_AddRep` (the fixed `hdlv_msk.geo` mask quad at rep band (0,4)). The 40 and
  generic paths call only `create_obj_ext` + `ClassCreate` + matrix assign — no .geo is
  ever loaded, so injected parts are **invisible transform nodes**.
- **40 (custom POV):** stores the node into craft state +0xF4, the same eyepoint slot
  filled from a class-40 model part (:294202). Effect = first-person camera origin and
  basis come from this transform (with `HoverCraft::UpdateEyepoint` applying its own math
  on top). Note: if the VGEO hierarchy *already* contains a class-40 part,
  `obj_find_class(EYEPOINT)` may resolve to that one instead depending on tree order —
  treat injected POVs as "when the model has none" or verify in-game.
- **Generic values with effect classes are the interesting case:** because flame(75) /
  smoke(76) / dust(77) emitters are behavior-driven rather than mesh-driven, an injected
  emitter-classed node should produce visible effects at an arbitrary authored spot with
  no VGEO entry. Smoke collection scans children for class 76 (:154472), so injected
  smoke sources qualify; flame has a nacelle-parent check on one of its paths (:193771)
  and dust is gated by a D3D option — both need in-game verification. Injected
  hardpoints are useless (weapon lookup is by name suffix, injected names are empty);
  an injected spinner(15) would rotate invisibly.

### 5.7 Part scale leaks straight into gameplay physics

Part transforms are consumed as **general 3×3 matrices — basis-vector lengths are never
renormalized**, so any scale baked into a hardpoint's record matrix (its own, or any
ancestor's) multiplies derived velocities:

- **Producer build throw-out — CONFIRMED.** `GetConstructionMatrix` (:216760) takes the
  GS1/GS2 special-hardpoint's world matrix verbatim (`obj_rel_parent_matrix(eject…)`,
  ancestors included); `FinishBuild` (:216866–876) then sets the new object's velocity to
  `25 × front_row(that matrix)` (`ScaleVector(local_70, 25.0, front)` → `SetVelocity`).
  A hardpoint scaled 2× along its front axis launches builds at ~50 u/s instead of 25.
- **Weapon fire — same mechanism.** `Cannon::Simulate` (:349640) composes the muzzle as a
  raw product of weapon/hardpoint transforms and passes it to `OrdnanceClass::Build`;
  projectile spawn derives position/direction from that matrix's rows. Non-uniform
  hardpoint scale therefore skews direction and scales exit speed/knockback exactly as
  modding reports describe. (Per-projectile formulas vary by class; the un-normalized
  matrix hand-off is the shared root cause.)
- Practical upshot: keep hardpoints/emitters/eject slots at unit scale unless deliberately
  tuning gameplay; the toolkit's "Raw transform scaling" experimental mode can produce
  these effects on purpose.

### 5.8 Recognized-but-stub chunks

`Process_VTFC/VCST/VCFC/WEPN/SPEC/VCHK/WGEO/GGEO/OGEO/WDFC/GDFC/SCHK` are literal
`return 1;` stubs — the loader traverses such chunks and discards their payloads.
Conversely **COLP is not a stub**: its 12 dwords are copied into the collision context
(+0xC) with an enable flag at +8 (:342760), matching the toolkit's collision-helper
model. `SPCS` has no dedicated processor in this build (payload ignored); the toolkit
already round-trips it under both spellings. SDF extras: `SOBJ` loads one inline .geo by
chunk id straight into the structure context (`Geom_Load`, context+100).

### 5.9 Exact name-magic rules

- **Hardpoints**: `FindHardpoint` matches **id chars 5–7** of the 8-char name,
  case-insensitive, recursively over the tree (:181894/:215829). The gc/gr/gm/gs
  convention is just the common case — any name ending in the searched triple works.
- **Turret rotators**: id char 7: `X`/`x` → yaw-slot array, `Y`/`y` → pitch slot, anything
  else traces `Unusual turret id "%.8s" … assuming Y` (:228891).
- **Parents**: records whose parent is `world` or `null` are rooted specially
  (:341631–635); a `null` parent plus all-zero rotation rows gets the identity matrix
  substituted (:341495–502).
- **Spinner byte aliasing**: see §5.2 — exact byte map of the dummy-helper trick.
- **Part scale → gameplay**: see §5.7 — hardpoint/ancestor scale multiplies producer
  throw speed (25 × front row) and skews weapon muzzle matrices.

### 5.10 Authored bounding volumes become authoritative with ObjectFlags bit 0x1

`SetObjBbox` (:128036) recomputes each part's `bBox`/`bSphere` from LOD0 vertices **only
when `(flags & 1) == 0`**. With bit 0x1 seeded from the record's ObjectFlags, the values
authored in the VDF/SDF record (`GeoCenter`, `SphereRadius`, `BoxHalfHeightX/Y/Z`) are
kept verbatim and never touched again. Those volumes then feed:

- collision broadphase (sphere tests use `bSphere.radius` directly, :127299),
- car-sphere setup for entity classes 1/3/4 (`SetCarSphere`),
- AI approach/aim sphere queries and target-sphere math,
- the bbox used by explosion/region logic.

So *record bounds + flags bit 0x1* = a deliberate hitbox/broadphase override lever.
Stock assets ship flags=0, i.e. always recomputed — nobody uses this.

### 5.11 The VGEO damage axis is loaded but dead in 1.5

The 28 VGEO bands per part are **7 damage states × 4 LODs**: the cache key is
`repNum = (damage << 16) | lod`; `GeoCache_SelectLOD` pokes the low half (rendering does
this continuously), while `GeoCache_SelectRep`/`ObjTree_SelectRep` — which poke the high
half — have **no callers** in the executable. Consequence: an author may supply up to 7
progressively damaged variants of each part and the loader will happily cache them, but
bzone.exe 1.5 never selects anything except damage state 0. (Caveat: a vtable-dispatched
caller would be invisible to this decomp; none was found.) The extra weapon-parented rep
at band index 3 (§3, class 50 row) is loaded under the same dead axis.

**Lineage:** this axis is an Interstate '76 inheritance — the engine is transparently
built on I'76 remains (`_OBJ76`, `File_Matrix_To_I76_Matrix`, `FindI76Instance`,
`I76FatalError`, `CheckForI76CD` all survive as PDB symbols), and I'76's cumulative
vehicle damage visuals map directly onto 7 damage reps × 4 LODs. Cross-check against the
Redux binary: the symbol-transfer table locates both functions in battlezone98redux.exe,
and in the best-effort decomp they form an isolated thunk cluster with no gameplay
caller — Rebellion didn't wire it back up either. Practical consequence: authoring
damage variants into bands 1–6 is harmless today and becomes a live feature the moment
anything calls `ObjTree_SelectRep(root, state)` — e.g. a small BZR-OpenShim hook mapping
healthRatio thresholds to damage states, or a future Rebellion patch.

### 5.12 Emitter collection can overflow — validation hazard

`Craft::FindSmokeSource` (:154463) appends **every** class-76 part found anywhere in the
hierarchy into a fixed `smokeList[8]` with an unchecked `smokeCount++`;
`Producer::FindSmokeSource` (:215847) does exactly the same for class-70 hardpoints.
A vehicle with more than 8 smoke emitters, or a producer with more than 8 weapon
hardpoints, writes part pointers past the array into adjacent craft-state fields —
silent memory corruption, crash, or weirder. Export validation should flag counts > 8.

### 5.13 Eyepoint transform drives the cockpit camera feel

`HoverCraft::UpdateEyepoint` (0049D8C7) reads the eyepoint node's transform basis rows
and multiplies them by 25.0 into the view-offset/shake integration. Position, rotation
*and scale* of the POV part therefore change first-person framing and head-bob response
(active only for the user craft with internal view + cockpit detail enabled). Combined
with §5.6's VLOC value-40 injector, both a model-authored POV and a chunk-injected POV
land in the same slot (+0xF4).

### 5.14 Collision hull composition

`Cgeom_Create` (00474CC6) builds collision polygons from **every face with
vertex_count > 2**, regardless of ShadeType/TextureType/XluscentType — there is no
material-based ghost-wall trick. Planes are stored negated (inward-facing convention);
degenerate faces (≤2 verts) are skipped from collision but still render. Vertex welding
dedupes identical positions (< 0.0001² distance).

## 6. Recommended toolkit updates (documentation-level; not implemented)

1. Relabel `33 LGT` and `34 RADAR` as *"not an engine class — renders as normal geometry"*
   (constants.py `insertgeotypedata` entries), or drop them from the picker.
2. Annotate defined-but-inert values (42 COM, 54, 61, 69): safe to set, but bzone.exe 1.5
   gives them no behavior beyond normal rendering.
3. Keep/expand the 1/3/6 crash warnings — now with the true cause (ODF-name remap NULL
   deref).
4. Note the SDF/VDF emitter discrepancy: emitter-classed parts (75–77) keep their geometry
   on structures (AddStructReps doesn't exclude them) but not on vehicles (AddReps does).
5. Type 81 hint can add: disk bounding sphere/center is preserved for this class.
6. Type 65 hint is validated by source (X/x yaw, Y/y pitch, else assume-Y with a trace
   warning).
7. Types 66/67/68 hints are engine-validated: rotors spin and fins roll with
   throttle/steering, nacelles pitch; a craft with nacelles but no flame parts gets flames
   auto-created under each nacelle (75).
8. **Fix the 8/9 labels**: they are the drivable-floor controls (§5.1), not inert.
9. Document spinner tuning via the SDF tail ints (§5.2) — including that values are
   revolutions/second per axis and that spinners stop when destroyed; flag the VDF-record
   caveat as untested.
10. Optional validation info: steep faces (>≈66° from horizontal) never become hover floor;
    scrounge/parking-lot parts keep their authored bounding spheres.
11. Potential future feature: a VLOC chunk writer (§5.6) — engine-verified injection of
    headlight masks, custom eyepoints or extra parts with no stock-file precedent. Needs
    in-game testing before exposure; at minimum document its existence for hex editors.
12. Validation could relax hardpoint-name checks: the engine only compares chars 5–7,
    case-insensitively (§5.9).
13. Document the scale caveat (§5.7) next to the raw-transform experimental mode: scaling
    hardpoints/eject slots changes throw speeds and muzzle behavior — feature, not bug,
    but it should be stated where users are invited to try it.
14. VLOC writer (§5.6): if pursued, value-38 gives day/night headlight masks without
    authoring hdlt_msk parts per LOD; value-40 gives model-free POV overrides; emitter
    classes give effect points without VGEO slots. All need in-game verification first.
15. **Validation: warn when a craft has >8 smoke-emitter (76) parts or a producer >8
    weapon-hardpoint (70) parts** — the engine's fixed `smokeList[8]` overflows
    silently beyond that (§5.12).
16. Document ObjectFlags bit 0x1 as "keep authored bounds" next to the existing
    ObjectFlags advanced field (§5.10) — it is the only way record GeoCenter/SphereRadius
    have any effect on non-terrain-special objects.

## 7. Source index

- `functions/0052/0052594e_NewObj.c` — Class remap (1/3/6), ClassCreate call, spinner seed
- `functions/004e/004e9b3d_ClassCreate.c`, `functions/004e/004e99a1_ClassIDtoIndex.c` —
  14-slot funk dispatch, index-0 fallback
- `functions/0048/00485d97_Craft_GetClass.c`, `functions/0041/0041a4e4` +
  entry 004998f6 `GameObjectClass::Find` — ODF-name lookup & NULL miss
- `functions/0052/00525b7d_AddReps.c` / `00525d8b_AddStructReps.c` — helper-class
  exclusion lists, GeoCache_AddRep gating
- `functions/0052/00525a99_LoadScrounge.c` — scrounge record built with Class=0xB
- Enum values: `BZR-OpenShim/reverse_engineering/badlands/legacy_bz1_exact_full/
  pdb_reference/llvm/publics.txt` (S_CONSTANT, OBJECT_CLASS_T)
- Consumer sites: see table in §3 (all_decompiled.c line numbers)
- `HoverCraft::HoverCraft` (0049CE34, :193408+) — nacelle/flame/dust part scan & auto-emitter creation
- `HoverCraft::UpdateGimbals` (0049D014 region, :193563+) — per-frame rotor/nacelle/fin gimbals + flame spin animation
- `Cache_Is_Moving_Obj` (004E7CA1, :286371) — moving-object class list + WEAPON_GEOMETRY exception
- HUD hardpoint ring icons: class range test 70–74 (:269713)
- Floor system: `Floor_InitEntity` (00475AF3, :130615), `CollectObjFloor` (00475A6E,
  :130596), `CollectFloorFaces` (004758B6, :130489; normal-y > 0.4 test)
- Spinner: `Spinner_SetRate` (004EE182), `Spinner_SetDDR` (004EE10A),
  `Spinner_Simulate` (004EE051); seed site in `NewObj` (:341520)
- `AddTerrainSpecial` (00521833, :337607) — bounds recompute skip for classes 11/81
- HELICOPTER/VEHICLE consumers: `IsCraft` (:11216), radar (:292411), dynamic_object
  (:292398), same-team damage cancel (:340862), explosion-class selection (:349024)
- `Process_VLOC_Chunk` (00526CBE, :343000 region) — part injection; stub processors
  VTFC/VCST/VCFC/WEPN/SPEC/VCHK/WGEO/GGEO/OGEO; COLP copy (:342760); SOBJ Geom_Load
- VDF/SDF table sizes: `ReadBWD2File(…, VDFChunkDefs, 0xe …)` :342663,
  `(…, SDFChunkDefs, 7 …)` :341322
- Stock chunk census: 99 VDFs → {VDFC,VGEO,EXIT}×99, {COLP,SPCS}×77, no VLOC/XGEO/WGEO;
  scanner script in temp workspace (`chunk_tags.py`)
- Bounds lifecycle: `SetObjBbox` (:128036, flags&1 gate), broadphase sphere use (:127299)
- Rep cache key: `GeoCache_SelectRep` (0049B0A3, damage<<16|lod), `GeoCache_SelectLOD`
  (0049B10A), dead `ObjTree_SelectRep` (0049B17A, no callers)
- Emitter overflow: `Craft::FindSmokeSource` (00485DD6), `Producer::FindSmokeSource`
  (004AA4FB) — unchecked `smokeList[count++]` vs fixed array of 8
- Eyepoint camera math: `HoverCraft::UpdateEyepoint` (0049D8C7, transform rows ×25)
- Collision hull: `Cgeom_Create` (00474CC6 — all faces >2 verts, negated planes)
