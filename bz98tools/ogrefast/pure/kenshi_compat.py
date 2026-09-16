from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Iterable

import numpy as np

from .model import (
    BoneAssignment as WireBoneAssignment,
    GeometryData as WireGeometryData,
    MeshBounds,
    MeshData as WireMeshData,
    MeshVersion as WireMeshVersion,
    SubMeshData as WireSubMeshData,
    VertexBuffer,
    VertexElement,
)
from .serializer import OgreMeshSerializer


class MeshVersion(IntEnum):
    V_Latest = 0
    V_1_10 = 1
    V_1_8 = 2
    V_1_7 = 3
    V_1_4 = 4
    V_1_0 = 5
    V_Legacy = 6


class SkeletonVersion(IntEnum):
    V_1_0 = 0
    V_1_8 = 1
    V_Latest = 100


class OperationType(IntEnum):
    triangle_list = 4


V_1_10 = MeshVersion.V_1_10
V_Latest = MeshVersion.V_Latest
V_Legacy = MeshVersion.V_Legacy
triangle_list = OperationType.triangle_list


@dataclass
class BoneAssignmentData:
    vertex_index: int
    bone_index: int
    weight: float


@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class OgreQuaternion:
    w: float = 1.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Matrix3:
    def __init__(self, values=None, *args):
        self.values = values if values is not None else args


class BoneData:
    def __init__(
        self,
        bone_id=0,
        name="",
        position=None,
        rotate=None,
        scale=None,
        parent_name="",
        child_names=None,
    ):
        self.id = bone_id
        self.name = name
        self.position = position or Vector3()
        self.rotate = rotate or OgreQuaternion()
        self.scale = scale or Vector3(1.0, 1.0, 1.0)
        self.parent_name = parent_name
        self.child_names = list(child_names or [])


class AnimationData:
    def __init__(self):
        self.name = ""
        self.length = 0.0

    def append_animation_track(self, *args, **kwargs):
        raise NotImplementedError("pure Python skeleton animation export is not implemented yet")

    def set_animation_tracks(self, *args, **kwargs):
        raise NotImplementedError("pure Python skeleton animation export is not implemented yet")


class SkeletonData:
    def __init__(self, file="", group="General"):
        self.file = file
        self.group = group
        self._bones = []
        self._animations = []

    def get_bones(self, has_helper=True):
        return list(self._bones)

    def set_bones(self, bones):
        self._bones = list(bones)

    def add_animation(self, animation):
        self._animations.append(animation)


class GeometryData:
    """Compatibility view exposing the properties used by the Blender adapter."""

    def __init__(self, wire: WireGeometryData | None = None):
        self._wire = wire

    @property
    def vertex_count(self):
        return self._wire.vertex_count if self._wire else 0

    @property
    def has_positions(self):
        return self._has("POSITION")

    @property
    def has_normals(self):
        return self._has("NORMAL")

    @property
    def has_texture_coord(self):
        return self._has("TEXCOORD") or self._has("TEXTURE_COORDINATES")

    @property
    def has_tangents(self):
        return self._has("TANGENT")

    @property
    def has_binormals(self):
        return self._has("BINORMAL")

    @property
    def has_colors(self):
        return self._has("DIFFUSE")

    @property
    def has_shared_geometry(self):
        return False

    @property
    def tangent_dimensions(self):
        if not self._wire:
            return 0
        for element in self._wire.declaration:
            if element.semantic.upper() == "TANGENT":
                return 4 if element.component_type.upper() == "FLOAT4" else 3
        return 0

    @property
    def texcoords_size(self):
        if not self._wire:
            return 0
        return sum(
            1
            for element in self._wire.declaration
            if element.semantic.upper() in {"TEXCOORD", "TEXTURE_COORDINATES"}
        )

    def _has(self, semantic: str) -> bool:
        if not self._wire:
            return False
        semantic = semantic.upper()
        return any(element.semantic.upper() == semantic for element in self._wire.declaration)


