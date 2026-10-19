# Redux cockpits and walker units: findings (2026-10-05)

These notes come from building the ISDF Chronicles **Attila Mk II** (`ivwalkh`): a BZ2 beta hi-res walker given a new VDF ANIM gait, telescoping pistons and a new first-person cockpit. The build scripts live outside this repo, in `Documents\BZ_PilotCompare\walkerhr`:
- `build.py`: the VDF, mesh and skeleton writer.
- `cockpit.py`: the cockpit builder.
- `cockpit_view.py`: a Blender eye-point preview of any `mesh.xml`.
- `WALKER_NOTES.md`: the notes.

Evidence:
- the stock `ivwalk` / `ivwalko` files in ISDF Chronicles;
- `bzrmodelporter/bzportmodels.py` (cockpit LOD handling);
- the BZ1 1.5 decompile (`Walker::Simulate`, `Walker::UpdateWeaponAim`);
- `GEO_TYPES_RESEARCH.md` §5.13.

Nothing here has been confirmed in game yet except where stated.

## 1. How a Redux craft cockpit is put together
- **File:** in internal view the engine draws `<baseName>_cockpit.mesh`. `cockpit_suffix_map` in `bzportmodels.py` lists the stock exceptions that use `_c` or `_fp` instead. The mesh's `skeletonlink` names its own skeleton, usually `<baseName>_cockpit.skeleton`.
- **Bone takeover:** any cockpit-skeleton bone whose 8-character name matches a VDF part follows that part, exactly as on the 3rd-person model. That is how the cockpit rides the unit's VDF animation, including turret yaw and pitch.
- **LOD character (name index 3):**
  - `1` parts are the 3rd-person body; `2` parts are cockpit-only.
  - In the cockpit model, `1` bones are scaled to 0; on the 3rd-person model, `2` bones are scaled to 0 (REDUX_PILOT_NOTES).
  - Cockpit geometry is therefore skinned to `xxx21yyy` bones, each parented at identity to its `xxx11yyy` counterpart. The porter does the same: in separate-cockpit mode a cockpit bone gets an identity transform under its primary bone.
- **Stock `ivwalk_cockpit.mesh`, 2 submeshes:**
  - the ISDF overhead console (`ivcockcox00` textures) on `iwk21bda`, a child of the body `iwk11bda`;
  - the gun barrels (the hull texture) on `iwk21tx1`, a child of the gun yaw part `iwk11tx1`.
  - The stock VDF also carries `iwk21bda`, `iwk21tx1` and `iwk21ty1` parts.
- **Camera:** it sits at the POV part (class 40). `HoverCraft::UpdateEyepoint` scales the POV basis by 25 into the view offset and shake (§5.13), so keep the POV part's rotation and scale clean.
- **Weapons don't care about cockpit geometry.** Shots leave from the VDF hardpoints (`gc#`, `la#`, `gm#`...). `Walker::UpdateWeaponAim` builds a pitch matrix from the aim pitch and writes it into each weapon's transform relative to its hardpoint parent. Guns in the cockpit mesh are set dressing: put them where they read well in the view.
- **Material:** cockpit submeshes use `BZBaseCockpit`. The first-person layer is composited only where cockpit geometry wrote depth, so glows need the cockpit pass with depth write on (REDUX_PILOT_NOTES).

## 2. Pitfall: reusing a cockpit mesh from another unit
`ivwalk_cockpit.mesh` links **`ivwalko_cockpit.skeleton`**, not `ivwalk_cockpit.skeleton`. Its bone indices only exist in that 38-bone skeleton, whose `iwk*` names match the ivwalk / ivwalko VDF parts.

If you copy it to a unit with different part names (the first Mk II build used `ivwalkh_cockpit.mesh` = a copy):
- no bone is taken over, so the console and guns sit frozen at the stock model-space position;
- they don't follow the new unit's head or POV;
- turret motion doesn't move them.

A cockpit must be rebuilt (or rebased) for each unit's part names. A cheap rebase is to translate the stock geometry by `POV_new - POV_stock` and skin it to a `21` bone under the new POV's parent. `cockpit.py` does this for the overhead console.

