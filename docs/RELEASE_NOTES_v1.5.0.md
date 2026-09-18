# Battlezone Blender Toolkit v1.5.0

## Summary

This release modernizes the Redux Ogre mesh/skeleton workflow for current Blender runtimes. Blender 5.2.2 / Python 3.13 can now use the rebuilt pure-Python Ogre fast path instead of depending on the CPython-3.11-only native `kenshi_blender_tool.pyd` for normal mesh and skeleton workflows.

The release also preserves the existing native CPython 3.11 backend as an oracle/fallback where it remains available.

## Added

- Added a pure-Python OGRE `MeshSerializer_v1.100` binary writer.
- Added direct binary `.mesh` import support.
- Added direct `.skeleton` read/write support.
- Added static and rigged submesh support, 16/32-bit indices, bounds, skeleton links, and vertex bone assignments.
- Added multiple UV-set preservation through import/export.
- Added vertex colors, tangents, binormals, poses/shape keys, bone hierarchy/bind pose, and normal no-scale skeleton animation support.
- Added Blender 5.2.2 / Python 3.13 smoke coverage for pure Ogre export, rigged multi-UV export, pose handling, batch export, and animation baking.
- Added Python 3.11 / 3.12 / 3.13 semantic test coverage.
- Added Windows native-Ogre oracle coverage to compare the rebuilt path with the historical backend.
- Added Ogre 1.11.6-compatible automatic vertex-buffer organization/repacking in pure Python.
- Added release gating that runs semantic tests, Blender 5.2.2 smoke tests, the native Ogre oracle, version validation, and package-layout validation before publishing.

## Changed

- Pure Ogre exports no longer require `OgreMeshUpgrader.exe` to obtain BZR-compatible vertex stream organization.
- Skeletally animated meshes now use Ogre's recommended stream split, separating position/normal data from color/UV/binormal/tangent data where required.
- Vertex data reorganization is byte-preserving by semantic + semantic index; UVs, normals, tangents, colors, and positions are not numerically altered by the stream repack itself.
- Ogre import now preserves source topology by default instead of automatically merging vertices.
- Custom split-normal remapping after optional cleanup now uses original face/loop indices rather than vertex indices.
- Pure export avoids an unnecessary BMesh triangulation pass for meshes that are already triangle lists, preserving imported custom split normals.
- Updated the public add-on identity and release packaging to **Battlezone Blender Toolkit** while retaining the internal `bz98tools` package name for compatibility.
- Bumped the add-on version to 1.5.0.

## Battlezone 98 Redux validation

The real `aspilo.mesh` / `aspilo.skeleton` pilot was used throughout development to recover compatibility details that synthetic fixtures did not expose.

Confirmed during runtime investigation:

- the rebuilt bind-pose/skeleton path restores pilot animation playback in BZR;
- stock mesh + stock material + rebuilt bind-fix skeleton renders and animates correctly in BZR;
- the apparent UV/material regression was traced to mesh round-trip topology/custom-normal corruption rather than the stock material file;
- the identified topology and split-normal causes are fixed in this release candidate.

A final clean stock -> Blender 5.2.2 -> pure export -> BZR round-trip remains the last manual acceptance check before tagging the public release.

## Intentional limitations

The pure backend fails closed or retains fallback behavior for features that are not yet separately validated:

- animation scale keys;
- mesh-animation chunks distinct from skeleton animation;
- linked external skeleton-animation sources;
- Ogre LOD generation and edge-list authoring;
- native PhysX collision serialization/cooking;
- historical Ogre mesh-version behavior outside the BZR-targeted v1.10 path.

## Supported Blender versions

- Blender 4.5 LTS remains supported.
- Blender 5.2.2 is supported for the rebuilt pure-Python Redux mesh/skeleton workflow.
- On compatible CPython 3.11 Windows runtimes, the historical native Ogre backend remains available and is retained as an independent compatibility oracle.

## Installation

1. Download `Battlezone_BlenderToolKit-v1.5.0.zip` from the release.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install From Disk** and select the downloaded archive.
4. Enable **Battlezone Blender Toolkit**.
5. Restart Blender when replacing an already-loaded version.

**Full changes:** compare `v1.4.10...v1.5.0` after the release tag is created.