class SubMeshData:
    def __init__(self):
        self.index = 0
        self.submesh_name = ""
        self.material = ""
        self.operation_type = OperationType.triangle_list
        self.use_shared_vertices = False
        self.use_32bit_indexes = False
        self.faces: list[int] = []
        self.face_count = 0
        self.boneassignments: list[BoneAssignmentData] = []
        self._source_vertex_indices = np.empty(0, dtype=np.int32)
        self._wire_geometry: WireGeometryData | None = None
        self._wire_bone_assignments: list[WireBoneAssignment] = []
        self._positions = np.empty((0, 3), dtype=np.float32)

    @property
    def geometry(self):
        return GeometryData(self._wire_geometry)

    @property
    def encorded_material(self):
        return self.material.encode("utf-8")

    @property
    def encorded_name(self):
        return self.submesh_name.encode("utf-8")

    def set_vertex(
        self,
        nd_vert_indices,
        nd_loop_indices,
        nd_positions,
        nd_normals,
        nd_tangents,
        nd_bitangent_signs,
        nd_bitangents,
        nd_texcoords,
        nd_colors,
        nd_alphas,
        tangent_dimensions=3,
        optimize=False,
    ):
        source_indices = np.asarray(nd_vert_indices, dtype=np.int32).reshape(-1)
        loop_indices = np.asarray(nd_loop_indices, dtype=np.int32).reshape(-1)
        source_positions = np.asarray(nd_positions, dtype=np.float32).reshape(-1, 3)
        loop_count = source_indices.size
        if loop_indices.size != loop_count:
            raise ValueError("loop index and vertex index counts differ")
        if loop_count % 3:
            raise ValueError("triangle-list export requires a loop count divisible by three")
        if source_indices.size and (
            int(source_indices.min()) < 0 or int(source_indices.max()) >= len(source_positions)
        ):
            raise ValueError("source vertex index is outside the position array")

        positions = source_positions[source_indices]
        normals = _loop_array(nd_normals, loop_count, 3)
        texcoords = _loop_array(nd_texcoords, loop_count, 2)
        tangents = _loop_array(nd_tangents, loop_count, 3)
        bitangents = _loop_array(nd_bitangents, loop_count, 3)
        tangent_signs = _flat_loop_array(nd_bitangent_signs, loop_count)
        colors = _loop_array(nd_colors, loop_count, 4)
        alphas = _loop_array(nd_alphas, loop_count, 4)

        exported_rows: list[tuple] = []
        exported_sources: list[int] = []
        faces: list[int] = []
        lookup: dict[bytes, int] = {}

        for loop_i in range(loop_count):
            source_i = int(source_indices[loop_i])
            row = [positions[loop_i]]
            if normals is not None:
                row.append(normals[loop_i])
            if colors is not None:
                rgb = colors[loop_i][:3]
                alpha = (
                    float(alphas[loop_i][0])
                    if alphas is not None
                    else float(colors[loop_i][3])
                )
                row.append(np.asarray([rgb[0], rgb[1], rgb[2], alpha], dtype=np.float32))
            if texcoords is not None:
                row.append(texcoords[loop_i])
            if tangents is not None:
                if tangent_dimensions == 4 and tangent_signs is not None:
                    row.append(
                        np.asarray(
                            [
                                tangents[loop_i][0],
                                tangents[loop_i][1],
                                tangents[loop_i][2],
                                tangent_signs[loop_i],
                            ],
                            dtype=np.float32,
                        )
                    )
                else:
                    row.append(tangents[loop_i])
            if bitangents is not None:
                row.append(bitangents[loop_i])

            key = source_i.to_bytes(4, "little", signed=True) + b"".join(
                np.asarray(component, dtype="<f4").tobytes() for component in row
            )
            if optimize and key in lookup:
                out_index = lookup[key]
            else:
                out_index = len(exported_rows)
                exported_rows.append(tuple(row))
                exported_sources.append(source_i)
                if optimize:
                    lookup[key] = out_index
            faces.append(out_index)

        self.faces = faces
        self.face_count = len(faces) // 3
        self.use_32bit_indexes = bool(faces and max(faces) > 0xFFFF)
        self._source_vertex_indices = np.asarray(exported_sources, dtype=np.int32)
        self._positions = np.asarray(
            [row[0] for row in exported_rows], dtype=np.float32
        ).reshape(-1, 3)
        self._wire_geometry = _build_geometry(
            exported_rows,
            has_normals=normals is not None,
            has_colors=colors is not None,
            has_texcoords=texcoords is not None,
            has_tangents=tangents is not None,
            tangent_dimensions=tangent_dimensions,
            has_binormals=bitangents is not None,
        )
        return self._source_vertex_indices.copy()

    def set_bone_assignments(self, assignments, out_nd_indices):
        self.boneassignments = list(assignments)
        source_map = np.asarray(out_nd_indices, dtype=np.int32).reshape(-1)
        by_source: dict[int, list[BoneAssignmentData]] = {}
        for assignment in assignments:
            by_source.setdefault(int(assignment.vertex_index), []).append(assignment)
        wire = []
        for exported_i, source_i in enumerate(source_map):
            for assignment in by_source.get(int(source_i), ()):
                wire.append(
                    WireBoneAssignment(
                        exported_i, int(assignment.bone_index), float(assignment.weight)
                    )
                )
        self._wire_bone_assignments = wire

    def append_shapekey(self, *args, **kwargs):
        raise NotImplementedError("pure Python pose export is not implemented yet")

    def to_wire(self) -> WireSubMeshData:
        if self._wire_geometry is None:
            raise ValueError("submesh has no vertex geometry")
        return WireSubMeshData(
            material_name=self.material,
            indices=list(self.faces),
            name=self.submesh_name,
            use_shared_vertices=False,
            operation_type=int(self.operation_type),
            geometry=self._wire_geometry,
            bone_assignments=list(self._wire_bone_assignments),
        )


