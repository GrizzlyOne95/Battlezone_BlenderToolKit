# Battlezone for Maya

Autodesk Maya plug-in that imports and exports static Battlezone models: legacy `.vdf` and `.sdf`
(with their `.geo` parts) and Redux Ogre `.mesh`.
It reuses the toolkit's own serializers (`bz98tools`), so the file formats are handled by the
same code as the Blender add-on.

**Status:** verified headless in **Maya 2027** (mayapy, Python 3.13) by `tests/maya_integration_test.py`
(plug-in load, import / export through the menu handlers, round trips of stock files). The Maya GUI has not
been run: the menu itself (needs a main window) and the file dialogs are untested. The Maya-free core
(`bz98maya/core`) is also covered by `tests/test_maya_core.py`.

## Requirements

- Maya 2027 is the tested version. Maya 2024+ (Python 3.10+) should work but is **untested**; the toolkit's
  Ogre serializer uses `dataclass(slots=True)`, so Maya 2022/2023 (Python 3.7/3.9) cannot work.
- numpy (ships with Maya 2027's Python).
- The toolkit checkout (this repository). The plug-in finds `bz98tools` next to `maya/`; set the
  `BZ98TOOLKIT_PATH` environment variable to the repository root if you install it elsewhere.

## Install

Either:

1. Add the repository's `maya` folder to `MAYA_MODULE_PATH` (the folder holds `bz98maya.mod`),
   restart Maya, then enable `bz98maya_plugin.py` in Windows > Settings/Preferences > Plug-in Manager; or
2. Load `maya/plug-ins/bz98maya_plugin.py` directly from the Plug-in Manager (Browse).

A **Battlezone** menu appears in the main menu bar.

## Usage

| Menu item | Does |
| --- | --- |
| Import VDF | Reads the `.vdf` and the `.geo` files beside it. Creates a group named after the file with one transform per part (parent hierarchy, pivots, LOD2/3 parts), locators for hardpoints / eyepoints / emitters, and lambert shading groups named after each face's texture (a `file` node is added when `<texture>.png/.tga/.dds/...` sits beside the VDF). |
| Export VDF | Writes the `.vdf` and one `.geo` per mesh part beside it. |
| Import SDF / Export SDF | Same as VDF for structures (`.sdf`). Keeps the structure header (name, type, LOD distances, ddr, death effect / sound) and each part's ddr / x / y / z / time. |
| Import GEO / Export GEO | One `.geo` as a single mesh part / the selected mesh as a `.geo` (header words and face defaults come from its attributes). |
| Import Ogre Mesh | One transform per submesh, material per submesh, normals and every UV set. Rigged meshes (skeleton link) are rejected. |
| Export Ogre Mesh | "Redux Mesh Only" with Blender's default options: the BZR `.mesh` and its `.material` (see Export modes below). |
| Export Options... | The export dialog: Blender's three modes, every option, presets. |
| Battlezone Panel | Opens the dockable authoring panel (below). |
| Validate Battlezone Scene | Runs Blender's validation on the selection / scene and prints the issues (the panel lists them with Select / Fix). |
| Create Vehicle GEO Set | Blender's starter set (body, POV, hardpoints, rotor, nacelle): meshes for the visible classes, locators for the invisible ones. |
| Select Mesh Parts / Select Non-Mesh Parts | Blender's "Select Battlezone Objects". |

Export uses the current selection (and its descendants); with nothing selected it exports the whole scene.

### Authoring attributes and the panel

Every per-part field Blender exposes is a typed extra attribute named `bz` + the Blender property name,
created on import (or by **Tag Selected as BZ Part** in the panel, which uses Blender's defaults):

- **GEO**: `bzGEOType` (enum: Blender's full GEO type list; a value outside the list is kept and shown as "Unknown"),
  `bzGEOFlags` (raw ObjectFlags).
- **Engine Object Flags**: `bzFlagKeepBounds`, `bzFlagViewRender`, `bzFlagDestroyedSeed`, `bzFlagLightAttached`,
  `bzCollisionClass` (enum), `bzObjFlagTeam`. For the bits they cover the checkboxes win over the raw int at export;
  every other bit of `bzGEOFlags` is preserved untouched.
- **Bounds**: `bzBoundsMode` (enum Auto / Preserve / Recalculate), `bzGenerateCollision`, `bzHasAuthoredBounds`, `bzGeoCenter`,
  `bzSphereRadius`, `bzBoxHalfHeight`. Export follows Blender's rules (imported parts default to Preserve).
- **Damage representation**: `bzDamageGeo1`..`3` (written as VGEO band records), `bzHasDamageVariants`.
- **Eyepoint / spinner / SDF**: `bzIsPOVHelper`, `bzIsSpinnerHelper`, `bzSpinnerTarget`, `bzSpinnerAxis`, `bzSpinnerSpeed`,
  `bzSDFDDR`, `bzSDFTarget`, `bzSDFTime`.
- **Experimental**: `bzUseRawVDFMatrix`, `bzRawVDFMatrix`, `bzGEOHeaderUnknown`, `bzGEOHeaderUnknown2` and the
  `bzGEOFace...Default` bytes written for every face of the part.

Model header fields (`bzName`, `bzVehicleSize`, `bzMass`, `bzStructureType`, `bzDefensive`, `bzLOD1`..`5`, SPCS / raw ints ...)
live on the group an import creates (or one the panel creates when you edit a header field). Raw chunks, VLOC entries and
damage records the model has no field for are kept on the group as `bzPreserved`; unknown part keys as `bzProps` (JSON).

The **Battlezone** panel (Battlezone menu) shows these fields for the selected transform with Blender's labels and
tooltips, the model header, Blender's GEO Type Reference, the validation results (select the target, fix a legacy name) and the tools.

Validation runs Blender's own `bz98tools/validation.py` on the scene, so checks and wording are the same (naming, hierarchy, LOD
counterparts, hardpoint suffixes, collision helpers, pivots / scale, eyepoint, bridge / floor, flags, bounds, emitters, materials ...).
Animation checks come with the animation tier. As in Blender, export validates first: errors block the export (Blender has no
override, so neither does the menu), warnings are reported. Hardpoint locators count as helper
empties (Blender authors them as meshes).

### Export modes (as in Blender)

The **Export Options...** dialog (and `bz98maya.export_dialog.new_session`) offers Blender's modes and option sets, read from
Blender's operators so labels, tooltips, enums and defaults match:

- **Legacy only**: `.vdf` / `.sdf` / `.geo` (+ the GEO files). Options: Skip GEO File Export, Face Plane Export
  (Current / Preserve / Recompute / DX fix, Blender's `face_plane_mode`).
- **Legacy + Redux** ("Also Create Redux Files"): the same, then the toolkit's own auto-port (`ogre_autoport`, unchanged) writes the
  Redux `.mesh` / `.skeleton` / `.material` / `.dds` next to the files or into the output folder. All porter options (name, suffix,
  flat colours, normal mode, bounds scale, ACT / config, only-once, dry run, skip unchanged, output folder, and the VDF-only
  headlight / person / turret / cockpit / scope options) are exposed. Redux `.dds` output needs Pillow, which Maya's Python does not
  ship (the porter then writes no `.dds`); plain `pip install pillow` into mayapy fixes that.
- **Redux only**: the BZR `.mesh` and `.material` without any legacy file. Options: tangents / binormals / zero tangents / tangent
  parity, apply transforms, batch (one file per selected object), export / overwrite materials, copy textures.

Presets: Blender's built-in presets plus user presets (JSON in `~/.bz98maya/presets/<kind>/`, or `$BZ98MAYA_PRESET_DIR`):
Apply, Save Current Settings, Delete.

The `.mesh` is Blender's pure static export: the per-loop arrays go through the toolkit's own `pose_compat.SubMeshData.set_vertex`,
`MeshData` and `OgreMeshSerializer`, so the vertex layout (BZR vertex-buffer organisation), vertex splitting, multi-UV, `1 - v`
flip, bounds and radius are the toolkit's. The `.material` text is a port of `OgreExport.xSaveMaterialData` (BZBase templates;
`_d` / `_n` / `_S` / `_e` .dds names derived from the texture); the tests compare it with Blender's own function. Colour and
texture come from the shader's colour (a texture on the colour makes Blender's default grey) and its file texture.

Differences from Blender, deliberate: tangents are a per-loop UV-derivative frame with Blender's sign convention (Blender uses
MikkTSpace); a Maya mesh with several materials becomes one submesh per material (Blender: one submesh per object, first material);
hierarchy transforms are baked from the world matrix; vertex colours are not exported; no skeleton, shape-key or animation export.

