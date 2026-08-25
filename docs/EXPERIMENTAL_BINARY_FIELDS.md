# Experimental Binary Fields Reference

This note documents raw values currently exposed in the toolkit for advanced import/export workflows.

**Status update:** see `GEO_FLAGS_RESEARCH_REVIEW.md` for corrections to the
record layouts below, and `ADVANCED_AUTHORING.md` for the implemented
authoring/preservation layer. In particular: the ANIM element tail is preserved
verbatim regardless of interpretation; the VDF/SDF record order is name,
matrix, parent (`=8s12f8s7fii`); and the toolkit never rewrites unknown bits or
chunks it does not model.

Sources used:
- Commando950 addon docs
- Nielk1 `bz1-geo-editor` (`BZ1GeoEditor/Geo.cs`)
- Battlezone 98 Redux PDB reference in `Battlezone98Redux_Shim`
- Stock Redux assets in `Documents/Battlezone 98 Redux/Edit/stock` (1615 GEO, 98 VDF, 115 SDF scanned)
- Blake "Dummy" Robinson legacy VDF/SDF/GEO viewers and 3D Studio Max import/export plugins

## VDF / SDF ANIM

The PDB names the 64-byte ANIM payload as `tagANIMOBJ_HEADER`:

- `ANIM null2` maps to `animPtr`.
- `ANIM unknown2` maps to `meshPtr`.
- `ANIM reserved[0]` maps to `rotKeyPtr`.
- `ANIM reserved[1]` maps to `sclKeyPtr`.
- `ANIM reserved[2]` maps to `posKeyPtr`.
- `ANIM reserved[3]` maps to `obj`.
- `ANIM reserved[4]` maps to `entity`.

On disk, stock exported assets store these pointer-placeholder fields as zero. The toolkit preserves them for round-trip safety, but modders normally should not edit them.

### ANIM element layout — VERIFIED (2026-08 verification gate closed)

The 148-byte element layout is now established by **decompiled machine code in two independent binaries** (PDB treated as advisory naming only):

```
dword   0    int   animIndex      matched against the requested animation id
dwords  1-32 int   meshIndex[32]  NEVER read by either engine (advisory name)
dword  33    int   startFrame     active.curFrame init; float-cast
dword  34    int   frameCount     framesToDo; NEGATIVE = reverse playback
dword  35    int   loopCount      loopsToDo (<1 = infinite)
dword  36    float frameRate      playback rate
```

Evidence chain (names advisory; offsets are the load-bearing facts):
- Redux raw decomp `FUN_0062a270` (`Raw .C/`, unsymbolized) walks elements at stride `0x94` and reads only dwords `[0]`, `[0x21]`, `[0x22]`, `[0x23]`, `[0x24]` (= 33..36). Roles follow from usage: sign-flipped `[0x22]` branch, `loopsToDo`, `(float)` conversion of startFrame.
- bzone.exe 1.5 symbolized decomp (`functions/004e/004e731c_AnimObj_Start.c`) reads the identical offsets as `animIndex/frameRate/startFrame/frameCount/loopCount`.
- Header offsets agree across both binaries (`animPtr` walk at `+0x24`; `obj`/`entity` at `+0x38`/`+0x3c`).
- Stock data corroborates placement: 75 stock files / 374 elements reinterpret coherently under this layout - timing tuples like `(0,31,1,15.0)` and reversed clips `(30,-31,1,15.0)` land on dwords 33-36, while dwords 1-32 hold 0/1 values.
- `meshIndex[]`: zero references in both trees. Stock usage is 0/1 flags in contiguous prefixes (all-32-set being most common), supporting the historical "per-mesh-slot mask" reading. Name from PDB: ADVISORY.

Consequences for the toolkit mapping (all previously correct despite the research-doc dispute):

| Toolkit field | Element dword | Verified meaning |
|---|---:|---|
| `Index` | 0 | animIndex |
| `UnknownGeoMask[32]` | 1-32 | meshIndex[32] - unread by engines; preserve |
| `Start` | 33 | startFrame (negative length reverses via Length) |
| `Length` | 34 | frameCount (may be negative for reverse clips) |
| `Loop` | 35 | loopCount (<1 loops forever) |
| `Speed` | 36 | frameRate (float; stock: 10/15/50) |

The GEO_FLAGS_RESEARCH.md section 4 claim that frameRate/startFrame/frameCount/loopCount occupy dwords 1-4 was incorrect; see its addendum.

The PDB names `ANIMOrientation.unknown` as `tagANIMOBJ_MESH.flags`. Stock files scanned so far store `0`.

`UseTranslation2Track` writes Blender scale keys to the legacy `SCLKEY` slot (`tagANIMOBJ_SCLKEY`). The toolkit still uses the historical internal class name `ANIMTranslation2`, but engine symbols identify this as the scale-key track. A few stock assets use it for special motion.

## VDF SPCS / SCPS

Redux symbols contain an `SPCS` chunk tag and a `VDF_VEHICLE_SHELL_INFO` structure for shell UI slots, while older addon code called the preserved raw block `SCPS`. No stock VDF in the current scan contained this optional block. Treat the toolkit's `SPCS/SCPS Data` fields as compatibility preservation only until a real sample is found.

## VDF / SDF Object Records

