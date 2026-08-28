# Redux Pilot Animation Runtime Test

Use this checklist to qualify the pilot animation patch workflow in Battlezone 98 Redux before PR #9 is marked ready.

## First test: one third-person clip

1. Install/run the toolkit from `agent/pilot-animation-patch-workflow` in Blender 4.5 LTS.
2. Import one stock third-person pilot `.mesh` with **Import Animations** enabled. Soviet `sspilo` is a good first target.
3. Keep the imported stock armature unchanged: do not rename/delete/reparent OGRE bones or edit `OGREID` values.
4. Create or retarget an obviously different Action for `runForward` so the replacement is unmistakable in-game.
5. Open **Battlezone > Pilot Animation Patch**.
6. Choose the imported armature and the original unmodified stock `.skeleton`.
7. Click **Load / Refresh Stock Clips**.
8. Map the test Action to `runForward`, enable **Replace**, then **Preview** it.
9. Click **Validate**. Do not continue if validation reports a bone/OGREID/bind-contract error.
10. Export to a separate output file with **Bake + Export Patch**.
11. Put the generated skeleton in a test addon using the exact stock filename, while leaving the stock `.mesh` unchanged.
12. Launch Redux and force/observe normal forward-running pilot movement.

### Third-person pass criteria

- Redux loads the pilot without crash/resource errors.
- `runForward` visibly uses the replacement motion.
- Other locomotion/idle/death clips still use their original stock motion.
- The pilot mesh is not distorted in idle or during the replacement clip.
- No obvious bone explosions, bind-pose offset, flipped limbs, timing corruption, or looping regression occurs.

If this passes, repeat with one additional stock clip such as `idle` or `jump` to prove selective multi-clip replacement.

## Second test: one first-person clip

Repeat the same process using the matching `_fp` mesh/skeleton. Keep the `_fp` skeleton as the authoritative stock contract; do not reuse the third-person output.

Prefer a clip that is easy to trigger and observe. Verify specifically that:

- first-person arms remain aligned;
- weapon/helper motion remains sane;
- POV/camera behavior does not acquire an unexpected offset or rotation;
- untouched first-person clips remain stock.

## Failure capture

If a test fails, record:

- pilot asset and view (`sspilo`, `sspilo_fp`, etc.);
- patched clip name;
- source Action name;
- whether Blender **Validate** passed;
- whether **Bake + Export Patch** completed without error;
- whether failure occurs on load, on animation start, or only visually;
- screenshots/video and Blender console/Redux log output when available.

Do not merge PR #9 based only on successful file generation. The minimum runtime gate is one confirmed third-person replacement and one confirmed first-person replacement in Redux.
