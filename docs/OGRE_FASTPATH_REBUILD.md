# Pure-Python OGRE Fast Path Rebuild

This branch replaces the CPython-ABI-bound `kenshi_blender_tool` dependency with a Blender-independent Python serializer.

## Phase 1 scope

- OGRE `MeshSerializer_v1.100` output only.
- Static triangle-list meshes first.
- Geometry declaration + vertex buffers.
- 16-bit and 32-bit index buffers.
- Material names, submeshes, bounds, skeleton link and bone assignments.
- Existing XML/native backends remain available until the new writer is verified.

## Non-goals for the first milestone

- Skeleton binary serialization.
- Animation tracks and poses.
- Edge lists and LOD generation.
- Historical OGRE mesh versions other than v1.10.

## Validation strategy

Synthetic meshes are serialized through the new backend and compared structurally against OGRE's documented chunk layout and, where available, output from the existing CPython 3.11 native serializer. Runtime validation in Battlezone 98 Redux remains required before the backend becomes the default.