Texture import: `.mesh` import reads the sibling `.material` (diffuse colour, DiffuseMap file found beside the mesh). Legacy import
follows Blender's "Auto-load .map textures" (`ImportMapTextures`, `MapTextureDirectory`, `MapTextureZFS`: import folder, your folder,
then the stock ZFS archive) with the toolkit's own palette and decoder code, and loads the converted `.png` into the shader.

### Rigs and animation

- **Redux skeletons.** Importing a `.mesh` that links a `.skeleton` builds the joints (bind pose = the bone's local TRS, in Ogre
  space = Maya space), a `skinCluster` with the file's weights and the clips as keys on the joints. A `.skeleton` can also be
  imported / exported on its own (menu: Import / Export Ogre Skeleton).
- **Clips.** Each clip is keyed on its own stretch of the timeline in real seconds (the scene's frame rate decides the frame
  numbers), the next clip starting one second after the previous one ends; the clip table (name, start, length) is kept in
  `bzClips` on the root joint, so clip names and lengths survive a round trip. Ogre keys are offsets from the bind pose; Maya's
  `jointOrient * rotate` composition is the same thing, so keys are exact (stock sweep tolerance 1e-4).
- **Your own rig.** Every joint in the exported selection (or scene) is a bone; joints that did not come from an import get the
  next free id, the bind pose is the pose at the start of the playback range, and keys without a clip table export as one clip
  (`Take_001`). Imported joints keep their Ogre ids and names (`bzBoneId`, `bzBoneName`).
