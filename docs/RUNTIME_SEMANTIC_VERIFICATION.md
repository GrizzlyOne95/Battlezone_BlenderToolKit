# Redux Runtime Semantic Verification

## Purpose

PR #6 established strong structural and file-format correctness for advanced GEO/VDF authoring. This follow-up phase closes the remaining behavioral gap by validating authored semantics in a live Battlezone 98 Redux runtime.

The central rule for this work is evidence chaining. Do not change exporter behavior merely because the game behaves unexpectedly. For every experiment, record all three layers:

1. Blender semantic setting / authored scene state.
2. Exact exported VDF/SDF/GEO bytes and decoded field values.
3. Observed Redux runtime behavior.

Only promote a behavior from inferred/research-backed to runtime-verified when those three layers agree and the result is reproducible.

## Non-goals

- Do not reinterpret already-verified ANIM element layout without new contradictory executable evidence.
- Do not replace unknown fields with guessed semantics.
- Do not canonicalize or discard unknown bytes merely to simplify test assets.
- Do not use PDB names as sole proof of runtime behavior.
- Do not weaken byte-exact preservation guarantees established in PR #6.

## Test asset policy

Use synthetic/minimal authored assets where practical. Do not commit copyrighted stock assets. If a stock asset is needed as a local baseline, document only the filename, field values required for comparison, and observed behavior.

Each synthetic runtime case should be intentionally small and isolate one variable. Paired A/B assets are preferred over complex all-in-one fixtures.

## Evidence record for every case

For each experiment, capture:

- test ID and purpose;
- Blender property values;
- object names and hierarchy;
- relevant transform matrix;
- exported filename;
- exact decoded record/chunk values;
- byte offsets for the tested field where practical;
- Redux version/build used;
- mission/map and spawn method;
- expected behavior;
- actual behavior;
- repeat count;
- verdict: VERIFIED / DISPROVED / INCONCLUSIVE;
- resulting documentation, tooltip, validation, parser, or exporter change, if any.

## Phase A — VLOC class 38: headlight

Build paired synthetic vehicle assets with an otherwise-identical VLOC class-38 record.

Verify independently:

1. Presence vs absence of the VLOC record.
2. Translation X/Y/Z changes.
3. Rotation changes one axis at a time.
4. Uniform and non-uniform scale behavior.
5. Multiple class-38 records in one asset.
6. Ordering of multiple records.
7. Behavior when the associated geometry/bind object is absent or renamed.

Acceptance criteria:

- identify which matrix components affect the live light;
- identify whether scale is read, ignored, clamped, or produces side effects;
- establish whether multiple records are supported and whether order matters;
- update tooltips/docs only with reproducible observations.

## Phase B — VLOC class 40: eyepoint / POV

Construct a minimal vehicle with a class-40 helper and controlled transforms.

Verify independently:

1. Baseline camera/POV origin.
2. Translation X/Y/Z.
3. Rotation around each axis.
4. Uniform and non-uniform scale.
5. Presence/absence of a matching `.geo` part.
6. Duplicate class-40 records.
7. The currently documented `x25` relationship.

For the `x25` hypothesis, measure at least three known authored offsets rather than accepting a single matching point.

Acceptance criteria:

- derive a reproducible transform relationship between authored matrix values and live POV position/orientation;
- confirm, revise, or reject the `x25` documentation;
- define validation rules for duplicate or malformed POV records only if runtime behavior supports them.

## Phase C — VLOC class 42 and generic/unknown injection kinds

Treat class 42 as opaque until behavior is demonstrated.

Test:

1. presence vs absence;
2. extreme but valid translations;
3. rotations;
4. scale changes;
5. interaction with otherwise-identical assets;
6. whether any visible, physical, targeting, camera, collision, or attachment behavior changes.

If no external behavior is observed, retain opaque preservation semantics and document the negative result without inventing a label.

Generic/unknown VLOC classes should receive the same preservation-first treatment.

## Phase D — damage representation bands

Create a synthetic vehicle with visually unmistakable geometry for damage states 0, 1, 2, and 3.

Verify:

1. exact runtime thresholds/transitions between states;
2. `band = lod_slot * 4 + damage_state` under live execution;
3. state behavior at multiple LOD distances;
4. missing state 1/2/3 behavior;
5. duplicate/shared geometry names across states;
6. fallback behavior when a damage band is absent;
7. whether damage-state switching is driven solely by vehicle health or by another state variable;
8. behavior after repair/healing, if Redux permits returning across thresholds.

