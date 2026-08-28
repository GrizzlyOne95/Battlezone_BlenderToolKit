# Redux Pilot Animation Workflow

This document describes the first supported custom-animation path for Battlezone 98 Redux pilots.

## Why pilots are different

Redux pilots use named OGRE skeletal animation clips from the rendered `.skeleton` file. The legacy VDF animation slots still describe gameplay animation semantics, but an animation-only visual replacement should not rewrite the mesh, bone handles, hierarchy, or bind pose.

The safest workflow is therefore to keep the original stock skeleton authoritative and patch only selected named animation tracks.

## Stock pilot skeleton contract

Qualification against the supplied stock pilot files found:

| Pilot asset | View | Skeleton serializer | Bones | Named clips |
| --- | --- | --- | ---: | ---: |
| `sspilo` | third person | `[Serializer_v1.80]` | 32 | 19 |
| `sspilo_fp` | first person | `[Serializer_v1.10]` | 32 | 19 |
| `aspilo` | third person | `[Serializer_v1.80]` | 71 | 19 |
| `aspilo_fp` | first person | `[Serializer_v1.10]` | 73 | 19 |
| `bspilo` | third person | `[Serializer_v1.80]` | 71 | 19 |
| `bspilo_fp` | first person | `[Serializer_v1.10]` | 73 | 19 |

The observed stock files have these format characteristics:

- v1.80 third-person skeletons contain the OGRE skeleton blend-mode chunk.
- v1.10 first-person skeletons do not contain that chunk.
- stock pilot bone chunks use the no-scale 36-byte bone payload.
- sampled stock animation keyframes use the 38-byte rotation + translation payload; no scale payload was observed.
- all inspected pilot skeletons expose 19 named animation clips.

Do not assume the first-person and third-person rigs are interchangeable. Even when the bone count matches, the handle mapping and helper usage can differ.

## Current animation-only patch workflow

1. Import the original pilot `.mesh` / `.skeleton` with **Import Animations** enabled.
2. Keep the imported armature. The toolkit stores the original OGRE numeric handle as each bone's `OGREID`.
3. Retarget or author animation on that stock armature.
4. Bake the final evaluated motion onto the stock bones. A 30 FPS authoring/bake rate is recommended for stock-like timing.
5. Name the Blender Action exactly like the Redux clip being replaced, for example `runForward` or `idle`.
6. Export a temporary replacement `.skeleton` from Blender.
7. Patch only the desired clip(s) into a copy of the original stock skeleton.
8. Put the patched skeleton in the mod/addon override location while leaving the original mesh unchanged.

## Command-line patcher

From the repository root:

```text
python scripts/patch_pilot_skeleton.py STOCK.skeleton EXPORTED.skeleton OUTPUT.skeleton -a runForward
```

Patch several clips:

```text
python scripts/patch_pilot_skeleton.py STOCK.skeleton EXPORTED.skeleton OUTPUT.skeleton \
  -a idle -a runForward -a runBackward -a runLeft -a runRight
```

Patch every clip present in the replacement skeleton:

```text
python scripts/patch_pilot_skeleton.py STOCK.skeleton EXPORTED.skeleton OUTPUT.skeleton --all
```

The standalone command uses the toolkit's Ogre serializer stack, which requires NumPy. Blender's bundled Python already provides NumPy; a separate CPython environment must have it installed.

## Safety checks

Before a clip is copied, the patcher validates that the replacement skeleton matches the stock rig:

- exact bone-handle set;
- bone name for each handle;
- parent handle for each bone;
- bind position within a small floating-point tolerance;
- bind orientation within a small floating-point tolerance;
- bind scale within a small floating-point tolerance;
- every animation track targets an existing stock handle with the matching stock bone name.

The output is then written from the original stock skeleton object. This preserves stock:

- numeric bone handles;
- names;
- hierarchy;
- bind transforms;
- linked skeleton animation sources;
- untouched animation clips;
- source serializer version.

The patcher refuses to overwrite either input file in-place.

## Known person animation semantics

The toolkit's current person slot reference is:

| Legacy/game slot | Semantic / visual clip family |
| ---: | --- |
| 0 | Stand to snipe |
| 1 | Snipe to stand |
| 2 | Standing / idle |
| 3 | Sniping / idle |
| 4 | Forward movement |
| 5 | Backward movement |
| 6 | Strafe left |
| 7 | Strafe right |
| 8 | Sniped / death |
| 9 | `idleParachute` |
| 10 | `landParachute` |
| 11 | `jump` |

Do not equate these gameplay slot numbers with the physical serialized order of OGRE animation chunks. Redux resolves named Ogre clips from the pilot animation semantics.

## First-person guidance

First-person pilot skeletons should be treated as separate animation targets. Retarget the same source animation to the appropriate `_fp` armature and then make view-specific adjustments to arms, weapon helpers, and POV/helper motion.

Avoid copying a third-person skeleton wholesale over an `_fp` skeleton. The animation patcher intentionally uses the original `_fp` skeleton as the authority so first-person helper bones and handle mappings survive unchanged.

## Next implementation stages

The backend/CLI is the first stage. Planned Blender integration is:

1. Pilot Animation panel with stock/replacement/output skeleton selectors.
2. Automatic detection of third-person versus `_fp` pilot rigs and source serializer version.
3. Checklist of discovered named clips with stock person presets.
4. One-click bake of selected Blender Actions to the imported stock armature.
5. Animation-only patch export directly from Blender, without requiring a temporary command-line step.
6. FP helper/POV validation and warnings.
7. Optional retarget helpers for FBX/BVH/glTF source animation.

The final goal is an artist-facing workflow of **import stock pilot → retarget/animate → bake → export animation patch** while keeping the binary compatibility rules internal to the toolkit.