- **Export** follows Blender's influence rules: bone ids are renumbered 0..n-1, each vertex keeps its 3 heaviest influences and
  (with `renormalize_weights`, the default) they are scaled to sum to 1. `export_skeleton` / `export_animation` /
  `ExportAnimations` are in the export dialog; as in Blender, animation export is off by default for the Redux path.
  Meshes keep one submesh per material.
- **Legacy ANIM (VDF / SDF).** The slot table (index, start, length, loop, speed, geo mask) sits on the model group; the keys sit
  on the part transforms (`translate`, `rotate` as unrolled Euler with linear tangents, `scale` for the optional second
  translation track) at the file's integer frames. Import sets the playback range; export reads the key frames back. Animated scenes are exported at the start of the playback
  range: a keyed part's record matrix is its first key's pose (Maya shows that pose, so the stored record matrix of a stock file
  can differ slightly from the re-exported one). Animated
  parts should be direct children of exported parts (keys are the part's own local values).
- **Panel.** The *Animation Slots* section edits the slot table (add / duplicate / remove / up / down / presets), shows
  Blender's Animation Index Reference and runs Mirror Legacy Animation Keys (token matching such as `.L` -> `.R`, mirror axis,
  keys relative to the rest poses, as Blender's formula). The Animation validation checks now run (loop pose mismatch, slot
  length hints, animated parts with no slots, skinned meshes in legacy exports).
- **Load Stock Clips / Auto-map** exist as `core.anim.stock_clip_table` / `auto_map` (Blender's own alias matching, e.g. the stock
  idleEject / idleElect spelling). The rest of Blender's pilot-animation *patch* workflow (writing a patched stock skeleton from
  Actions) is not ported: it is Blender-Action centric and Maya clips are exported with the normal skeleton export.

### Authoring rules for VDF export

- Part names are at most 8 characters with the LOD digit (1-3) as the 4th character, for example
  `abc11bdy`. A LOD2/LOD3 part (`abc21bdy`) needs a LOD1 twin that differs only in the 4th and 5th characters.
  Parts that break this are skipped with a warning, except parts that carry the authoring attributes (imported, or tagged by you),
  which may have any name of up to 8 characters and are treated as LOD1.
- Hardpoints are locators (or transforms) tagged with a `bzGEOType` (70 weapon, 71 cannon, 72 rocket, 73 mortar, 74 special,
  75 flame, 76 smoke, 77 dust, 40 eyepoint, 38 headlight).
