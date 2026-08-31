# Redux Pilot Animation Workflow

This document describes the supported custom-animation path for Battlezone 98 Redux pilots.

## Why pilots are different

Redux pilots use named OGRE skeletal animation clips from the rendered `.skeleton` file. The legacy VDF animation slots still describe gameplay animation semantics, but an animation-only visual replacement should not rewrite the mesh, bone handles, hierarchy, or bind pose.

The toolkit therefore keeps the original stock skeleton authoritative and patches only selected named animation tracks.

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

- v1.80 third-person skeletons contain the OGRE skeleton blend-mode chunk;
- v1.10 first-person skeletons do not contain that chunk;
- stock pilot bone chunks use the no-scale 36-byte bone payload;
- sampled stock animation keyframes use the 38-byte rotation + translation payload; no scale payload was observed;
- all inspected pilot skeletons expose 19 named animation clips.

Do not assume the first-person and third-person rigs are interchangeable. Even when the bone count matches, the handle mapping and helper usage can differ.

## Blender workflow

The addon now exposes **Battlezone > Pilot Animation Patch** in the 3D View sidebar.

### 1. Import the stock pilot

Import the original Redux pilot `.mesh` with **Import Animations** enabled. Keep the generated armature. The Ogre importer stores each original numeric bone handle as the Blender bone custom property `OGREID`.

For an animation-only replacement, do not rebuild or re-export the stock mesh.

### 2. Choose the stock contract

In **Pilot Animation Patch**:

1. Choose the imported pilot armature, or leave it unset when the correct armature is the active/selected one.
2. Choose the original unmodified stock `.skeleton` file.
3. Click **Load / Refresh Stock Clips**.

The toolkit reads the actual file and populates its clip names, durations, and track counts. It also performs best-effort stock profile detection for:

- Soviet third person;
- Soviet first person;
- American third person;
- American first person;
- Black Dog third person;
- Black Dog first person.

The stock file remains authoritative even if profile detection reports an unknown/custom rig.

### 3. Author or retarget motion

Bring the desired source animation into Blender using FBX, BVH, glTF, another `.blend`, or normal Blender animation tools. Retarget the final motion onto the imported stock pilot armature.

The patch exporter uses **visual/evaluated baking**, not just direct quaternion FCurves. Constraints, IK, Euler-authored motion, quaternion motion, and other evaluated pose results are therefore sampled into the final Ogre tracks.

Automatic source-rig retargeting is not yet part of this panel. The Action selected for export must already drive the stock pilot armature correctly when previewed in Blender.

### 4. Map Actions to Redux clips

Each discovered stock clip has:

- a **Replace** checkbox;
- a **Source Action** selector;
- the original stock duration and track count for reference.

Use **Auto-map** when Actions already use stock-style names such as `runForward`, `idle`, or `stand2Kneel`. Blender numeric suffixes such as `.001` are tolerated. The known Soviet first-person `idleElect` / normal `idleEject` spelling variant is also handled as an alias.

You do not have to rename the artist/source Action. The exporter creates temporary uniquely named Action copies and maps the baked result back onto the chosen Redux clip name.

**Use Active Action** assigns the armature's current Action to the highlighted Redux clip. **Preview** makes the mapped Action active on the selected pilot armature so the result can be checked before export.

### 5. Validate

Click **Validate** before export. The Blender-side preflight checks:

- the selected armature contains OGREID metadata;
- the OGREID handle set exactly matches the stock skeleton;
- each handle still has the stock bone name;
- each OGRE bone still has the stock OGRE parent;
- every selected Redux clip has a mapped source Action;
- the output path does not overwrite the stock input.

Extra Blender-only control bones are allowed as long as they do not replace or interrupt the stock OGRE hierarchy.

### 6. Bake + Export Animation Patch

Click **Bake + Export Patch**.

The exporter then:

1. clones only the selected source Actions into temporary non-destructive Actions;
2. evaluates them on a temporary copy of the stock armature;
3. bakes every frame at **30 FPS** using the toolkit's native Ogre visual-keying path;
4. deliberately omits scale animation to match the inspected stock pilot keyframe layout;
5. exports a temporary Ogre skeleton;
6. reloads that skeleton through the raw Ogre serializer;
7. validates its bind pose against the original stock skeleton;
8. copies only the selected named clips into the original stock skeleton object;
9. writes a new output skeleton using the stock serializer version (`v1.80` or `v1.10`);
10. reloads the output and validates the stock bone contract and expected animation names again.

The final file write is atomic: a temporary file is written first and then moved over the requested non-stock output path.

Blender selection, active object, frame, mode, FPS, and frame-step state are restored after the bake attempt.

## Safety model

There are two independent safety gates.

### Blender armature preflight

The artist armature must still carry the original OGRE handle/name/parent contract. This catches accidental bone deletion, renaming, OGREID edits, and hierarchy edits before the temporary bake is attempted.

### Binary patch validation

Before a baked clip is copied, the patch backend additionally validates:

- exact bone-handle set;
- bone name for each handle;
- parent handle for each bone;
- bind position within a small floating-point tolerance;
- bind orientation within a small floating-point tolerance, treating quaternion `q` and `-q` as equivalent;
- bind scale within a small floating-point tolerance;
- every animation track targets an existing stock handle with the matching stock bone name.

The output is serialized from the original stock skeleton object. This preserves stock:

- numeric bone handles;
- names;
- hierarchy;
- bind transforms;
- linked skeleton animation sources;
- untouched animation clips;
- source serializer version.

Multi-clip patching is transactional: all selected replacement clips are cloned and validated before any clip is committed to the stock in-memory animation map.

## Command-line patcher

The lower-level CLI remains useful for diagnostics and non-Blender workflows:

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

Do not equate these gameplay slot numbers with the physical serialized order of Ogre animation chunks. Redux resolves named Ogre clips from the pilot animation semantics.

## First-person guidance

First-person pilot skeletons are separate animation targets. Retarget the same source motion to the appropriate `_fp` armature and make view-specific adjustments to arms, weapon helpers, and POV/helper motion.

Avoid copying a third-person skeleton wholesale over an `_fp` skeleton. The patch exporter always uses the chosen original `_fp` skeleton as the authority, so first-person helper bones and handle mappings survive unchanged.

For stock FP rigs, exact OGRE-hierarchy validation also implicitly requires the expected POV/gun helper bones because they are part of the authoritative stock handle set.

## Remaining qualification / future work

The Blender panel, profile detection, Action mapping, 30 FPS visual bake, and animation-only patch export are implemented. The remaining gates are:

1. run the generated 3P and FP skeletons in Redux and prove stock + patched clips behave correctly in game;
2. test a complete external FBX retarget on the American, Black Dog, and Soviet rigs;
3. add optional source-rig retarget helpers so importing an FBX/BVH can be more automatic;
4. add higher-level presets for common locomotion/death/parachute clip groups if useful.

The intended artist workflow is now **import stock pilot → retarget/animate → map Action to Redux clip → preview → validate → bake + export animation patch** while the binary compatibility rules stay internal to the toolkit.
