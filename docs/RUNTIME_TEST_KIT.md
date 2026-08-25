# Runtime Test Kit (manual, local-only)

`scripts/make_runtime_test_kit.py` turns any stock vehicle `.vdf` into two
modified copies for verifying the advanced semantics against the real game.
Generated files and results are **never committed**; this documents how to run
the check locally.

## Generate

```bat
python scripts\make_runtime_test_kit.py "C:\path\to\somevehicle.vdf" C:\temp\bz_runtime
```

## Install

Copy each generated `.vdf` into the same addon/campaign folder as its source
file so the referenced `.geo` dependencies resolve. Keep backups of anything
you overwrite; prefer a personal mod folder over stock campaigns when possible.

## Checks

| File | What it proves | Expected result |
|---|---|---|
| `<name>_dmgload.vdf` | VGEO damage-state bands 1-3 populated for every part load cleanly through real engine code (`GeoCache_AddRep` walks all 28 bands). | Vehicle loads and behaves exactly like stock (names unchanged = visual no-op). No crash/hitch at spawn or on LOD switch. |
| `<name>_vlocsmoke.vdf` | `Process_VLOC_Chunk` generic injection creates a live class-76 node that `Craft::FindSmokeSource` collects. | After damaging the vehicle, smoke rises from a point ~6 m ahead / 4 m above the hull in addition to normal damage smoke. |

## Confidence notes

- The VLOC dispatch, emitter collection order, and 8-slot budget are
  CONFIRMED from decompiled bzone.exe 1.5 and cross-checked against Redux
  symbol transfer tables (`docs/GEO_TYPES_RESEARCH.md` §5.6/§5.12).
- Damage states stay visually inert in stock engines: nothing calls
  `ObjTree_SelectRep`, so `<name>_dmgload.vdf` verifies load-path tolerance,
  not visible swapping. A visible swap test requires an external driver
  (e.g. a BZR-OpenShim health hook) and is out of scope here.
- If your source vehicle already has ≥8 class-76 parts, skip the VLOC check:
  the injected ninth emitter would overflow the fixed engine array (that is a
  validation ERROR in the toolkit, not a supported configuration).

## Status

Runtime execution is a manual step by design (no automated game harness).
Record results locally per vehicle tested; do not attach generated assets to
issues or commits.
