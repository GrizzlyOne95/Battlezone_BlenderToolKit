# Battlezone for Maya

First-version Autodesk Maya plug-in that imports and exports static Battlezone models.
It reuses the toolkit's own serializers (`bz98tools`), so the file formats are handled by the
same code as the Blender add-on.

**Status: written without access to Maya. The Maya layer has never been run.** The Maya-free
core (`bz98maya/core`) is covered by `tests/test_maya_core.py`.

## Requirements

- Maya 2024 or newer (Python 3.10+; the toolkit's Ogre serializer uses `dataclass(slots=True)`).
  Maya 2022/2023 ship Python 3.7/3.9 and will not work.
- numpy (expected to be present in Maya 2024+ `mayapy`; not verified).
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
| Import Ogre Mesh | One transform per submesh, material per submesh, normals and every UV set. Rigged meshes (skeleton link) are rejected. |
| Export Ogre Mesh | Writes a `.mesh`: world transforms baked in, one submesh per mesh and material, normals, all UV sets. No `.material` file is written. |

Export uses the current selection (and its descendants); with nothing selected it exports the whole scene.

### Authoring rules for VDF export

- Part names are at most 8 characters with the LOD digit (1-3) as the 4th character, for example
  `abc11bdy`. A LOD2/LOD3 part (`abc21bdy`) needs a LOD1 twin that differs only in the 4th and 5th characters.
  Parts that break this are skipped with a warning.
- Hardpoints are locators (or transforms) with a `bzType` attribute (70 weapon, 71 cannon, 72 rocket, 73 mortar, 74 special,
  75 flame, 76 smoke, 77 dust, 40 eyepoint, 38 headlight). Imported parts carry `bzType` and `bzFlags` already.
- Texture names come from the shading group: the `bzMaterial` attribute, else the connected file texture's name,
  else the material name; truncated to 8 characters on VDF export.
- Intermediate organising groups are fine: each part's transform is written relative to its nearest exported ancestor.

### Conventions

Maya space is Ogre space (Y up, right handed). Battlezone's own axes differ by a Z mirror: BZ +Z (the nose) is -Z in Maya.
1 BZ unit = 1 Maya internal unit (centimetres). The full derivation is the comment at the top of
`bz98maya/core/axes.py`.

## Limitations

- Static only: no skeletons, skin weights or animation; no VDF ANIM data, damage-state variant bands, VLOC chunks or SDF.
- Spinner (type 15) parts keep their spin vector but not an editable rotation.
- Face colour, shade / translucency bytes and GEO header words are not preserved (written with the toolkit's defaults).
- Face plane and bounds are recomputed on export unless the part came from an import (`bzProps`).
- Ogre export drops vertex colours and tangents; `.material` files are not written.
- Normals are applied as locked per-face-vertex normals.
- The winding / normal conventions were validated against stock GEOs and `.mesh` files outside the committed tests (no stock assets are committed); the tests only check self-consistency on synthetic data.
- GEO export keeps n-gons and warns about non-planar or concave faces; Ogre export triangulates a temporary copy in Maya.

## Layout

```
maya/
  bz98maya.mod
  plug-ins/bz98maya_plugin.py   menu + file dialogs
  bz98maya/
    maya_io.py                  neutral scene <-> Maya (API 2.0 + cmds)
    core/                       Maya-free: model, axes, toolkit lookup, vdf_io, ogre_io
```
