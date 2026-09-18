from __future__ import annotations

"""Pure-Python binary mesh import compatibility layer.

This module adapts the repository's existing bzrmodelporter Ogre reader onto
just enough of the legacy kenshi_blender_tool API for ogrefast/ogre_importer.py
to consume ordinary static meshes without the CPython-specific extension.
"""

from pathlib import Path
from typing import Iterable

import numpy as np

from ...bzrmodelporter.ogremesh import OT, VES, VET
from ...bzrmodelporter.ogremesh_serializer import MeshSerializer as LegacyMeshSerializer
from .kenshi_compat import (
    KenshiObjectSerializer as ExportKenshiObjectSerializer,
    MeshData,
    OperationType,
    SubMeshData,
)
from .model import GeometryData as WireGeometryData, VertexBuffer, VertexElement


class UnsupportedPureImport(RuntimeError):
    """Raised when a mesh contains data the static pure importer cannot preserve."""


class _DetectingMeshSerializer(LegacyMeshSerializer):
    """Record skipped feature chunks so the adapter never loses them silently."""

    def read_poses(self, mesh):
        mesh._pure_import_contains_poses = True
        return super().read_poses(mesh)

    def read_animations(self, mesh):
        mesh._pure_import_contains_animations = True
        return super().read_animations(mesh)


class ImportedSubMeshData(SubMeshData):
    def __init__(self):
        super().__init__()
        self._bone_mapping: dict[int, str] = {}

    def set_bone_mapping(self, bones):
        if isinstance(bones, dict):
            self._bone_mapping = {int(key): str(value) for key, value in bones.items()}
        else:
            self._bone_mapping = {int(bone.id): str(bone.name) for bone in bones}

    def _expand_to_corners(self, values: np.ndarray) -> np.ndarray:
        """Expand vertex-domain attributes through the triangle index buffer.

        The historical native wrapper exposes normals, UVs and colours in the
        order Blender's CORNER/loop domain expects, while positions remain
        vertex-domain.  Real Ogre meshes routinely reuse vertices across many
        triangle loops, so returning raw vertex-buffer rows is insufficient.
        """

        values = np.asarray(values)
        indices = np.asarray(self.faces, dtype=np.int64).reshape(-1)
        if indices.size == 0:
            return np.empty((0, values.shape[1]), dtype=values.dtype)
        if int(indices.min()) < 0 or int(indices.max()) >= len(values):
            raise UnsupportedPureImport(
                "submesh index buffer references a vertex outside its geometry"
            )
        return values[indices]

    def get_positions(self):
        values = _decode_semantic(self._wire_geometry, {"POSITION"})
        if values is None:
            return np.empty(0, dtype=np.float32)
        return _ogre_to_blender_xyz(values[:, :3]).astype(np.float32, copy=False).reshape(-1)

    def get_normals(self):
        values = _decode_semantic(self._wire_geometry, {"NORMAL"})
        if values is None:
            return []
        values = _ogre_to_blender_xyz(values[:, :3]).astype(np.float32, copy=False)
        return self._expand_to_corners(values).tolist()

    def get_texcoords(self):
        elements = _elements_by_semantic(
            self._wire_geometry, {"TEXCOORD", "TEXTURE_COORDINATES"}
        )
        if not elements:
            return np.empty((0, 0), dtype=np.float32)

        uv_sets = []
        for element in sorted(elements, key=lambda item: item.index):
            values = _decode_element(self._wire_geometry, element)
            if values.shape[1] < 2:
                raise UnsupportedPureImport("texture coordinate element has fewer than 2 components")
            uv = values[:, :2].astype(np.float32, copy=True)
            uv[:, 1] = 1.0 - uv[:, 1]
            uv_sets.append(self._expand_to_corners(uv).reshape(-1))
        return np.stack(uv_sets, axis=0)

    def get_colors(self, is_rgba=True):
        element = _first_element(self._wire_geometry, {"DIFFUSE", "COLOUR", "COLOR"})
        if element is None:
            empty = np.empty((0, 4), dtype=np.float32)
            return empty, empty

        rgba = self._expand_to_corners(
            _decode_colour_element(self._wire_geometry, element)
        ).astype(np.float32, copy=False)
        alpha = (
            np.repeat(rgba[:, 3:4], 4, axis=1)
            if rgba.shape[1] >= 4
            else np.ones((len(rgba), 4), dtype=np.float32)
        )
        colors = rgba if is_rgba else rgba[:, :3]
        return colors, alpha

    def get_vertex_groups(self):
        # Static pure import rejects bone assignments before reaching Blender.
        return []

    def get_shapekeys(self):
        # Pose chunks are rejected before reaching Blender.
        return []


class ImportedMeshData(MeshData):
    def get_linked_skeleton(self):
        return None