class MeshData:
    def __init__(self, file="", group="General"):
        self.file = file
        self.group = group
        self._submeshes: list[SubMeshData] = []
        self._bone_name_to_id: dict[str, int] = {}
        self._linked_skeleton_name = ""

    def set_submeshes(self, submeshes):
        self._submeshes = list(submeshes)

    def get_submeshes(self):
        return list(self._submeshes)

    def set_bone_mapping(self, bones):
        if isinstance(bones, dict):
            self._bone_name_to_id = {str(name): int(bone_id) for bone_id, name in bones.items()}
        else:
            self._bone_name_to_id = {bone.name: int(bone.id) for bone in bones}

    def get_bone_id(self, bone_name):
        return self._bone_name_to_id.get(bone_name, 65535)

    def set_linked_skeleton_name(self, filename):
        self._linked_skeleton_name = str(filename)

    def get_linked_skeleton_name(self):
        return self._linked_skeleton_name

    def to_wire(self) -> WireMeshData:
        submeshes = [submesh.to_wire() for submesh in self._submeshes]
        all_positions = [
            submesh._positions
            for submesh in self._submeshes
            if submesh._positions.size
        ]
        bounds = None
        if all_positions:
            positions = np.concatenate(all_positions, axis=0)
            minimum = tuple(float(v) for v in positions.min(axis=0))
            maximum = tuple(float(v) for v in positions.max(axis=0))
            radius = float(np.sqrt(np.sum(positions * positions, axis=1)).max())
            bounds = MeshBounds(minimum, maximum, radius)
        return WireMeshData(
            submeshes=submeshes,
            skeleton_name=self._linked_skeleton_name,
            bounds=bounds,
        )


