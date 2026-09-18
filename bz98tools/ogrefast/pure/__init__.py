"""Pure-Python OGRE mesh/skeleton serialization backend.

This package is intentionally independent of Blender and of the legacy
``kenshi_blender_tool`` CPython extension so it can run on newer Blender
Python runtimes.
"""

from .model import (
    BoneAssignment,
    GeometryData,
    MeshBounds,
    MeshData,
    MeshVersion,
    SubMeshData,
    VertexBuffer,
    VertexElement,
)

__all__ = [
    "BoneAssignment",
    "GeometryData",
    "MeshBounds",
    "MeshData",
    "MeshVersion",
    "SubMeshData",
    "VertexBuffer",
    "VertexElement",
]
