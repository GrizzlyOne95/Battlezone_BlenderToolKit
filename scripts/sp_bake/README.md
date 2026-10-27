# sp_bake: batch mesh-map bakes for BZR models in Substance 3D Painter

This tool bakes AO, curvature and thickness maps for every stock (or addon) Ogre `.mesh` without anyone touching Painter. You can use the maps for custom BZR shaders such as AO, edge wear, dirt, snow masks and translucency.

```powershell
.\run_bake.ps1 -Out D:\bzr_bake                 # all 240 stock models
.\run_bake.ps1 -Out D:\bzr_bake -Only avapc     # one model
.\run_bake.ps1 -Out D:\bzr_bake -Maps AO        # AO only
.\run_bake.ps1 -Out D:\bzr_bake -Preview        # + normal audit sheets (Blender)
.\run_bake.ps1 -Out D:\bzr_bake -FixNormals avapc,abbarr   # bake these with recomputed normals
```

The run resumes where it left off: meshes that already have `*_AO.png` are skipped unless you pass `-Rebake`.

## Pipeline

1. **`ogre_to_obj.py`** (plain Python + numpy + Pillow)
   - Reads `.mesh` files with the toolkit's `bzrmodelporter` reader. Skinned meshes are fine; they bake in bind pose.
   - Writes `obj/<mesh>.obj` with one `usemtl` per Ogre material, so Painter makes one texture set per material. V is flipped to OBJ convention.
   - Resolves each material's `NormalMap` alias from the `.material` files and writes it as `normal/<name>.png`. `flat_N` is skipped.
   - Records each material's diffuse size and bakes at that size, capped by `-MaxSize`.
   - Runs the normal audit (below).
   - Writes `manifest.json`.
2. **`normals_preview.py`** (Blender, optional `-Preview`)
   - Writes `normals_preview/<mesh>_normals.png`, 3 views per sheet.
   - Top row uses the stored normals, bottom row flat face normals.
3. **`bzr_bake_batch.py`** (Painter startup plugin; `run_bake.ps1` installs it)
   - Idle unless a job pointer exists.
   - For each mesh it:
     - creates a project with the DirectX normal format, because BZBase normal maps have green = -dh/drow;
     - assigns the BZR `_N` map as the texture set's Normal mesh map;
     - bakes the low poly as its own high poly;
     - exports, closes without saving and goes to the next mesh.
   - Painter quits when the job is done.

## Output (`<Out>\maps\<mesh>\`)

| file | content |
|---|---|
| `<material>_AO.png` | ambient occlusion, grey |
| `<material>_CURV.png` | curvature, 0.5 = flat. Built from the normal map when the material has one (picks up panel lines), otherwise from the mesh |
| `<material>_THICK.png` | thickness, grey |
| `<material>_ACT.png` | packed R = AO, G = curvature, B = thickness: one sampler for a shader |

Run logs:
- `bake_log.txt`
- `bake_results.json`
- `bake_parameters.json`: every Painter bake parameter name and default, dumped on the first mesh.

To tune parameters, put overrides in `bake_job.json` → `common` / `bakers.AO` (etc.) using those names, then rerun with `-SkipConvert -Rebake`. The plugin's own log flags unknown names.

Extra maps: `-Maps AO,Curvature,Thickness,Position,WorldSpaceNormal`.

## Normal audit (`normals_audit.csv`)

Stock BZR meshes use heavily smoothed vertex normals. On avtank, large flat faces carry normals bent 30–50°, and the `_N` maps are mostly flat detail baked against those normals. `fix_mesh_normals.py` / `fix_all.py` rebuild them hard-edged (60° split, corners kept where stored normals already disagree by more than 15°, splits under `--min-area 1e-4` of the bbox diagonal² merged back, which costs about +8% vertices). So "different from the face normal" is not in itself a bug, and the audit only fails a mesh automatically on objective errors:
- **degenerate**: zero, NaN or non-unit normals;
- **flipped**: corners whose normal points behind its own triangle;
- **skewed**: more than 75° off.

**Reversed winding** means a triangle whose winding disagrees with all three of its normals. These are counted per mesh and corrected in the OBJ by default, so Painter's raytrace sees the side the normals face. `--keep-winding` disables this.

Smoothing that looks wrong (for example avapc's smeared panels) is a judgement call. Check the `-Preview` sheets. For meshes you decide are wrong, pass `-FixNormals <mesh>` (angle-split smooth normals, `--smooth-deg 45`). This affects only the bake input; the game `.mesh` is not touched.

## Caveats

- **Overlapping or mirrored UV islands.** These share texels, so they get one AO value. Mirrored halves of a symmetric model are fine; unrelated overlaps are not.
- **Shared materials.** A material such as `avheadlight00` that is used by many meshes is baked once per mesh. Each output goes in that mesh's folder.
- **Normal maps and AO.** Painter does not document whether the Normal mesh map changes the AO bake. Curvature (from the normal map) definitely uses it.

## Derived normal maps (`derive_normals.py`)

Many buildings ship with `flat_N.dds`. `derive_normals.py --bake <bake dir> --assets <BZ_ASSETS> [--extra <addon>] [--only abbarr]` writes `<diffuse stem>_N.dds` (DXT1 + mips, BZBase green = DirectX) for each such material. It combines two sources:
- **Rounded edges.** `bevel_bake.py` runs a Cycles Bevel-shader bake of the mesh against itself, with radius `--bevel` × bbox diagonal.
- **Recessed paint detail.** With `--relief recess`, texels darker than a lower-envelope local level sink: painted notches, seams and holes. Bright decals (stars, stripes) and their outlines stay flat. `--detail` sets the strength. This is a heuristic, so dark grime sinks too.

`normalmap_preview.py` renders a lit sheet: the top row uses flat_N and the bottom row the derived map. Overlapping UVs share a texel, so `derive_report.json` lists the overlap per material.

## Shippable ACT maps (`act_pack.py`)

`python act_pack.py --bake <out> --out <dir> --materials <CR>/Materials` turns the per-mesh `<material>_ACT.png` bakes into one `<material>_ACT.dds` per material (512 px DXT1 with mips) plus `cr_act_neutral.dds` (255, 128, 255).

- **Coverage.** Coverage comes from rasterising the OBJ's UV triangles. Painter pads about 99% of the atlas, so the bake alone can't tell you which texels are real.
- **Sealed-in islands.** UV islands with mean AO under `--enclosed` (default 0.2) are geometry sealed inside the hull: cockpit interiors, buried headlights. They bake near black, which would kill ambient light in the cockpit view, so they are set to neutral.
- **Shared materials.** These take the per-texel median over their meshes.
- **Gutters.** Gutters are filled from the nearest covered texel.

Campaign Reimagined consumes them through `Tools/Apply-ActMaps.py` and the OpenShim `ACT_MAP_ENABLED` shader block.
