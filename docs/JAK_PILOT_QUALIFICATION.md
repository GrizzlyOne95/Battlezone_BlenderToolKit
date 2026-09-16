# Jak pilot Blender qualification -- Blender 4.5.4 LTS, branch agent/jak-sniper-melee-mapping

Build command (repo root on PATH via bootstrap because Blender clears
PYTHONPATH and may pre-load an installed bz98tools addon):

    blender --background --python <bootstrap> -- \
      --source-dir ".../Worlds/Mire/Creatures" \
      --output-blend ".../jakpilot.blend" \
      --manifest ".../jakpilot_manifest.json"

Source: `CombatCommanderSourceMaterialModsv2/Worlds/Mire/Creatures`
(`jak_walk/curious/death01/eat01/attack01-03`, `jak_skel`, `mcjak01.odf`,
`Textures/jak.tga|jak_n.tga|jak_s.tga`).

## Blocker found and fixed: stock FBX importer drops the Jak rig

`jak_walk.fbx` (FBX 7400) contains 50 Model nodes (4 Mesh + 46 LimbNode),
standard OO connections and a 42-node BindPose -- yet Blender 4.5 imported
only the unskinned `hp_dummyroot_1` dummy. Root cause, proven by
instrumenting `io_scene_fbx`: the XSI-style hierarchy puts the skinned
`mainbody` mesh ABOVE its own bones
(`hp_dummyroot_1 -> mainbody -> hip -> ...`). The importer inserts an
armature under the mesh, then `collect_armature_meshes` reparents the mesh
under that armature, creating an armature<->mesh parent cycle that detaches
both from the scene root. Fix: `_cycle_safe_fbx_import` in
`bz98tools/jak_animation_builder.py` keeps ancestor meshes in place while
still registering them for armature-modifier setup. Non-ancestor meshes use
the untouched stock path.

Second fix from the same session: `collect_bake_animations` used
`ActionSlots.get(..., default=...)`, which Blender 4.5 rejects
(`get() takes no keyword arguments`), breaking ALL visual-keying skeleton
bakes including the pilot patch workflow. Fixed with a positional
named-slot/first-slot lookup (`_action_slot_for_armature`).

Custom-rig export note: Jak bones carry no stock `OGREID` props, so
`export_all_bones=True` is required (sequential IDs). The pilot patch path
is unaffected (stock rigs keep their handles).

## Bind-pose validation (rest_tolerance 1e-4)

All six fixed FBXs validate against the canonical `jak_walk` bind pose:
names, hierarchy and rest matrices all match. `jak_skel.fbx` validated for
names+hierarchy only (known different bind matrices on ~36/40 bones) and its
idle was retargeted by evaluated armature-space pose transfer, not raw
F-curve copy.

## Baked ranges (30 FPS, frames are 0-based baked output)

| clip | source file | source range | frames | mode |
| --- | --- | --- | --- | --- |
| walk | jak_walk.fbx | 3-40 | 38 | direct |
| attack1 | jak_attack01.fbx | 2-21 | 20 | direct |
| attack2 | jak_attack02.fbx | 2-19 | 18 | direct |
| attack3 | jak_attack03.fbx | 2-21 | 20 | direct |
| curious | jak_curious.fbx | 2-61 | 60 | direct |
| death | jak_death01.fbx | 2-31 | 30 | direct |
| eat1 | jak_eat01.fbx | 2-51 | 50 | direct |
| idle | jak_skel.fbx | 2-61 | 60 | retarget |

Counts match the expected motion lengths (work-order estimates of idle 1-30
and walk 1-39 were off; actual `jak_skel` idle spans 60 frames). No entity
root exists in the bone set (`hp_dummyroot_1` is a mesh, not a bone), so no
root translation was introduced; body collapse during death lives on `hip`
as intended (hip location range -1.45..0.48, all other clips < 0.5).

## Armature / NLA / export inventory

- `Jak_Armature`, 46 bones, `mainbody` 2717 verts / 39 vertex groups with
  armature modifier; `eye_l`/`eye_r` 58 verts each.
- 30 muted NLA tracks (8 baked + 4 ODF aliases + 18 Jak Person-compat
  aliases), 22 manifest aliases, full 20-name Redux pilot coverage.
- `jakpilot.skeleton` (native backend, visual keying, export_all_bones):
  46 bones, 30 animations; alias durations equal their sources
  (attack4=attack3, eat2=eat1, stand2Kneel/kneel2stand=attack1/3,
  fireRecoilSniper=attack2, locomotion=walk, death1/2=death, rest=idle).
- `jakpilot.mesh` exports FINISHED; one harmless "Invalid vertex group /
  OGREID" warning for the unweighted dummy-root mesh.
- Leftover raw `Armature|Take 001|BaseLayer*` actions remain in the working
  .blend (import byproducts); only NLA actions drive the export.
- Scale: source is centimeter-scale (mainbody ~0.022 units). No rescale
  baked in; decide ODF/export scale during in-game qualification.
- Renders (magenta = textures not yet assigned): idle standing intact,
  attack2 strike distinct, curious head-up distinct, death prone collapse.
