from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class MeshVersion(str, Enum):
    V_1_10 = "MeshSerializer_v1.100"


@dataclass(slots=True)
class VertexElement:
    semantic: str
    source: int
    offset: int
    component_type: str
    index: int = 0


@dataclass(slots=True)
class VertexBuffer:
    bind_index: int
    vertex_size: int
    data: bytes


@dataclass(slots=True)
class GeometryData:
    vertex_count: int
    declaration: list[VertexElement] = field(default_factory=list)
    buffers: list[VertexBuffer] = field(default_factory=list)


@dataclass(slots=True)
class BoneAssignment:
    vertex_index: int
    bone_index: int
    weight: float


@dataclass(slots=True)
class PoseVertex:
    vertex_index: int
    offset: tuple[float, float, float]
    normal: tuple[float, float, float] | None = None


@dataclass(slots=True)
class PoseData:
    name: str
    # Ogre pose targets use 0 for shared geometry and submesh_index + 1 for
    # dedicated submesh geometry.
    target: int
    vertices: list[PoseVertex] = field(default_factory=list)

    @property
    def includes_normals(self) -> bool:
        return any(vertex.normal is not None for vertex in self.vertices)


@dataclass(slots=True)
class SubMeshData:
    material_name: str
    indices: list[int]
    name: str = ""
    use_shared_vertices: bool = False
    operation_type: int = 4  # Ogre::RenderOperation::OT_TRIANGLE_LIST
    geometry: GeometryData | None = None
    bone_assignments: list[BoneAssignment] = field(default_factory=list)


@dataclass(slots=True)
class MeshBounds:
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]
    radius: float


@dataclass(slots=True)
class MeshData:
    submeshes: list[SubMeshData] = field(default_factory=list)
    shared_geometry: GeometryData | None = None
    skeleton_name: str = ""
    bounds: MeshBounds | None = None
    bone_assignments: list[BoneAssignment] = field(default_factory=list)
    poses: list[PoseData] = field(default_factory=list)

    def iter_geometries(self) -> Iterable[GeometryData]:
        if self.shared_geometry is not None:
            yield self.shared_geometry
        for submesh in self.submeshes:
            if submesh.geometry is not None:
                yield submesh.geometry
