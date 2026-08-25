# GEO Flags Research — Engine Archaeology Report

**Work order:** Determine what the legacy Battlezone "GEO flags" fields mean, whether the
engine consumes them, and how the Blender toolkit should treat them.

**Primary authority:** Battlezone 1.5 `bzone.exe` decompilation with real `bzint.pdb`
symbols (`GIT/BZ1_Source/1.5`, 15,070 named functions). Secondary: retail asset corpus,
Redux decompile, Nielk1's `bz1-geo-editor`, toolkit sources.

**Investigated 2026-08-24. All line numbers verified against the trees cited.**

> **Correction gate:** read alongside `GEO_FLAGS_RESEARCH_REVIEW.md`, which fixes
> the 100-byte record layout below (name @0x00, matrix @0x08, parent @0x38), the
> 13-byte GEO texture-name field, and the ANIM tail interpretation. The
> implemented toolkit follows the corrected layouts and preserves all tail
> bytes verbatim (`docs/ADVANCED_AUTHORING.md`).

---

## 0. Executive summary

There are three unrelated legacy fields that have been called "GEO flags." They must not
be conflated:

| Field | Where | 1.5 engine verdict | Confidence |
|---|---|---|---|
| **A. GEO header trailing int** (`@0x20`) | `.GEO` file header | **Never read. Skipped by a hardcoded offset. Dead format member.** | CONFIRMED |
| **B. VDF/SDF object final int** (`ObjectFlags`) | last int of each 100-byte object record | **Read, stored into `_OBJ76.flags`, heavily branched on at runtime** (death/collision/team/light/bbox bits). Disk value is only an initial seed; stock assets ship 0. | CONFIRMED |
| **C. ANIM element dwords 1–32** (`unknowngeoflag`) | each 148-byte `tagANIMOBJ_ANIM` element | **Toolkit's interpretation is wrong.** Dwords 1–4 are actually `frameRate/startFrame/frameCount/loopCount` (consumed by `AnimObj_Start`). Remaining tail is unread. No slot mask is consumed anywhere. | CONFIRMED (engine read), UNKNOWN (tail semantics) |

Headline answers:

> **A)** The `.GEO` header flag integer is a surviving legacy renderer mode word that
> Battlezone 1.5 does not even read off disk. All meaningful rendering behavior comes from
> per-face `ShadeType` / `TextureType` / `XluscentType`. Nielk1's bit names describe a
> pre-per-face era of the format; nothing in the shipped engine validates or uses them.
>
> **B)** `GEOFlags` in VDF/SDF records is *not* a GEO/rendering flag. It seeds the engine
> object-system state bitfield `_OBJ76.flags` (`StructObjectType.ObjectFlags` per PDB).
> It is genuinely consumed — but as collision-class/team/death-style state bits, not render
> modes.
>
> **C)** The toolkit's ANIM export heuristic writes `[1,1,1,1,…]` into what the engine
> parses as `frameRate=1, startFrame=1, frameCount=1, loopCount=1`. It happens to produce a
> valid (if degenerate) one-shot animation, but the "slot mask" model behind it does not
> exist in the 1.5 executable.

Empirical bit-flip testing (Phase 9) was **not needed**: source analysis fully resolved
fields A and B, and field C was resolved by code plus stock-data cross-checks.

---

## 1. Exact binary locations of the three fields

### A. GEO header trailing integer

```
offset 0x00  int   '.GEO' tag (bytes on disk: "OEG.", LE dword 0x2E47454F)
offset 0x04  int   checksum-ish second int (varies per file; never validated by engine)
offset 0x08  char  name[16]
offset 0x18  int   vertex count
offset 0x1C  int   face-group ("polygon") count
offset 0x20  int   << RESEARCH TARGET A >>
offset 0x24  ...   vertex positions (vert_count * 12 bytes)
```

Toolkit today: bz98tools `GEOHeader.Unknown2` / RNA `GEOHeaderUnknown2`
(`geo_classes.py:43`, unpacked via `"=4si16siii"` at `import_geo.py:996`);
bzrmodelporter `Geo.flags` (`bzgeo.py:68`, read/written at `bzgeo_serializer.py:31,87`).

### B. VDF/SDF object record trailing integer

100-byte record (VDF `ObjectType`, SDF `StructObjectType`; SDF extends to 120 bytes with
extra tail ints):

