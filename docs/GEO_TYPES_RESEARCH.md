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
| 8 BRIDGE | Structure-like entity collision 0x3000; no unique part behavior beyond rendering. | :129206 |
| 9 FLOOR | Floor faces collected for hover-height even when default collection is off (`class_id == CLASS_ID_FLOOR` condition in CollectFloorFaces call). | :130599 |
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

## 5. Recommended toolkit updates (documentation-level; not implemented)

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

## 6. Source index

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