class KenshiObjectSerializer(ExportKenshiObjectSerializer):
    """Compatibility serializer that adds direct binary mesh loading."""

    def __init__(self, logfile="Kenshi_io_OGRE.log"):
        super().__init__(logfile=logfile)
        self._resource_locations: list[Path] = []

    def add_resource_location(self, folder):
        path = Path(folder).expanduser()
        if path not in self._resource_locations:
            self._resource_locations.append(path)

    def load_mesh(self, file):
        path = self._resolve_resource(file)
        with path.open("rb") as stream:
            mesh = _DetectingMeshSerializer(stream).read()
        return _convert_mesh(mesh, path.name)

    def _resolve_resource(self, file) -> Path:
        candidate = Path(file).expanduser()
        if candidate.is_file():
            return candidate
        for folder in self._resource_locations:
            candidate = folder / file
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(file)


def _convert_mesh(source, filename: str) -> ImportedMeshData:
    if getattr(source, "_pure_import_contains_poses", False):
        raise UnsupportedPureImport("mesh contains pose/shape-key data")
    if getattr(source, "_pure_import_contains_animations", False):
        raise UnsupportedPureImport("mesh contains mesh animation data")
    if source.skeleton_name:
        raise UnsupportedPureImport(
            f"mesh links skeleton {source.skeleton_name!r}; rigged import still uses the legacy/native path"
        )
    if source.get_bone_assignment_count():
        raise UnsupportedPureImport("mesh contains shared-geometry bone assignments")
    if any(submesh.get_bone_assignment_count() for submesh in source.submesh_list):
        raise UnsupportedPureImport("mesh contains submesh bone assignments")

    shared_geometry = (
        _wire_geometry_from_legacy(source.shared_vertex_data)
        if source.shared_vertex_data is not None
        else None
    )

    mesh = ImportedMeshData(filename, "General")
    submeshes = []
    for index, source_submesh in enumerate(source.submesh_list):
        if int(source_submesh.operation_type) != OT.TRIANGLE_LIST:
            raise UnsupportedPureImport(
                f"submesh {index} uses unsupported operation type {source_submesh.operation_type}"
            )

        geometry = shared_geometry if source_submesh.use_shared_vertices else (
            _wire_geometry_from_legacy(source_submesh.vertex_data)
            if source_submesh.vertex_data is not None
            else None
        )
        if geometry is None:
            raise UnsupportedPureImport(f"submesh {index} has no vertex geometry")

        indices = [int(value) for value in source_submesh.get_index_array().tolist()]
        if len(indices) % 3:
            raise UnsupportedPureImport(
                f"submesh {index} triangle-list index count is not divisible by three"
            )

        submesh = ImportedSubMeshData()
        submesh.index = index
        submesh.submesh_name = source_submesh.get_name() or f"SubMesh{index}"
        submesh.material = source_submesh.material_name or ""
        submesh.operation_type = OperationType.triangle_list
        submesh.use_shared_vertices = bool(source_submesh.use_shared_vertices)
        submesh.use_32bit_indexes = bool(source_submesh.indices_32_bit)
        submesh.faces = indices
        submesh.face_count = len(indices) // 3
        submesh._wire_geometry = geometry
        ogre_positions = _decode_semantic(geometry, {"POSITION"})
        if ogre_positions is not None:
            submesh._positions = ogre_positions[:, :3].astype(np.float32, copy=True)
        submeshes.append(submesh)

    mesh.set_submeshes(submeshes)
    return mesh


def _wire_geometry_from_legacy(vertex_data) -> WireGeometryData:
    declaration = []
    for element in vertex_data.vertex_declaration.vertex_element_list:
        declaration.append(
            VertexElement(
                semantic=_semantic_name(element.semantic),
                source=int(element.source),
                offset=int(element.offset),
                component_type=VET.label(element.type),
                index=int(element.index),
            )
        )

    buffers = []
    for bind_index, buffer in vertex_data.vertex_buffers():
        buffers.append(
            VertexBuffer(
                bind_index=int(bind_index),
                vertex_size=int(buffer.vertex_size),
                data=bytes(buffer.buffer),
            )
        )

    return WireGeometryData(
        vertex_count=int(vertex_data.vertex_count),
        declaration=declaration,
        buffers=buffers,
    )


def _semantic_name(value: int) -> str:
    if value == VES.COLOUR:
        return "DIFFUSE"
    if value == VES.COLOUR2:
        return "SPECULAR"
    if value == VES.TEXTURE_COORDINATES:
        return "TEXCOORD"
    return VES.label(value)


def _elements_by_semantic(geometry: WireGeometryData | None, names: set[str]):
    if geometry is None:
        return []
    names = {name.upper() for name in names}
    return [
        element
        for element in geometry.declaration
        if element.semantic.upper() in names
    ]


def _first_element(geometry: WireGeometryData | None, names: set[str]):
    elements = _elements_by_semantic(geometry, names)
    return elements[0] if elements else None


def _decode_semantic(geometry: WireGeometryData | None, names: set[str]):
    element = _first_element(geometry, names)
    return None if element is None else _decode_element(geometry, element)