```
offset 0x00  char[8]  GeometryFile/name
offset 0x08  char[8]  Parent
offset 0x10  MAT_3D_FILE transform matrix (48 bytes)
offset 0x40  floats   SphereRadius, GeoCenter, BoxHalfHeightX/Y/Z
offset 0x5C  int      Class/type          <- toolkit GEOData.type  (@92)
offset 0x60  int      << RESEARCH TARGET B: ObjectFlags >>   (@96)
[SDF only:   int ddr @100, float target xyz/time @104..119]
```

Toolkit today: `GEOData.geoflags` (`vdf_classes.py:126,140`, `sdf_classes.py:127,141`),
RNA `GEOFlags` ("Object Flags", `__init__.py:1434`); porter `GeoObject.object_flags`
(`bzbwd2.py:252`, `bzbwd2_serializer.py:367,449`).

### C. ANIM element (148 bytes, stride 0x94)

See §6 for the corrected layout. Toolkit today: `ANIMElement.unknowngeoflag[32]`
(`vdf_classes.py:228–267`, `sdf_classes.py:266–305`, struct `"=i32iiiif"`),
RNA `UnknownGeoMask`; porter `Animation.mesh_index_list` (`bzbwd2.py:396`).

---

## 2. Field A — GEO header flags: full trace

### 2.1 The loader never reads it

The sole function in bzone.exe 1.5 that checks the `.GEO` magic is
`load_geometry` (`0x0049B56D`, `functions/0049/0049b56d_load_geometry.c`;
merged source `all_decompiled.c:191924`). Complete header interaction:

```c
if (*(int *)param_1 == 0x2e47454f) {                      // :191984 — ".GEO" tag @0x00
    local_1c = param_1 + *(int *)(param_1 + 0x18)*0xc + 0x24;   // normal table base
    ...
    iVar6 = *(int *)(param_1 + 0x1c);                     // face-group count @0x1C
...
    p_Var5->vertex_count = *(long *)(param_1 + 0x18);     // vertex count @0x18
...
    local_10 = (float *)(param_1 + 0x2c);                 // first vertex = floats @0x24,0x28,0x2C
```

Offset `0x24` appears as a **hardcoded constant**; the dword at `0x20` is jumped over
without ever being loaded. Likewise the checksum at `0x04` is never validated.
The parsed-out runtime `_GEOMETRY` struct (`new_geometry`/`load_geometry`,
`functions/0049/0049b352_new_geometry.c:18`, loader lines :91–98) contains only
`cache_info, vertex_count, normal_count, vertex_table, normal_table, faces` — **no flags
member exists to receive it.**

The wrapper `Geom_Load` (`0x0049BE95`, `functions/0049/0049be95_Geom_Load.c:68–75`)
resolves `geo.<name>` via `MakePrjFile`, reads the file with `zixReadFile`, calls
`load_geometry`, then immediately `zixFree`s the raw buffer. No pre/post-processing touches
the header. Every consumer of GEO data funnels through this path (VGEO chunks →
`GeoCache_AddRep` → `Geom_Load`; scrounge path likewise).

**Verdict: READ=no (skipped), STORED=no, TESTED=no. The field is dead weight in the disk
format as shipped.** Confidence: CONFIRMED.

### 2.2 What actually drives rendering: per-face fields

Face records are parsed at stride `vertex_count*0x10 + 0x37`
(`load_geometry.c:161–176, 315`):

```
+0x04 int   vertex_count
+0x08 byte  color R,G,B        (+8,+9,+10)
+0x1F byte  ShadeType
+0x20 byte  TextureType
+0x21 byte  XluscentType
+0x22 char  texture_name[15]   (lowercased; ".tmt"/".map" forced)
+0x31 ..    per-vertex nodes {int vertIdx, int normIdx, float u, float v} ×16B
```

Runtime consumers (PDB-named):

- `Cache_Make_Poly` (`functions/004e/004e80f1_Cache_Make_Poly.c:64–71`):
  ```c
  if (param_2->ShadeType == '\0') { param_2->ShadeType = '\x04'; }   // default = flat shaded
  bVar2 = sniped_by[((uint)param_2->TextureType + (uint)param_2->ShadeType * 8) * 4 + 0xf8];
  *(byte *)((int)&pBVar5->Bsp_Type + 3) = bVar2;
  if (param_2->XluscentType != '\0') { *(byte *)((int)&pBVar5->Bsp_Type + 3) = bVar2 | 0x80; }
  ```
  i.e. rendering mode is a pure function of the **per-face** `(ShadeType, TextureType)`
  pair through the `sniped_by` lookup table, with translucency OR'd in as `0x80`.
  Same indexing used by `Submit_Scrounge_Objects` (`004ed8a7`, line 142).