## 3. Authoring a cockpit when the eye is inside the hull
- **Stock framing:** on the stock Attila the console sits 0.3-1.2 m ahead of the eye, along the top ~10% of the view, and the gun barrels fill the bottom corners. Use that as the reference framing.
- **When the real canopy is no use:** if the POV sits inside a small canopy (the Mk II's eye is 0.2-0.5 m behind faceted glass), the real geometry is too close to frame anything. Build a stylised frame instead, about 0.85-1.0 m out. Echo the exterior's facet layout, keep struts thin (about 2-3° of view) and keep the centre clear for the reticle.
- **Rigging:** skin all cockpit pieces to **one** `21` bone under the part the POV hangs from (Mk II: `iwh21cab` under `iwh11cab`). The frame then stays registered to the view when that part bobs.
  - Splitting pieces across bones that move relative to the POV makes them swim in view. The Mk II's gun pods pitch on `ty1` about a pivot 2.6 m from the eye, so they stay on the cab bone.
- **Presence:** copying exterior parts (scaled and placed for framing) works. The Mk II shows its cannon pods at 0.6 scale in the bottom corners, toed in 12° so the muzzle brakes show past the pod shrouds.
- **Connect every frame member.** Each strut must end on another strut, the console, a pedestal that rises from below the view, or off-screen. Cover joints with gusset plates. A free-standing console box or a strut ending in mid-air reads as floating geometry (user feedback on the first Mk II pass).
- **Build the frame as one outline, not overlapping beams.** Separate box struts with gusset plates clip through each other at every joint, which is very visible right in front of the eye (user feedback on the second pass). `cockpit.py` instead does this:
  - unions all strut centre-lines into one polygon in a single plane about 0.9 m from the eye (`shapely` buffer with mitred joins);
  - triangulates it (`shapely.constrained_delaunay_triangles`);
  - extrudes the outline away from the eye.
  - The joints come out as clean mitred corners.
- **Hide strut ends** behind something nearer the eye (the pedestal front is at 0.86 m, the frame plane at 0.9 m) or off-screen. A strut that pierces a visible face shows its intersection line.
- **Mount on existing geometry:** measure the stock console's lower edge projected onto the frame plane (`trim_curve`) and run the top member along it, so the frame sits flush instead of poking posts into the console face.
- **FOV:** check the framing at more than one FOV (65/75/90° horizontal). Elements tuned for one FOV slide off-screen or into the middle at others.

## 4. Walker units (VDF ANIM): facts for exporters
Source: `Walker::Simulate`, confirmed on `ivwalk.vdf`.
- **Animation:** walkers play legacy VDF ANIM elements by index, not Ogre clips:
  - 2 = idle;
  - 4 / 5 = walk forward / back;
  - 6 / 7 = strafe, with 7 the reverse of 6;
  - 0 → 3 → 1 = jump launch → in the air → landing.
  - The element's `meshIndex[32]` block is unused.
- **`animRate` is frames per metre:** while moving, `AnimObj_SetRate(handle, speed * animRate)` sets frames per second. For planted feet, `animRate = frames_per_cycle / metres_per_cycle`. The jump elements keep their own fps.
- **VDF matrices:**
  - They are DirectX row vectors (world = local · R_parent).
  - A stored 3x3 is the standard matrix of its key quaternion (w,x,y,z), i.e. the transpose of the column-vector rotation.
  - Keys are absolute local values.
  - The VDF frame is the Ogre frame mirrored in X.
- **Turret ids:** read at part-name character 7 (1-based). `x` = a yaw slot (several allowed), `y` = the single pitch slot.
  - ISDFC's walker pitches its head (`ty1`) and yaws its guns (`tx1`/`tx2`).
  - A unit with two pitching pods must merge them into one `ty1` part.
- **Collision:** each part's GEO faces become collision polygons (`Cgeom_Create`, §5.14), and the VDF COLP gives the inner/outer boxes. Cheap per-part boxes are enough for a port.

## 5. Recommended toolkit updates (documentation-level; not implemented)
1. **Validation:**
   - Warn when a separate cockpit skeleton has no bone matching any VDF part name (a copied cockpit, section 2).
   - Warn when a cockpit mesh's `skeletonlink` names a skeleton from another unit.
2. **Cockpit rebase helper:** translate a stock cockpit by the POV delta, rename its `21` bones to the target unit's prefix, and rewrite the `skeletonlink`.
3. **POV framing preview:** a camera at the POV part with 65/75/90° presets, showing only the LOD2 cockpit objects. The external equivalent is `walkerhr\cockpit_view.py`.
4. **Walker export:** an `animRate` helper that computes frames per metre from a marked stance foot's travel over the walk element.
5. **Converter note:** OgreXMLConverter 1.10 (`ogretools`) can segfault on exit after writing a valid binary, so check the output file rather than the exit code.

## Open questions (check in game)
- Do cockpit `21` bones need matching VDF parts? The stock walker has them; the Mk II's `iwh21cab` has none and relies on Ogre parenting under the taken-over `iwh11cab`.
- Is the walker's first-person view pitch taken from the POV part's parent, or applied by the camera code? The Mk II's POV hangs from the non-pitching cab.