Acceptance criteria:

- visually and byte-level prove which VGEO bands Redux selects for each live state;
- record exact fallback behavior for missing bands;
- add pure fixtures/tests for any newly discovered serialization requirement;
- do not alter the lod-major mapping unless contradictory executable/runtime evidence is obtained.

## Phase E — ObjectFlags one-bit-at-a-time matrix

Use minimal paired assets differing in exactly one tested field.

Priority fields:

- bit `0x00000001` — authored/keep bounds behavior;
- bit `0x00000010` — view-related behavior;
- bit `0x00000200` — destroyed-state-related behavior;
- bit `0x00000800` — light-related behavior;
- collision nibble values, including known and reserved values;
- team bits.

For each field distinguish:

- load-time effects;
- render effects;
- collision effects;
- targeting/radar effects;
- AI/pathing effects;
- destruction/damage effects;
- whether the field is ignored for a given asset class.

Unknown bits must remain preserved even if no behavior is found.

## Phase F — BRIDGE / FLOOR runtime behavior

Author minimal structures with controlled surfaces and part classes.

Test:

1. identical geometry classified NONE vs BRIDGE vs FLOOR;
2. shallow traversable surface;
3. steep surface around the documented 66-degree threshold;
4. collision/bounds bit `0x1` combinations;
5. pathing across and under bridge geometry;
6. destroyed-state interaction;
7. AI navigation vs direct player traversal;
8. whether BRIDGE alone has behavior without FLOOR children.

Acceptance criteria:

- separate geometry/collision limitations from class-driven behavior;
- confirm the steep-face warning boundary with runtime evidence;
- document pathing and traversal semantics independently.

## Instrumentation and comparison strategy

Prefer deterministic, repeatable experiments. When visual inspection alone is ambiguous, add lightweight diagnostic tooling that records exported structure and test case identity. Runtime instrumentation may be used when available, but observations should still be reproducible without requiring a debugger for ordinary toolkit users.

For transform experiments, vary one component at a time and keep the rest at identity.

For bit-field experiments, use XOR-style A/B comparisons so only the targeted bit changes.

For damage bands, use deliberately distinct meshes/material appearances so state selection is unambiguous on screen.

## Documentation rules

Use explicit evidence labels:

- **VERIFIED (runtime)** — reproduced in Redux with authored bytes confirmed.
- **VERIFIED (binary)** — supported by executable behavior/static machine-code analysis but not necessarily a live authored experiment.
- **STOCK-DATA OBSERVED** — pattern exists in stock files without proven runtime meaning.
- **INFERRED** — best current interpretation, not yet proven.
- **OPAQUE / UNKNOWN** — preserved without semantic claim.

Do not collapse these categories.

## Regression gates

Every implementation change made from runtime findings must preserve:

- all existing bpy-free tests;
- Blender 4.5.4 registration/integration checks;
- byte-exact synthetic fixture round trips;
- 235/235 local stock parse/serialize byte-exact validation when that local corpus is available;
- unknown-field preservation guarantees from PR #6.

Add a focused pure test whenever a runtime finding changes serialization, validation, or semantic-model behavior.

## Deliverables

This PR should eventually contain:

1. a reproducible runtime test matrix and results table;
2. synthetic test assets/scripts or generators where legally/technically practical;
3. exact byte/field evidence for each runtime claim;
4. corrected tooltips and authoring documentation;
5. validation changes justified by proven engine behavior;
6. focused regression tests for any changed serialization/semantic rules;
7. a final table showing which PR #6 semantics moved from inferred/binary-backed to runtime-verified, which were disproved, and which remain unknown.

## Initial priority order

1. VLOC class 40 eyepoint/POV — highest user-facing authoring risk.
2. Damage representation switching — highest exporter/semantic risk.
3. VLOC class 38 headlight.
4. ObjectFlags `0x1`, `0x10`, `0x200`, `0x800`.
5. BRIDGE/FLOOR traversal and pathing.
6. VLOC class 42 / generic opaque cases.

The first implementation commit after this plan should establish the synthetic runtime harness/test assets and record baseline Redux behavior before changing any semantic code.