The PDB names the VDF object record as `ObjectType` and the SDF object record as `StructObjectType`.

Confirmed names:

- `GEOFlags` is `ObjectFlags`.
- `SDFDDR` is `ddr`.
- The SDFC header field formerly labeled `Defensive` is also a raw integer DDR-style value.
- `SDFX`, `SDFY`, `SDFZ` are the `Target` vector.
- `SDFTime` is `Time`.

Stock scan notes:

- VDF `ObjectFlags` was `0` in all 1243 valid stock VDF object records.
- SDF `ObjectFlags` was `0` in all normal valid structure records seen in the scan.
- Stock SDF `ddr` is commonly `500000`; mine/powerup-like SDFs can use smaller values such as `200`.
- Stock type-15 structure spinner helpers commonly use `Target.y` as a small spin-speed value such as `0.1` or `0.2`.

## GEO Header (`=4si16siii`)

Toolkit fields:
- `GEOHeaderUnknown` = second int in header. This behaves like a legacy checksum/signature field in stock assets; preserve imported values for round-trip compatibility.
- `GEOHeaderUnknown2` = trailing int in header.

Stock scan notes:

- `GEOHeaderUnknown2` was `0` in all 1615 stock Redux GEO files scanned.

From `bz1-geo-editor`, the trailing int is treated as object `Flags` with these common bits:
- `0x001` Gouraud shaded
- `0x002` Tiled bitmap
- `0x004` Texture map
- `0x008` Parallel
- `0x010` True perspective
- `0x020` Wireframe
- `0x040` Transparent pixels
- `0x080` One-third translucent pixels
- `0x100` Project polygon only

## GEO Face (`=iiBBBffffi3s13sii`)

Toolkit per-face attrs (import/export):
- `bz_face_unknown_raw` (reserved int; stock scan found `0` on all faces)
- `bz_face_shade_type` (byte stored in int attr)
- `bz_face_texture_type` (byte stored in int attr)
- `bz_face_xluscent_type` (byte stored in int attr)
- `bz_face_parent` (int)
- `bz_face_node` (int)
- `bz_face_plane_x` (float)
- `bz_face_plane_y` (float)
- `bz_face_plane_z` (float)
- `bz_face_plane_d` (float)

Direct GEO export has an experimental `Face Plane Export` mode:
- `Current Toolkit Default` writes the older toolkit behavior: face center X/Y/Z and `D = 1.0`.
- `Preserve Imported` writes imported `bz_face_plane_x/y/z/d` attributes when present.
- `Recompute From Faces` writes a normalized face plane and distance from the exported GEO-coordinate vertices.
- `DX Normal Distance Fix` currently uses the same recompute path as a best-effort recreation of the old Max GEO exporter workaround. The exact 2010 exporter formula still needs sample comparison or deeper reverse engineering before it should be treated as a byte-identical recreation.

The old Max `GEOExport.dle` exposes a `DX normal distance fix` option. Its help text says the workaround addresses a Battlezone engine issue where triangles can disappear even though they should be visible. Ghidra analysis of that exporter shows it computes per-face plane data while writing faces, which is why the toolkit now preserves and can recompute these fields instead of always discarding them on import.

The unpacked legacy GEO viewer stores each face in a 216-byte in-memory record: 56 bytes for fixed face data plus room for ten 16-byte polygon-vertex records. The on-disk format stores a variable vertex count, but faces over 10 vertices are now flagged by validation because they can overflow or break older tools.

The PDB `FACE` structure confirms the three string-header bytes as:

- `ShadeType`
- `TextureType`
- `XluscentType`

The same structure has an in-memory `front_facet` pointer and `Test_Point`; the on-disk parent/node integers appear to be loader inputs for that face tree rather than arbitrary user data.

Stock scan notes:

- Most faces use bytes `04 01 00` (flat shaded, true perspective, not translucent).
- Gouraud faces use `05 01 00`.
- A small number use `04 03 00` (flat shaded plus true perspective and tiled texture bits).

Face enum values from `bz1-geo-editor`:

Shade type (`bz_face_shade_type`):
- `1` wireframe
- `2` solid wireframe
- `3` constant shaded
- `4` flat shaded
- `5` gouraud shaded

Texture type bits (`bz_face_texture_type`):
- `0x01` true perspective
- `0x02` tiled texturemap
- `0x04` transparent textmap

Xluscent type (`bz_face_xluscent_type`):
- `0` none
- `1` one-third
- `2` two-third

Node/tree branch (`bz_face_node`):
- commonly treated as `0` back, `1` front.

## Legacy VDF/SDF Viewer Rules

The legacy VDF/SDF viewer CHM documents several rules now mirrored in validation/tooltips:

- Type `40` is the POV/eyepoint; vehicles without an exportable POV can crash Battlezone when ejecting.
- Body/root GEOs should resolve to parent `WORLD`.
- VDF children must appear after parents in the object table; the toolkit exporter already orders object records from the Blender hierarchy.
- Common naming suffixes include `bd*` body chunks, `pov`, `gc*` cannon hardpoints, `gr*` rocket hardpoints, `gm*` mortar hardpoints, `gs*` special hardpoints, `tx*` pitch rotators, and `ty*` yaw rotators.

## Safety

- Always keep backups.
- Change one field at a time and test in-game.
- Some values are engine- or asset-specific and may be ignored.