class KenshiObjectSerializer:
    is_pure_python = True

    def __init__(self, logfile="Kenshi_io_OGRE.log"):
        self.logfile = logfile

    def create_mesh(self, filename):
        return MeshData(filename, "General")

    def create_skeleton(self, filename):
        return SkeletonData(filename, "General")

    def save_mesh(self, mesh, path, version=MeshVersion.V_Latest):
        if version not in {MeshVersion.V_Latest, MeshVersion.V_1_10}:
            raise NotImplementedError(
                f"pure Python backend currently writes only MeshSerializer_v1.100, got {version!r}"
            )
        OgreMeshSerializer().dump(mesh.to_wire(), Path(path), WireMeshVersion.V_1_10)

    def save_skeleton(self, *args, **kwargs):
        raise NotImplementedError("pure Python skeleton serialization is not implemented yet")


def _loop_array(value, loop_count: int, width: int):
    array = np.asarray(value, dtype=np.float32)
    if array.ndim != 2 or array.shape != (loop_count, width):
        return None
    return array


def _flat_loop_array(value, loop_count: int):
    array = np.asarray(value, dtype=np.float32).reshape(-1)
    return array if array.size == loop_count else None


def _pack_colour_abgr(rgba) -> bytes:
    rgba = np.clip(np.asarray(rgba, dtype=np.float32), 0.0, 1.0)
    r, g, b, a = (int(round(float(channel) * 255.0)) for channel in rgba)
    # OGRE's VET_COLOUR_ABGR integer is 0xAABBGGRR. In little-endian files
    # this is physically stored as R, G, B, A bytes.
    return bytes((r, g, b, a))


def _build_geometry(
    rows: Iterable[tuple],
    *,
    has_normals: bool,
    has_colors: bool,
    has_texcoords: bool,
    has_tangents: bool,
    tangent_dimensions: int,
    has_binormals: bool,
) -> WireGeometryData:
    rows = list(rows)
    declaration = []
    offset = 0

    declaration.append(VertexElement("POSITION", 0, offset, "FLOAT3"))
    offset += 12
    if has_normals:
        declaration.append(VertexElement("NORMAL", 0, offset, "FLOAT3"))
        offset += 12
    if has_colors:
        declaration.append(VertexElement("DIFFUSE", 0, offset, "COLOUR_ABGR"))
        offset += 4
    if has_texcoords:
        declaration.append(VertexElement("TEXCOORD", 0, offset, "FLOAT2"))
        offset += 8
    if has_tangents:
        tangent_type = "FLOAT4" if tangent_dimensions == 4 else "FLOAT3"
        declaration.append(VertexElement("TANGENT", 0, offset, tangent_type))
        offset += 16 if tangent_dimensions == 4 else 12
    if has_binormals:
        declaration.append(VertexElement("BINORMAL", 0, offset, "FLOAT3"))
        offset += 12

    raw = bytearray()
    for row in rows:
        component_i = 0
        raw.extend(np.asarray(row[component_i], dtype="<f4").tobytes())
        component_i += 1
        if has_normals:
            raw.extend(np.asarray(row[component_i], dtype="<f4").tobytes())
            component_i += 1
        if has_colors:
            raw.extend(_pack_colour_abgr(row[component_i]))
            component_i += 1
        if has_texcoords:
            raw.extend(np.asarray(row[component_i], dtype="<f4").tobytes())
            component_i += 1
        if has_tangents:
            raw.extend(np.asarray(row[component_i], dtype="<f4").tobytes())
            component_i += 1
        if has_binormals:
            raw.extend(np.asarray(row[component_i], dtype="<f4").tobytes())

    return WireGeometryData(
        vertex_count=len(rows),
        declaration=declaration,
        buffers=[VertexBuffer(0, offset, bytes(raw))],
    )


__all__ = [
    "AnimationData",
    "BoneAssignmentData",
    "BoneData",
    "GeometryData",
    "KenshiObjectSerializer",
    "Matrix3",
    "MeshData",
    "MeshVersion",
    "OgreQuaternion",
    "OperationType",
    "SkeletonData",
    "SkeletonVersion",
    "SubMeshData",
    "V_1_10",
    "V_Latest",
    "V_Legacy",
    "Vector3",
    "triangle_list",
]