- Collision geometry extraction (`Cgeom_Create`, `CollectFloorFaces`) consumes
  `_GEOMETRY` faces without consulting any header word.

Confidence: CONFIRMED.

### 2.3 The historical bit names vs the engine

Nielk1 `bz1-geo-editor` `Geo.cs` defines, on the header dword (field declared
`public long Flags; // Object Flags`):

| Bit | Historical name | Present in 1.5 source? | Read from GEO header? | Runtime effect |
|---|---|---|---|---|
| 0x001 | `_GOURAUD_SHADED` | no | no | none |
| 0x002 | `_TILED_BITMAP` | no | no | none |
| 0x004 | `_TEXTURE_MAP` | no | no | none |
| 0x008 | `_PARALLEL` | no | no | none |
| 0x010 | `_TRUE_PERSPECTIVE` | no | no | none |
| 0x020 | `_WIRE_FRAME` | no | no | none |
| 0x040 | `_TRANSPARENT_PIXELS` | no | no | none |
| 0x080 | `_ONE_THIRD_TRANSLUCENT_PIXELS` | no | no | none |
| 0x100 | `_PROJECT_POLYGON_ONLY` | no | no | none |

Searched across `all_decompiled.c` and `function_index.tsv`: zero occurrences of
`PROJECT_POLYGON*`, `POLYGON_ONLY`, `GOURAUD`, `TILED_BITMAP`, `TRUE_PERSPECTIVE`,
`WIRE_FRAME` as identifiers. The only related PDB names anywhere are the **per-face**
members `FACE.ShadeType`, `FACE.TextureType`, `FACE.XluscentType` (loader +
`Cache_Make_Poly` + scrounge path). Verdict per bit: **defined only in tooling; not
supported by any evidence in the shipped engine.**

### 2.4 The semantic-overlap hypothesis (Phase 4 question)

The overlap between header bits and face enums is real and systematic:

| Header bit | Nearest per-face equivalent |
|---|---|
| `_GOURAUD_SHADED` 0x001 | ShadeType 5 (Gouraud) |
| `_TILED_BITMAP` 0x002 | TextureType 0x02 (tiled texturemap) |
| `_TEXTURE_MAP` 0x004 | presence of texture_name / TextureType ≠ 0 |
| `_PARALLEL` 0x008 | TextureType perspective-vs-parallel mapping mode |
| `_TRUE_PERSPECTIVE` 0x010 | TextureType 0x01 (true perspective) |
| `_WIRE_FRAME` 0x020 | ShadeType 1/2 (wireframe, solid wireframe) |
| `_TRANSPARENT_PIXELS` 0x040 | TextureType 0x04 (transparent textmap) |
| `_ONE_THIRD_TRANSLUCENT` 0x080 | XluscentType 1 (one-third) |
| composite `_TWO_THIRD` = WIRE\|THIRD | ShadeType wireframe + Xluscent two-thirds combo |

This is exactly what one expects if the earliest GEO format carried a single object-wide
renderer-mode word that was later superseded by per-face material fields while the header
dword was retained for layout compatibility. The engine-side mechanism that replaced it
(the `sniped_by[(TextureType + ShadeType*8)]` mode table, §2.2) still exists in 1.5.

Verdict: **legacy pre-per-face renderer format member, retained in layout, ignored by the
shipped engine.** The "retained-but-dead" outcome is CONFIRMED; the precise history
(when the per-face fields appeared) is INFERRED — no original Activision source is
available locally to pin the era.

---

## 3. Field B — VDF/SDF `ObjectFlags`: full trace

### 3.1 Read path

Chunk handlers `Process_SGEO_Chunk` (SDF structures, `0x52611B`) and
`Process_XGEO_Chunk` (VDF objects, `0x5263F0`) walk fixed-stride records via
`AddStructReps` / `AddReps` (`0x525D8B` / `0x525B7D`, stride hardcoded `local_3c += 100`
at `all_decompiled.c:341680`). Each record goes whole into:

```c
_OBJ76 * NewObj(_OBJ76 *param_1, StructObjectType *param_2, ...)
{
    p_Var4 = create_obj_ext(param_1, param_3);
    File_Matrix_To_I76_Matrix(&p_Var4->transform, &param_2->TransformMatrix);
    p_Var4->flags = param_2->ObjectFlags;        // all_decompiled.c:341487 — full 32-bit copy, no mask
```

PDB member names `StructObjectType.ObjectFlags` / `ObjectType` come straight from
`bzint.pdb`; the Redux decompile shows the identical instruction
(`FUN_00825650`: `*(undefined4 *)(iVar1 + 0x14) = *(undefined4 *)(param_2 + 0x60);`,
symbol-transfer CSV row 25951 maps it to `NewObj`).

### 3.2 Runtime consumers of `_OBJ76.flags` (+0x14)

All verified in merged source:

| Bits | Meaning | Evidence |
|---|---|---|
| 0x0001 | bbox/LOD selection gate (`SetObjBbox` skips `GeoCache_SelectLOD` when clear) | :128042 |
| 0x0010 | view/render-related test in `AnimSprite::Render` | :137668 |
| 0x0200 | destroyed flag (`IsAlive` requires clear; `IsObjDestroyed` tests set; threat queries skip flagged objects) | :26165, :125027, :102023, :16312 |
| 0x0800 | light-source attached (set at runtime by LOBJ chunk handler; consumed by `Building::Explode`) | :342247, :150749 |
| 0xF000 | collision class nibble (`obj_get_collision`, `obj_set_collision`, `ClearObjCollision`, `CarRecurse`, `PointRecurse`) | :126196, :128250, :128948, :127488, :130986 |
| 16–19 | team id (`get_obj_team` / `set_obj_team`: `*(ushort*)(&flags+2) & 0xf`) | :150324, :181767 |
| — | exposed to game scripts/logic via `GameObject::GetFlags` | :10368 |

Most of these bits are also **set/cleared dynamically during play** (death sets 0x200,
LOBJ ORs 0x800, mission code assigns team). The disk value therefore acts as an
*initial seed* applied at object creation, before game logic mutates the rest.

**Verdict: READ=yes, STORED=yes (raw copy), TESTED=yes (extensively), but as an
object-system state bitfield — not a rendering flag, and not a "GEO" anything.**
Confidence: CONFIRMED (mechanism); the practical effect of nonzero disk values in stock
gameplay is INFERRED (no stock content exercises it — every scanned record is 0).

`Process_WGEO/GGEO/OGEO_Chunk` are literal `return 1;` stubs — their payloads (including
any flag-like fields) are entirely ignored (:342818, :342878, :342902).

---

## 4. Field C — ANIM element: corrected layout

### 4.1 What the engine says

Element table stride `0x94` = 148 bytes (`AnimObj_Add`, `functions/004e/004e71aa_AnimObj_Add.c:20,33,36`).
`AnimObj_Start` (`functions/004e/004e731c_AnimObj_Start.c:41–67`) reads, via **PDB-named
struct members**:

```c
ptVar3->animIndex    // +0x00
ptVar3->frameRate    // +0x04
ptVar3->startFrame   // +0x08
ptVar3->frameCount   // +0x0C
ptVar3->loopCount    // +0x10
```

These are the **only** members of `tagANIMOBJ_ANIM` referenced anywhere in bzone.exe 1.5
(grep over merged source: sole consumer `AnimObj_Start`; `AnimObj_Simulate` operates on the
separate active-list entries). The remaining 32 dwords (+0x14…+0x93) are copied verbatim
and never looked at.

Mesh-slot selection during playback does **not** use the element tail at all:
`TraverseObjTree` (`functions/004e/004e78f5_TraverseObjTree.c`, :286042+) matches
`tagANIMOBJ_MESH` records (stride 0x84) by 8-byte object-id against `_OBJ76.id`, then
`AnimateMeshTransform` (`functions/004e/004e7747_AnimateMeshTransform.c:42–121`) pulls
static matrix (+0x3C…0x5C), static scale (+0x60…0x68), rotation-key start/count
(+0x6C/+0x70) and scale-key start/count (+0x7C/+0x80) **from the MESH record**, sampling
key tables via the anim header's `rotKeyPtr`/`sclKeyPtr`.