- `inner_col` / `outer_col` meshes are the VDF collision helpers: an import creates them from the COLP box, an export writes
  COLP from them (the parts' bounding box when they are missing).
- Texture names come from the shading group: the `bzMaterial` attribute, else the connected file texture's name,
  else the material name; truncated to 8 characters on VDF export.
- Intermediate organising groups are fine: each part's transform is written relative to its nearest exported ancestor.

### Conventions

Maya space is Ogre space (Y up, right handed). Battlezone's own axes differ by a Z mirror: BZ +Z (the nose) is -Z in Maya.
1 BZ unit = 1 Maya internal unit (centimetres). The full derivation is the comment at the top of
`bz98maya/core/axes.py`.

## Limitations

- Redux poses / shape keys are not imported or exported (a warning is shown); at most 3 influences per vertex are written (the
  engine's limit, as in Blender). Legacy ANIM keys are linear (no tangent data exists in the format). Stock clips whose keys run
  more than 0.25 s past their declared length cannot be placed on the timeline and are not round-tripped exactly. Faces that
  repeat a vertex cannot exist in Maya: a repeated neighbouring corner is collapsed (a quad `a b c c` becomes a triangle), other
  faces are dropped on import. Attribute values outside Blender's hard min / max are clamped, as Blender does. The `Armature` validation scope has no Maya counterpart.
- Not covered from Blender's panels: VLOC editing (entries are preserved verbatim), per-face GEO attributes (one default per part
  is kept instead), "Create Spinner Helper" / "Capture Raw VDF Matrix" / "Generate COL Boxes" operators.
- Part LOD comes from the file's band (stored as `lod` / `slot` in `bzProps`); names that disagree with it (some stock parts do) are
  kept as they are. SDF files that repeat the LOD1 records in the LOD2/3 bands are folded into one set of parts and written back
  with those bands empty.
- Spinner (type 15) parts keep their spin axis and speed but not an editable rotation; use the raw matrix for exact records.
- Face colour is written as 204,204,204; the face plane is recomputed from the winding (stock convention `d = -n.a`).
- Normals are applied as locked per-face-vertex normals.
- The winding / normal conventions were validated against stock GEOs and `.mesh` files outside the committed tests (no stock
  assets are committed); the tests only check self-consistency on synthetic data.
- GEO export keeps n-gons and warns about non-planar or concave faces; Ogre export triangulates a temporary copy in Maya.
- GUI only, not verified: docking of the workspace control, the Battlezone menu itself, file dialogs, widget tooltips, the
  selection-driven refresh, and the look and behaviour of the export dialog widgets. Headless Maya builds the panel and the dialog and
  drives their handlers (standalone controls hold no state, so values are fed to the handlers).
- A stock GEO has several vertices per position (one per UV / normal index); the exported GEO welds equal (position, normal, UV)
  vertices, so the porter may produce somewhat fewer Redux vertices for the same triangles.
- Not ported: pose data of the Redux export, the pilot-animation patch workflow, the OgreXMLConverter fallback, Blender's `--onlyonce`-style
  state outside the porter options.

## Tests

- `python tests/test_maya_core.py` (or `tests/run_tests.py`): Maya-free core, plain Python.
- `"C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe" tests/maya_integration_test.py`: real Maya, headless
  (about 1 minute). It skips cleanly under plain Python. Set `BZ98_STOCK_DIR` to a folder laid out as
  `vdf/<name>/<name>.vdf` (+ `.geo`), `sdf/<name>/<name>.sdf` (+ `.geo`) and `mesh/*.mesh` to also round-trip real
  stock files; without it only the fixture-based cases run. Stock assets are not committed.

## Layout

```
maya/
  bz98maya.mod
  plug-ins/bz98maya_plugin.py   menu + file dialogs
  bz98maya/
    maya_io.py                  neutral scene <-> Maya (API 2.0 + cmds), authoring tools
    attributes.py               authoring attributes <-> neutral props / model header
    panel.py                    dockable panel (controller + UI)
    menu.py                     menu items and their handlers
    rig.py                      joints, skinCluster, clips (Redux skeleton <-> Maya)
    legacy_anim.py              VDF / SDF ANIM keys on part transforms, key mirroring
    animation_tools.py          slot table operations, mirror tokens
    core/                       Maya-free: model, axes, toolkit lookup, schema, authoring, validate, vdf_io, sdf_io, ogre_io
```
