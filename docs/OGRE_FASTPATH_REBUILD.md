# Pure-Python OGRE Fast Path Rebuild

This branch replaces the CPython-ABI-bound `kenshi_blender_tool` dependency for Ogre mesh/skeleton workflows with a Blender-independent Python implementation while retaining the old CPython 3.11 backend as a compatibility oracle and preferred backend where it is available.

## Architecture

Existing Blender collector code continues to use the historical `kenshi_blender_tool` API. `bz98tools.ogrefast` now resolves that import name to:

- the bundled native CPython extension when its ABI is compatible; or
- a pure-Python compatibility facade on newer Python/Blender runtimes.

This keeps one Blender-side mesh/skeleton collection path instead of maintaining separate native and pure exporters.

## Implemented

- OGRE `MeshSerializer_v1.100` direct binary mesh output.
- Direct binary `.mesh` import.
- Triangle-list submeshes with 16-bit and 32-bit indices.
- Vertex declarations and buffers for positions, normals, UVs, colors, tangents and binormals.
- Legacy Blender/Ogre coordinate and UV conversion.
- Material/submesh names and mesh bounds.
- Vertex splitting/deduplication compatible with the fast exporter workflow.
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
- native/pure pose wire-format and cross-reader coverage; and
- a real Blender 5.2.2 / Python 3.13 smoke test covering shape-key mesh round-trip, Batch Selected export, visual-keying `.skeleton` animation bake, and the pilot-animation bake helper.

## Intentional fallback / remaining gaps

The pure backend currently fails closed or leaves the legacy path in place for features that have not been proven safe:

- animation scale-key export/import;
- mesh animation chunks distinct from skeleton animation;
- linked external skeleton-animation sources;
- Ogre LOD generation and edge-list authoring;
- native PhysX collision serialization/cooking; and
- historical mesh versions beyond the BZR-targeted v1.10 path where behavior has not been validated.

## Final validation before merge

The remaining release gate is representative Battlezone 98 Redux runtime testing. Exported `.mesh`/`.skeleton` pairs should be loaded in BZR and checked for geometry orientation, materials, skinning, shape keys where applicable, and animated playback before this branch becomes the default production path.
