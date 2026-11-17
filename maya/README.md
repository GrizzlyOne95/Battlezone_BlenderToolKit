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
| Export Ogre Mesh | Writes a `.mesh`: world transforms baked in, one submesh per mesh and material, normals, all UV sets. No `.material` file is written. |
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
Animation checks come with the animation tier. Export validates first and warns, it never blocks. Hardpoint locators count as helper
empties (Blender authors them as meshes).

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

- Static only: no skeletons, skin weights or animation; the ANIM block and the animation checks are a later tier.
- Not covered from Blender's panels: VLOC editing (entries are preserved verbatim), per-face GEO attributes (one default per part
  is kept instead), "Create Spinner Helper" / "Capture Raw VDF Matrix" / "Generate COL Boxes" operators.
- Part LOD comes from the file's band (stored as `lod` / `slot` in `bzProps`); names that disagree with it (some stock parts do) are
  kept as they are. SDF files that repeat the LOD1 records in the LOD2/3 bands are folded into one set of parts and written back
  with those bands empty.
- Spinner (type 15) parts keep their spin axis and speed but not an editable rotation; use the raw matrix for exact records.
- Face colour is written as 204,204,204; the face plane is recomputed from the winding (stock convention `d = -n.a`).
- Ogre export drops vertex colours and tangents; `.material` files are not written.
- Normals are applied as locked per-face-vertex normals.
- The winding / normal conventions were validated against stock GEOs and `.mesh` files outside the committed tests (no stock
  assets are committed); the tests only check self-consistency on synthetic data.
- GEO export keeps n-gons and warns about non-planar or concave faces; Ogre export triangulates a temporary copy in Maya.
- GUI only, not verified: docking of the workspace control, the Battlezone menu itself, file dialogs, widget tooltips and the
  selection-driven refresh. Headless Maya builds the panel and drives its change handlers.

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
    core/                       Maya-free: model, axes, toolkit lookup, schema, authoring, validate, vdf_io, sdf_io, ogre_io
```