def _decode_element(geometry: WireGeometryData, element: VertexElement) -> np.ndarray:
    buffer = next(
        (item for item in geometry.buffers if item.bind_index == element.source),
        None,
    )
    if buffer is None:
        raise UnsupportedPureImport(
            f"vertex declaration references missing buffer source {element.source}"
        )

    component_type = element.component_type.upper()
    scalar_info = _SCALAR_TYPES.get(component_type)
    if scalar_info is None:
        raise UnsupportedPureImport(
            f"unsupported vertex element type {element.component_type}"
        )
    dtype, width, normalized = scalar_info
    dtype = np.dtype(dtype)
    end = element.offset + dtype.itemsize * width
    if end > buffer.vertex_size:
        raise UnsupportedPureImport(
            f"vertex element {element.semantic} overruns its vertex stride"
        )

    values = np.ndarray(
        shape=(geometry.vertex_count, width),
        dtype=dtype,
        buffer=buffer.data,
        offset=element.offset,
        strides=(buffer.vertex_size, dtype.itemsize),
    ).copy()
    if normalized:
        if np.issubdtype(dtype, np.unsignedinteger):
            values = values.astype(np.float32) / float(np.iinfo(dtype).max)
        else:
            max_value = float(np.iinfo(dtype).max)
            values = np.maximum(values.astype(np.float32) / max_value, -1.0)
    return values


def _decode_colour_element(
    geometry: WireGeometryData, element: VertexElement
) -> np.ndarray:
    buffer = next(
        (item for item in geometry.buffers if item.bind_index == element.source),
        None,
    )
    if buffer is None:
        raise UnsupportedPureImport(
            f"colour declaration references missing buffer source {element.source}"
        )

    component_type = element.component_type.upper()
    if component_type in {"COLOUR", "COLOUR_ABGR", "COLOUR_ARGB", "UBYTE4", "UBYTE4_NORM"}:
        raw = np.ndarray(
            shape=(geometry.vertex_count, 4),
            dtype=np.uint8,
            buffer=buffer.data,
            offset=element.offset,
            strides=(buffer.vertex_size, 1),
        ).copy()
        # A serialized OGRE 1.x ABGR value is physically R,G,B,A on a
        # little-endian stream; ARGB is B,G,R,A.
        if component_type == "COLOUR_ARGB":
            raw = raw[:, [2, 1, 0, 3]]
        return raw.astype(np.float32) / 255.0

    values = _decode_element(geometry, element).astype(np.float32)
    if values.shape[1] < 4:
        alpha = np.ones((len(values), 1), dtype=np.float32)
        values = np.concatenate((values[:, :3], alpha), axis=1)
    return values[:, :4]


def _ogre_to_blender_xyz(value):
    array = np.asarray(value, dtype=np.float32).reshape(-1, 3)
    out = np.empty_like(array)
    out[:, 0] = array[:, 0]
    out[:, 1] = -array[:, 2]
    out[:, 2] = array[:, 1]
    return out


_SCALAR_TYPES = {
    "FLOAT1": ("<f4", 1, False),
    "FLOAT2": ("<f4", 2, False),
    "FLOAT3": ("<f4", 3, False),
    "FLOAT4": ("<f4", 4, False),
    "DOUBLE1": ("<f8", 1, False),
    "DOUBLE2": ("<f8", 2, False),
    "DOUBLE3": ("<f8", 3, False),
    "DOUBLE4": ("<f8", 4, False),
    "SHORT1": ("<i2", 1, False),
    "SHORT2": ("<i2", 2, False),
    "SHORT3": ("<i2", 3, False),
    "SHORT4": ("<i2", 4, False),
    "USHORT1": ("<u2", 1, False),
    "USHORT2": ("<u2", 2, False),
    "USHORT3": ("<u2", 3, False),
    "USHORT4": ("<u2", 4, False),
    "INT1": ("<i4", 1, False),
    "INT2": ("<i4", 2, False),
    "INT3": ("<i4", 3, False),
    "INT4": ("<i4", 4, False),
    "UINT1": ("<u4", 1, False),
    "UINT2": ("<u4", 2, False),
    "UINT3": ("<u4", 3, False),
    "UINT4": ("<u4", 4, False),
    "BYTE4": ("i1", 4, False),
    "UBYTE4": ("u1", 4, False),
    "BYTE4_NORM": ("i1", 4, True),
    "UBYTE4_NORM": ("u1", 4, True),
    "SHORT2_NORM": ("<i2", 2, True),
    "SHORT4_NORM": ("<i2", 4, True),
    "USHORT2_NORM": ("<u2", 2, True),
    "USHORT4_NORM": ("<u2", 4, True),
}


__all__ = [
    "ImportedMeshData",
    "ImportedSubMeshData",
    "KenshiObjectSerializer",
    "UnsupportedPureImport",
]