Adjacent confirmed quirk: `AnimObj_Add:25–27` only registers an ANIM block if
`rotKeyCount != 0 || scaleKeyCount != 0 || meshCount*elemCount < posKeyCount` — an ANIM
chunk carrying only static orientations and no key tables is silently dropped by 1.5.

### 4.2 Stock-data cross-check (retail SDFs, `Battlezone_Install/stock/*.sdf`)

First elements dump as `[idx, 1, start, count, loop, …]`, e.g. `abcomm.sdf` elem0 =
`0,1,1,1,1,1,1,0…0` — perfectly coherent as rate=1 fps, startFrame=1, frameCount=1,
loopCount=1 (+ poskey idx/count), and incoherent as a "slots 0–5 selected" mask followed by
a mask of zeros.

### 4.3 Consequence for the toolkit

The legacy parse `"=i32iiiif"` maps:

| Toolkit name | Dwords | Actual 1.5 meaning |
|---|---|---|
| `index` | 0 | `animIndex` ✔ |
| `unknowngeoflag[0]` | 1 | **`frameRate`** |
| `unknowngeoflag[1]` | 2 | **`startFrame`** |
| `unknowngeoflag[2]` | 3 | **`frameCount`** |
| `unknowngeoflag[3]` | 4 | **`loopCount`** |
| `unknowngeoflag[4..31]` | 5–32 | unread tail |
| `start`,`length`,`loop`,`speed` | 33–36 | unread tail |

So the export heuristic (`export_vdf.py:526–541`, `export_sdf.py:357–372`):
`if item.Index in [0,1]: newelement.unknowngeoflag = [1]*32 else [0]*32`
writes `frameRate=1, startFrame=1, frameCount=1, loopCount=1` — a degenerate but valid
one-shot animation — and zeros for everything else. Round-trips of stock files are
byte-safe because everything is preserved verbatim; only *authored* values built from the
mask model encode wrong semantics.

Where did `meshIndex[32]` come from? Most plausibly the **mismatching** Redux PDB labeling
the opaque dword array region (see `EXPERIMENTAL_BINARY_FIELDS.md:26`). Nothing in 1.5
corroborates a per-element slot mask; slot targeting lives in the MESH table keyed by
object ID. Tail semantics beyond "unread": UNKNOWN.

---

## 5. Stock-asset statistics

| Corpus | Field | Result |
|---|---|---|
| Retail install tree `Battlezone_Install/**/*.geo` (**1,614 files**, scanned for this report) | header int @0x20 | **0 in all 1,614** |
| Redux stock scan (prior work, `EXPERIMENTAL_BINARY_FIELDS.md:63`) | header int @0x20 | 0 in all 1,615 |
| Same 1,614 retail files | checksum int @0x04 | varies widely (54×201, 53×130, 142×16, 59×15, 69×14, …) — real per-file data, never validated by engine |
| 40-file/546-face subset | face ShadeType / TextureType / Xluscent | {4 flat}, {0x01 true-perspective}, {0} — parse walked cleanly at engine stride `n*0x10+0x37`, independently validating the layout |
| VDF/SDF stock scans (prior work) | ObjectFlags | 0 in 1,243 valid VDF records; 0 in normal SDF records |

Zero-values alone don't prove non-use — which is why §2–§4 trace code, not just corpora.
Both lines of evidence now agree.

---

## 6. Comparison with Redux / PDB evidence

- Redux engine still parses the identical GEO format: `BZ1_Source/Redux/Raw .C/FUN_004e39b0`
  checks the same magic (:163), same counts (:167), copies face `ShadeType/TextureType/
  XluscentType` from record offsets 0x1F/0x20/0x21 (:299–305). No header-flag consumption
  appears there either.
- Redux `NewObj` equivalent seeds `obj+0x14` from record+0x60 (`FUN_00825650:17`) — same
  ObjectFlags behavior preserved.
- The "meshIndex[32]" naming traces only to the mismatching Redux PDB reference noted in
  the toolkit docs; 1.5 symbols contradict the mask interpretation (§4).
