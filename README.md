# Battlezone Blender Toolkit

Full Blender import/export and authoring suite for classic **Battlezone (1998)**, **The Red Odyssey**, and **Battlezone 98 Redux** workflows.

The Battlezone Blender Toolkit works with classic Battlezone model formats (`.vdf`, `.sdf`, `.geo`, `.map`), Redux model formats (`.mesh`, `.skeleton`, `.material`), ZFS archives, and BZMapIO terrain workflows (`.hg2`).

It combines legacy model editing, Redux mesh export, terrain tools, validation, and workflow helpers into one Blender-based toolset. Redux-specific features are included where the later engine requires them, but the toolkit itself is not Redux-only.

## Install

1. Download the newest `Battlezone_BlenderToolKit-vX.Y.Z.zip` from [Releases](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/releases).
2. In Blender, open `Edit > Preferences > Add-ons`.
3. Click `Install...` or `Install From Disk`.
4. Select the downloaded zip.
5. Enable `Battlezone Blender Toolkit`.
6. Restart Blender when replacing an already-loaded version.

The release archive intentionally contains the internal add-on folder `bz98tools/`. That package name is retained for Blender-install and import compatibility even though the public product name is **Battlezone Blender Toolkit**.

## Supported Blender Versions

- Blender 4.5 LTS remains supported for the established legacy and Redux workflows.
- Blender 5.2.2 is supported for the rebuilt pure-Python Redux mesh/skeleton fast path.
- On compatible Windows/CPython 3.11 runtimes, the historical native Ogre backend remains available as a fast-path implementation and compatibility oracle.
- Blender 5.2.2 no longer requires the CPython-3.11-only native Ogre extension for normal Redux mesh/skeleton import and export.

## Main Features

- Import and export legacy `.geo`, `.vdf`, and `.sdf` files.
- Import models directly from `.zfs` archives with dependency extraction.
- Export Redux `.mesh`, `.skeleton`, and `.material` output.
- Use explicit Legacy Only, Legacy + Redux, and Redux Mesh Only workflows.
- Import/export `.hg2` terrain through integrated BZMapIO map tools.
- Paint stock and custom world terrain atlases.
- Convert `.map` textures to `.png` on import and prepare `.dds` output on export.
- Validate legacy model naming, hierarchy, hardpoints, LODs, pivots, collision helpers, and animation setup.
- Author advanced engine semantics directly: part classes, ObjectFlags bit fields, VLOC part injection, damage-representation bands, authored bounds, eyepoints, and bridge/floor decks - with unknown data preserved on round trips.
- Generate cockpit GEOs from selected LOD1 faces.
- Create organic Redux skins from legacy GEO control pivots.
- Use advanced VDF helpers for spinners and raw transform experiments.

## Documentation

Use the wiki for workflows and modeling reference:

- [Getting Started](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Getting-Started)
- [Importing Models and Stock MAP Textures](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Importing-Models-and-Stock-MAP-Textures)
- [Legacy VDF SDF GEO Modeling Guide](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Legacy-VDF-SDF-GEO-Modeling-Guide)
- [Validation Checks](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Validation-Checks)
- [Animation Tools and Animation Slots](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Animation-Tools-and-Animation-Slots)
- [Map Tools and Custom World Terrain Painting](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Map-Tools-and-Custom-World-Terrain-Painting)
- [Redux Mesh Skeleton Export](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Redux-Mesh-Skeleton-Export)
- [Cockpit GEO Workflow](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Cockpit-GEO-Workflow)
- [Organic Redux Skin Walkthrough](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Organic-Redux-Skin-Walkthrough)
- [Collision Helpers and SDF Collision Data](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Collision-Helpers-and-SDF-Collision-Data)
- [Advanced VDF Editing Spinners and Raw Transforms](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Advanced-VDF-Editing-Spinners-and-Raw-Transforms)
- [Troubleshooting](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/wiki/Troubleshooting)

Versioned technical notes stay in the repository:

- [Release notes](docs/)
- [Advanced GEO/VDF semantic authoring](docs/ADVANCED_AUTHORING.md)
- [Runtime test kit](docs/RUNTIME_TEST_KIT.md)
- [Experimental binary fields](docs/EXPERIMENTAL_BINARY_FIELDS.md)
- [Organic Redux Skin source walkthrough copy](docs/ORGANIC_REDUX_SKIN_WALKTHROUGH.md)

## Latest Highlights

- `v1.5.0`: adds Blender 5.2.2 / Python 3.13 Redux mesh/skeleton support through a rebuilt pure-Python Ogre binary path, including multi-UV preservation, rigging/animation support, BZR-compatible vertex-buffer organization, and topology/custom-normal fidelity fixes.
- `v1.4.10`: added the runtime semantic-verification evidence kit and stricter reproducibility controls for advanced VDF/GEO validation.
- `v1.4.8`: added import texture source options for stock `.map` folders and ZFS archives.
- `v1.4.7`: fixed Animation Tools mirror dialog invocation so `Mirror From` and `Mirror To` selectors open from the sidebar.
- `v1.4.6`: fixed popup info buttons such as `What Gets Checked`.
- `v1.4.5`: added custom world atlas terrain painting, compact validation reports, expanded legacy guide validation, and removed unsupported Game Playback tooling.

See [Releases](https://github.com/GrizzlyOne95/Battlezone_BlenderToolKit/releases) and the `docs/RELEASE_NOTES_*.md` files for full version history.

## Credits

| Contributor / Project | Contribution |
| --- | --- |
| DivisionByZero | Original legacy model-to-Redux Python porting script |
| Business Lawyer | BZMapIO map tooling |
| Kindrad | Kenshi mesh add-on and Ogre import/export foundation |
| Commando950 | Original Blender VDF/SDF plugin |
| GrizzlyOne95 | Unified toolkit integration, additional features, bug fixes, Blender API upgrades, and workflow modernization |
| Lucius64 / kenshi_io_blender | Inspiration and reference for modern Kenshi/Ogre mesh workflows: [github.com/Lucius64/kenshi_io_blender](https://github.com/Lucius64/kenshi_io_blender/tree/main) |

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).

Portions of the importer/exporter system are derived from [Kenshi_IO_Continued](https://codeberg.org/Kindrad/Kenshi_IO_Continued), which is also licensed under GPL-3.0.

The bundled native Ogre backend and related workflow ideas also draw from [Lucius64's kenshi_io_blender](https://github.com/Lucius64/kenshi_io_blender/tree/main).

OGRE conversion utilities are licensed under the MIT License. See `OGRE_LICENSE.txt`.

Other portions of code were derived from Commando950's plugin and DivisionByZero's porting code.
