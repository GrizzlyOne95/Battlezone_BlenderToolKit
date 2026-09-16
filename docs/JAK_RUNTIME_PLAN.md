# Jak Pilot Runtime Plan (Tasks 3-7)

Status: Blender asset conversion QUALIFIED (see `docs/JAK_PILOT_QUALIFICATION.md`).
Runtime game qualification (Redux + lcbench) is still open. This document
captures the concrete, stock-grounded plan so the next session can execute
without re-deriving baselines.

## 1. Animation surface (shipped in `jakpilot.skeleton`)

30 Ogre clips, verified by serializer read-back (46 bones):

- Native sniper/crouch FSM as three-stage melee controller:
  - `stand2Kneel` (index 0) -> `attack1` content
  - `fireRecoilSniper` (index 3) -> `attack2` content
  - `kneel2stand` (index 1) -> `attack3` content
- Locomotion: `runForward/Backward/Left/Right`, `walkForward/Backward/Left/Right`,
  `jump` -> `walk` content; `run`/`jump` ODF aliases preserved.
- Death: `death1`, `death2` -> `death` content.
- Idle hold: `idle`, `idleParachute`, `landParachute`, `idleEject`, `idleElect`,
  `Take_001` -> `idle` content.
- Native Jak clips preserved: `walk run jump idle death curious
  attack1 attack2 attack3 attack4 eat1 eat2`.

## 2. Core Task 3 -- prove the native sniper FSM sequence (DO FIRST)

Do not add engine hooks until Lua/ODF/weapon behavior is exhausted.

Stock baselines (observed in `ISDF Chronicles` addon):

- `fssnip.odf`: `classLabel = "person"`, weapon `g2snipe` (`classLabel = "snipergun"`).
- `fspilo.odf`: `classLabel = "person"`, weapon `fgsnip_c`.
- Both carry `[PersonClass] xplSnipe = "xplSnipe"`.

Expected (UNPROVEN -- verify, do not assume):

    idle -> stand2Kneel [0] on sniper equip/crouch-enter
         -> fireRecoilSniper [3] on sniper fire
         -> kneel2stand [1] on sniper unequip/stand
         -> idle

Trace protocol (safest instrumentation first):

1. Equip a stock pilot with a stock sniper (`fssnip` + `g2snipe`) in a clear
   lcbench-style mission.
2. Record per event: Person animation index, Ogre animation name, selected
   weapon, shot event, timestamp/order. Sources in order of preference:
   Redux logs, existing OpenShim Person animation tracing, EXU animation
   inspection, scripted mission prints.
3. Test matrix:
   - select sniper in inventory (no fire);
   - fire one aimed shot;
   - switch away / remove sniper;
   - crouch/stand without sniper (if the key exists for pilots);
   - jump, run, death for contrast.
4. Critical question: WHAT exact gameplay event causes indices 0 and 1?
   If equip/remove suffices, the dummy-sniper mechanism (section 3) is viable.
   If a crouch key or AI state is also required, record the extra input.

## 3. Core Task 4 -- invisible dummy sniper/melee trigger (DRAFTS PROVIDED)

Drafts in `assets/jak/` (UNQUALIFIED -- tune in game):

- `jakbite_gun.odf`: clone of stock `g2snipe` (`classLabel = "snipergun"`)
  with `fireSound` removed, `shotDelay` kept short, and ordnance pointed at
  `jakbite_shell`. No visible gun model changes are made at the ODF level;
  hide the weapon mesh via the wrapper loadout or an emptyDeploy mesh swap
  during qualification.
- `jakbite_shell.odf`: clone of stock `g2snipb` (`classLabel = "sniperShell"`)
  with `damageBallistic = 0`, `killRadius = 0`, `killLength = 0`,
  `lifeSpan` minimal, tracer/smoke/flash render blocks deleted.

Desired native sequence:

    locomotion -> AI selects jakbite_gun -> stand2Kneel (attack1 wind-up)
      -> AI fires -> fireRecoilSniper (attack2 strike)
      -> AI removes weapon -> kneel2stand (attack3 recovery)
      -> locomotion

If Redux produces extra unavoidable sniper states, map them sensibly
(parachute/eject slots already resolve to `idle`) and record the observed
sequence here.

## 4. Core Task 5 -- keep real melee damage separate

Never use the dummy projectile as the hit mechanism. Model:

    begin attack -> attack2 strike window (known timed offset into
    fireRecoilSniper/attack2 content) -> range+cone+alive check ->
    apply damage from Lua/mission API -> recovery -> locomotion

First proof-of-concept: coarse timer aligned to attack2 is acceptable.
Later: synchronize with EXU animation time/state if reliable.

## 5. Core Task 6 -- BZ1/Redux Person wrapper recipe

Draft: `assets/jak/jakpilot.odf` (UNQUALIFIED), cloned from stock `fspilo.odf`:

- `classLabel = "person"` (Person-compatible gameplay shell);
- `geometryName = "jakpilot"` with exported `jakpilot.mesh`,
  `jakpilot.skeleton`, `jakpilot.material`, Jak TGA textures;
- own custom skeleton (NOT `aspilo.skeleton`); stock pilot animations are
  not needed because the required animation NAMES exist in the Jak skeleton;
- legacy shell: clone a known-good stock pilot VDF/GEO pair
  (`fspilo.vdf` baseline) and re-point its mesh reference; keep
  collision/gameplay semantics, required Person animation-table semantics;
- WORLD-only for this milestone. Explicitly out of scope: first-person
  cockpit, POV/camera bones, weapon-helper alignment, stock hardpoints.

Scale note: the FBX source is centimeter-scale (mainbody ~0.022 x 0.025 x
0.058 Blender units; `hp_dummyroot_1` carries 0.01 scale). Do NOT bake a
rescale into the Blender asset yet -- decide between ODF `geometryScale`,
export scale, or mesh scale during in-game qualification and record it.

## 6. Core Task 7 -- minimal lcbench runtime test outline

Setup: player + neutral Jak + enemy/target dummy, large clear area.
Scripted controls: spawn, wander, force idle/walk/attack, equip/fire/unequip
dummy sniper, play curious/eat, kill. Log per tick: Person index, Ogre clip,
weapon state, damage events, locomotion recovery.

## 7. Neutral wildlife AI (initial, after model works)

States: IDLE / WANDER / CURIOUS / FLEE / ATTACK / EAT.
First prototype: neutral wander + manually forced attack is sufficient.
Do NOT recreate BZ2 `LandAnimalProcess` before the model/animation path is
proven. Original BZ2 tuning (`mcjak01.odf`: cool 125, yum 75, goto-close 15,
flee 75, goto2attack 15, crowded 30) is preserved in the source ODF for later
Lua behavior work.
