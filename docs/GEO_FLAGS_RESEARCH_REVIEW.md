# Review Corrections — GEO Flags Research

This note is a correction gate for `docs/GEO_FLAGS_RESEARCH.md` before any exporter/UI implementation is changed.

The core conclusion of the research remains useful: the legacy `.GEO` header dword at `+0x20` is not consumed by the observed Battlezone 1.5 or Redux GEO loader paths, while per-face `ShadeType`, `TextureType`, and `XluscentType` are the meaningful legacy face-rendering inputs. The VDF/SDF trailing object-record integer is a separate `ObjectFlags` field and must not be conflated with the GEO header field.

Before treating the research report as canonical, correct the following items.

## 1. Correct the 100-byte VDF/SDF object-record layout

The report currently reverses the parent field and transform matrix.

The toolkit parser `=8s12f8s7fii` establishes the record order as:

```text
0x00  char[8]  GeometryFile / name
0x08  float[12] transform matrix       (48 bytes)
0x38  char[8]  Parent
0x40  float[3] GeoCenter
0x4C  float    SphereRadius
0x50  float[3] BoxHalfHeight
0x5C  int      Class / type
0x60  int      ObjectFlags
```

For SDF structure records, the format then continues with the structure-specific tail.

`ObjectFlags @ 0x60` remains correct; only the parent/matrix placement in the report needs correction.

## 2. Correct the GEO face fixed layout

The report currently states `texture_name[15]`. The serializer reads/writes 13 bytes.

The fixed face record is:

```text
+0x00  int      index
+0x04  int      vertex_count
+0x08  byte[3]  color
+0x0B  float[3] surface normal
+0x17  float    plane distance
+0x1B  float    polygon area
+0x1F  byte     ShadeType
+0x20  byte     TextureType
+0x21  byte     XluscentType
+0x22  char[13] texture_name
+0x2F  int      ParentFace
+0x33  int      TreeBranch
+0x37           FaceNode[]
```

This is consistent with the engine/toolkit stride `vertex_count * 0x10 + 0x37`.

## 3. Reopen the ANIM tail conclusion before changing code

The research correctly identifies the first five dwords of a 148-byte `tagANIMOBJ_ANIM` record as:

```text
+0x00  animIndex
+0x04  frameRate
+0x08  startFrame
+0x0C  frameCount
+0x10  loopCount
```

However, the remaining record size is exactly 32 dwords:

```text
148 bytes total
- 20 bytes for the five fields above
= 128 bytes
= 32 dwords
```

That lines up exactly with the prior Redux PDB-derived name `meshIndex[32]`.

Therefore, do **not** yet replace the tail with generic `unknown[32]` semantics or remove all mesh-slot terminology.

The likely structure that must be verified is:

```c
struct tagANIMOBJ_ANIM
{
    int animIndex;
    int frameRate;
    int startFrame;
    int frameCount;
    int loopCount;
    int meshIndex[32];
};
```

The Battlezone 1.5 playback code apparently does not consult those 32 tail dwords directly, but that only proves they are unused by the observed 1.5 consumer. It does not invalidate a PDB-backed member name, and Redux may differ.

### Important consequence for the current toolkit parser

The legacy parser is:

```text
=i32iiiif
```

So the current fields actually span the 148 bytes as:

```text
index                         -> dword 0  -> animIndex
unknowngeoflag[0..3]          -> dwords 1..4 -> frameRate/startFrame/frameCount/loopCount
unknowngeoflag[4..31]         -> dwords 5..32 -> possible meshIndex[0..27]
start/length/loop/speed       -> dwords 33..36 -> possible meshIndex[28..31]
```

In other words, the current toolkit likely preserves all 32 tail dwords across round trips, but groups and labels them incorrectly.

For newly authored animations, the current exporter is more concerning: it fills `unknowngeoflag` with `[1] * 32` for some animation indices and then separately writes `Start`, `Length`, `Loop`, and `Speed`. Under the likely engine layout, that means the first four values become animation timing fields, the next 28 become the first 28 mesh-index entries, and the separately authored fields occupy the final four mesh-index entries.

Do not modify this behavior until the exact PDB layout and Redux runtime behavior are verified.

### Required ANIM verification

Before implementation:

1. Inspect the actual 1.5 and Redux PDB type definitions for `tagANIMOBJ_ANIM` / equivalent.
2. Record member names, offsets, and `sizeof`.
3. Confirm whether the tail is exactly `meshIndex[32]`.
4. Trace whether Redux consumes any `meshIndex` values even if 1.5 does not.
5. Reinterpret a few stock ANIM records using the corrected five-field prefix plus 32-dword tail.
6. Only then redesign the Blender ANIM UI/export model.

## 4. Correct confidence wording for the GEO header field

The engine evidence strongly supports:

> The GEO header dword at `+0x20` is ignored by the observed 1.5 and Redux loader paths.

The historical interpretation as a pre-per-face renderer-mode word is plausible because the old editor bit names overlap strongly with per-face rendering semantics, but that history is not directly proven by the available engine code.

Prefer wording such as:

> The field appears to be a surviving legacy renderer-mode field; it is definitively ignored by the examined 1.5 and Redux loaders.

Keep the historical origin marked **INFERRED**, while the non-consumption result remains **CONFIRMED** for the examined builds.

## 5. Redux/toolkit treatment remains straightforward

For current Redux authoring, the practical guidance remains:

- Preserve the `.GEO` header `+0x20` dword for round-trip compatibility.
- Default it to `0` for newly authored GEOs.
- Do not expose its historical bit names as meaningful Redux rendering controls.
- Keep per-face `ShadeType`, `TextureType`, and `XluscentType` as the meaningful legacy face fields.
- Keep VDF/SDF `ObjectFlags` separate; it seeds live object-system state and is not a GEO render flag.
- Do not change ANIM authoring semantics until the PDB/Redux verification above is complete.

## Merge gate

Before an implementation PR changes exporter behavior, update `GEO_FLAGS_RESEARCH.md` and `EXPERIMENTAL_BINARY_FIELDS.md` so they agree on:

1. the corrected VDF/SDF object-record layout;
2. the 13-byte GEO texture-name field and `0x37` fixed face size;
3. the distinction between confirmed ANIM timing fields and the still-to-be-verified 32-dword tail;
4. the confirmed-vs-inferred confidence wording for the legacy GEO header field.

This review intentionally does not change exporter behavior.