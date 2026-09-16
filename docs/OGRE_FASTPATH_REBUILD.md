# Pure-Python OGRE Fast Path Rebuild

This branch replaces the CPython-ABI-bound `kenshi_blender_tool` dependency for Ogre mesh/skeleton workflows with a Blender-independent Python implementation while retaining the old CPython 3.11 backend as a compatibility oracle and preferred backend where it is available.

## Architecture

Existing Blender collector code continues to use the historical `kenshi_blender_tool` API. `bz98tools.ogrefast` now resolves that import name to:

- the bundled native CPython extension when its ABI is compatible; or
- a pure-Python compatibility facade on newer Python/Blender runtimes.

Bones and animation channels continue to use the established collector semantics. The pure mesh collector extends that path where the compiled API was limiting, including preservation of multiple Ogre texture-coordinate sets.

## Implemented

- OGRE `MeshSerializer_v1.100` direct binary mesh output.
- Direct binary `.mesh` import.
- Triangle-list submeshes with 16-bit and 32-bit indices.
- Vertex declarations and buffers for positions, normals, multiple UV sets, colors, tangents and binormals.
- Legacy Blender/Ogre coordinate and UV conversion.
- Material/submesh names and mesh bounds.
- Vertex splitting/deduplication using all authored UV sets.
- Skeleton links and vertex bone assignments.
- Direct `.skeleton` read/write using the repository's existing Ogre skeleton serializer.
- Bone hierarchy/bind-pose import and export.
- Skeleton animation import/export for the normal no-scale-key path.
- Visual-keying/baked animation matrix export used by the pilot animation workflow.
- Shape-key/Ogre pose import and export with per-submesh pose targets.
- Batch Selected fast export.
- A unified `kenshi_blender_tool` compatibility facade so existing collectors can run unchanged on Blender 5.x/Python 3.13.
- Native-only PhysX collision cooking remains explicit and unsupported by the pure facade rather than silently emulated.

## Validation

Automated coverage currently includes:

- bpy-free semantic tests on Python 3.11, 3.12 and 3.13;
- Windows/Python 3.11 native-oracle checks using the bundled old Ogre backend;
- native-vs-pure static mesh semantic comparison;
- native animation transform oracle coverage;
- native/pure pose wire-format and cross-reader coverage;
- multi-UV wire-format/readback coverage where a vertex differs only in UV set 1, ensuring optimization cannot incorrectly merge it; and
- real Blender 5.2.2 / Python 3.13 smoke tests covering shape-key mesh round-trip, Batch Selected export, visual-keying `.skeleton` animation bake, the pilot-animation bake helper, and rigged two-UV fast-path export.

The current fully green matrix is GitHub Actions run #123.

## Representative real-asset acceptance profile

A real Battlezone pilot pair (`aspilo.mesh` + linked `aspilo.skeleton`) is now used as an external/manual acceptance fixture. The original files are intentionally not committed to this public repository.

The source pair exercises substantially more than the synthetic fixtures:

- `MeshSerializer_v1.100` mesh and `Serializer_v1.80` skeleton;
- 2 dedicated-geometry triangle-list submeshes (`asp11ctr` and `gunmesh`);
- 9,970 total mesh vertices;
- 45,681 indices;
- 14,604 vertex/bone assignments, with no vertex exceeding the existing three-weight exporter limit;
- 2 UV sets on each submesh;
- vertex colors and tangents;
- linked `aspilo.skeleton`;
- 71 bones and 67 parent relations; and
- 19 skeleton animations with no scale-key frames and no linked external animation sources.

Those characteristics all fall inside the proven pure fast-path feature set. `tests/blender52_real_asset_smoke.py` accepts an external `.mesh` and `.skeleton` pair, forbids the XML fallback, imports the pair in Blender 5.2, verifies the armature/weights/materials/two UV sets/actions, re-exports through the pure fast path, and requires the resulting binary pair to preserve two submeshes, two UV sets, 71 bones and all 19 animation names.

Example:

```text
python tests/blender52_real_asset_smoke.py /path/to/aspilo.mesh /path/to/aspilo.skeleton
```

## Intentional fallback / remaining gaps

The pure backend currently fails closed or leaves the legacy path in place for features that have not been proven safe:

- animation scale-key export/import;
- mesh animation chunks distinct from skeleton animation;
- linked external skeleton-animation sources;
- Ogre LOD generation and edge-list authoring;
- native PhysX collision serialization/cooking; and
- historical mesh versions beyond the BZR-targeted v1.10 path where behavior has not been validated.

## Final validation before merge

Format-level compatibility and Blender 5.2 fast-path execution are now covered. The remaining release gate is Battlezone 98 Redux itself: export the representative real pair with Blender 5.2, load it in BZR, and check geometry orientation, both material/UV channels, skinning, and all expected pilot animation playback before this branch becomes the default production path.