- No `GEODATA` symbol exists in any local symbol corpus (it's a toolkit-side class name).

## 7. 1.4 vs 1.5 (Phase 7)

A direct 1.4↔1.5 code diff of the GEO loader was **not possible**: the local
`decomp1.4` tree contains only AI-heuristic renamed functions and does not include the
geometry loader (no magic-constant hit). Available comparisons instead:

- 1.5 loader ignores header flags/checksum outright (§2.1);
- Redux build (post-1.5 lineage) parses identically and equally ignores them (§6);
- Nielk1's editor targets classic 1.x files and exposes header Flags purely as inert
  checkboxes its own renderer never reads.

Everything consistent with: the field was already vestigial by the 1.4/1.5 patch era.
Confidence: STRONG for 1.5/Redux behavior, INFERRED for exact 1.4 parity.

## 8. Empirical validation (Phase 9)

Not performed — unnecessary. Source analysis resolved fields A (never read) and B (read,
seeded, consumed) completely; field C was settled by PDB-named member access plus stock
byte patterns. Controlled bit-flip GEOs would only re-demonstrate "no observable
difference" for field A.

---

## 9. Recommended toolkit treatment (Phase 10)

| Field | Category | Specific guidance |
|---|---|---|
| GEO header int @0x20 | **E — Deprecated legacy field** (preserve exactly) | Keep round-tripping raw. Do **not** expose Gouraud/tiled/perspective/PROJECT_POLYGON_ONLY checkboxes: they have zero engine effect in BZ 1.5 and Redux, and would imply false semantics. Default 0 for new assets (already the case). |
| Naming for @0x20 | keep neutral | Do **not** rename to `GEOFlags`/`RenderFlags`/`ObjectFlags` — none are source-backed (the 1.5 binary contains no named GEO-header struct; PDB covers runtime types only). `Unknown2`/"reserved raw" is closer to the truth than the porter's semantic-sounding `Geo.flags`. |
| GEO checksum @0x04 | F — preserve exactly | Never validated by engine (§2.1). Toolkit default 69 matches few stock files; harmless, but arbitrary. |
| VDF/SDF `geoflags` | **B/C — Advanced/experimental; preserve imported raw; force 0 on new** | Rename display semantics: it is `StructObjectType.ObjectFlags` seeding `_OBJ76.flags` (collision-class nibble 0xF000, team bits 16–19, death 0x200, light 0x800, bbox 0x1, view 0x10). It is *consumed*, so preserve user values, but warn that nonzero values pre-seed live state bits stock content never uses. Consider removing the main-panel exposure at `__init__.py:3043` (keep advanced panel). |
| ANIM element dwords | **rename semantics** | Stop presenting dwords 1–32 as a slot mask. Minimum: relabel dwords 1–4 as `frameRate/startFrame/frameCount/loopCount` (CONFIRMED engine fields) and treat 5–36 as unknown-reserved (preserve). Re-evaluate the `[1]*32` authoring heuristic — under engine semantics it means "rate 1 fps, start 1, count 1, loop 1". |
| ANIMOrientation.unknown | unchanged | `tagANIMOBJ_MESH.flags`, stock 0 — out of scope, prior doc stands. |

## 10. Incorrect names currently in the toolkit

1. `bzrmodelporter.bzgeo.Geo.flags` — implies consumed semantics; field is engine-ignored. Misleading.
2. `bz98tools` RNA description "GEO Header Flags" (`__init__.py:1545`) — implies render-flag semantics; engine never reads it.
3. `GEOData.geoflags` / RNA label grouping under GEO panels — the field is `ObjectFlags`, an object-system state seed, not a GEO property.
4. `ANIMElement.unknowngeoflag` / `UnknownGeoMask` / "mesh slot mask" wording — contradicts engine layout; dwords 1–4 are animation parameters.
5. Docs claim "stock files use it as a 0/1 per-mesh-slot mask" (`EXPERIMENTAL_BINARY_FIELDS.md:26`) — should be corrected to the §4 layout.

## 11. Proposed follow-ups (documented only; not implemented here)

Trivial/safe:
- Update `EXPERIMENTAL_BINARY_FIELDS.md` sections for A/B/C with this report's findings.
- Reword the three RNA descriptions above; optionally rename `Geo.flags` → `header_reserved_int` (porter-internal, no file impact).
- Move `GEOFlags` out of the main GEO panel.

Larger (needs design):
- ANIM editor UI rebuild around the real element layout (rate/start/count/loop + reserved tail), including whether to keep byte-exact preservation for the tail.
- Optional validation warning when exporting nonzero VDF/SDF ObjectFlags (advanced users only).
- Optional: verify empirically whether transform-only ANIM chunks (no rot/scale keys) are dropped by the engine as `AnimObj_Add:25–27` implies, and whether the toolkit needs a "dummy key table" workaround.

## 13. Addendum (verification gate closed): ANIM element layout corrected

Section 4 above states that `frameRate/startFrame/frameCount/loopCount` occupy
element dwords 1-4. That placement was **wrong**; it came from reading the
symbolized 1.5 decomp without cross-checking raw offsets. The verified layout
(PDB advisory-only; established from unsymbolized Redux decomp offsets,
matching symbolized 1.5 offsets, header-offset agreement across both binaries,
and coherent stock timing values on dwords 33-36) is:

```
dword   0   animIndex
dword 1-32 meshIndex[32]   <- the toolkit's unknowngeoflag[]; unread by both engines
dword  33   startFrame
dword  34   frameCount      negative = reverse playback
dword  35   loopCount
dword  36   frameRate (float)
```

The original toolkit field mapping (`index`, `unknowngeoflag[32]`,
`start`, `length`, `loop`, `speed`) is therefore exactly right. Section 9's
guidance row for "ANIM element dwords" should be read with this correction:
the export heuristic `[1]*32` writes a full mesh-slot mask, not animation
timing. Full evidence chain in `EXPERIMENTAL_BINARY_FIELDS.md` ("ANIM element
layout - VERIFIED").

## 12. Source reference index

1.5 tree (`GIT/BZ1_Source/1.5`; paths relative, `ADC:` = `all_decompiled.c`):

- `functions/0049/0049b56d_load_geometry.c` — GEO parser (tag :70; counts :71,:73,:92; verts @0x24 :102; faces :159–176,:315)
- `functions/0049/0049be95_Geom_Load.c` — cache/load wrapper (:68–75)
- `functions/0049/0049b352_new_geometry.c` — runtime `_GEOMETRY` alloc (no flags member)
- `functions/004e/004e80f1_Cache_Make_Poly.c:64–71` — per-face render-mode mapping (`sniped_by`)
- `functions/0052/0052594e_NewObj.c:25` / ADC:341487 — ObjectFlags → `_OBJ76.flags`
- `functions/0052/00525b7d_AddReps.c` / `00525d8b_AddStructReps.c` — 100-byte record walk (stride :341680)
- `functions/004e/004e71aa_AnimObj_Add.c` — ANIM strides/guard (:20–27), sub-table pointers (:35–40)
- `functions/004e/004e731c_AnimObj_Start.c:41–67` — sole `tagANIMOBJ_ANIM` consumer
- `functions/004e/004e7747_AnimateMeshTransform.c:42–121` — MESH-record key indices/static transforms
- `functions/004e/004e78f5_TraverseObjTree.c` — id-keyed mesh matching
- Consumers of `_OBJ76.flags`: ADC :10368, :26165, :125027, :126196, :128250, :128948, :127488, :130986, :150324, :181767, :137668, :150749, :128042, :342247

Secondary: `BZ1_Source/Redux/Raw .C/FUN_004e39b0-004e39b0.c` (:163,:167,:299–305);
BZR-OpenShim corpora `FUN_00825650:17` + `legacy_symbol_enriched_functions.csv:25951`;
Nielk1/bz1-geo-editor `BZ1GeoEditor/Geo.cs` (header consts & composites; face enums;
checkbox-only usage in `SharpGLForm.cs`);
toolkit: `geo_classes.py:43`, `import_geo.py:996,1045`, `export_geo.py:341–358`,
`bzgeo.py:68`, `bzgeo_serializer.py:31,87`, `vdf_classes.py:126,139–140,228–267`,
`sdf_classes.py:127,141,266–305`, `import_vdf.py:425`, `export_vdf.py:423–424,526–541`,
`import_sdf.py:254`, `export_sdf.py:267–268,357–372`, `bzbwd2.py:252,396`,
`bzbwd2_serializer.py:367,449,96,173`, `__init__.py:1434–1440,1544–1547,3043,3093,3398–3399`,
`docs/EXPERIMENTAL_BINARY_FIELDS.md`.

Retail corpus scan scripts: temp workspace `anim_dump.py`, `geo_scan.py` (methodology in §5).
