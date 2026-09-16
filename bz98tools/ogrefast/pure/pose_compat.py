from __future__ import annotations

"""Shape-key and multi-UV compatibility for the pure Ogre mesh serializer.

The established Blender collector calls SubMeshData.append_shapekey with
source-vertex deltas plus the exported-vertex -> source-vertex mapping returned
by set_vertex().  Modern assets can also carry multiple Ogre texture-coordinate
sets, so this subclass accepts either the historical ``(loops, 2)`` UV array or
``(sets, loops, 2)`` and includes every set in vertex splitting/deduplication.
"""

import numpy as np

from .kenshi_compat import *  # noqa: F401,F403 - deliberate compatibility surface
from .kenshi_compat import (
    KenshiObjectSerializer as BaseKenshiObjectSerializer,
    MeshData as BaseMeshData,
    SubMeshData as BaseSubMeshData,
    _blender_to_ogre_xyz,
    _flat_loop_array,
    _loop_array,
    _pack_colour_abgr,
)
from .model import GeometryData as WireGeometryData
from .model import PoseData as WirePoseData
from .model import PoseVertex as WirePoseVertex
from .model import VertexBuffer, VertexElement


class SubMeshData(BaseSubMeshData):
    supports_multiple_texcoords = True

    def __init__(self):
        super().__init__()
        self._wire_poses: list[tuple[str, list[WirePoseVertex]]] = []

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
            int(source_indices.min()) < 0
            or int(source_indices.max()) >= len(source_positions)
        ):
            raise ValueError("source vertex index is outside the position array")

        positions = _blender_to_ogre_xyz(source_positions[source_indices])
        normals = _loop_array(nd_normals, loop_count, 3)
        if normals is not None:
            normals = _blender_to_ogre_xyz(normals)

        raw_texcoords = np.asarray(nd_texcoords, dtype=np.float32)
        if raw_texcoords.ndim == 2 and raw_texcoords.shape == (loop_count, 2):
            texcoord_sets = [raw_texcoords.copy()]
        elif (
            raw_texcoords.ndim == 3
            and raw_texcoords.shape[1] == loop_count
            and raw_texcoords.shape[2] == 2
        ):
            texcoord_sets = [values.copy() for values in raw_texcoords]
        else:
            texcoord_sets = []
        for values in texcoord_sets:
            values[:, 1] = 1.0 - values[:, 1]

        tangents = _loop_array(nd_tangents, loop_count, 3)
        if tangents is not None:
            tangents = _blender_to_ogre_xyz(tangents)
        bitangents = _loop_array(nd_bitangents, loop_count, 3)
        if bitangents is not None:
            bitangents = _blender_to_ogre_xyz(bitangents)
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
                row.append(
                    np.asarray([rgb[0], rgb[1], rgb[2], alpha], dtype=np.float32)
                )
            for values in texcoord_sets:
                row.append(values[loop_i])
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
        self._wire_geometry = _build_geometry_multi_uv(
            exported_rows,
            has_normals=normals is not None,
            has_colors=colors is not None,
            texcoord_count=len(texcoord_sets),
            has_tangents=tangents is not None,
            tangent_dimensions=tangent_dimensions,
            has_binormals=bitangents is not None,
        )
        return self._source_vertex_indices.copy()

    def append_shapekey(self, name, nd_shape_keys, out_nd_indices):
        deltas = np.asarray(nd_shape_keys, dtype=np.float32).reshape(-1, 3)
        source_map = np.asarray(out_nd_indices, dtype=np.int32).reshape(-1)
        if self._wire_geometry is None:
            raise ValueError("shape key cannot be attached before vertex geometry")
        if source_map.size != self._wire_geometry.vertex_count:
            raise ValueError(
                "shape-key source mapping length does not match exported vertex count"
            )
        if source_map.size and (
            int(source_map.min()) < 0 or int(source_map.max()) >= len(deltas)
        ):
            raise ValueError("shape-key source mapping references an invalid source vertex")

        # Shape-key deltas are vectors, so they use the same Blender->Ogre axis
        # transform as positions: (X,Y,Z) -> (X,Z,-Y).
        exported = _blender_to_ogre_xyz(deltas[source_map])
        vertices: list[WirePoseVertex] = []
        for vertex_index, offset in enumerate(exported):
            # OGRE poses are sparse. Preserve exact zero as omission while
            # retaining tiny authored non-zero deltas.
            if not np.any(offset != 0.0):
                continue
            vertices.append(
                WirePoseVertex(
                    vertex_index=int(vertex_index),
                    offset=tuple(float(value) for value in offset),
                )
            )
        self._wire_poses.append((str(name), vertices))


class MeshData(BaseMeshData):
    def to_wire(self):
        wire = super().to_wire()
        poses = []
        for submesh_index, submesh in enumerate(self._submeshes):
            for name, vertices in getattr(submesh, "_wire_poses", ()):
                poses.append(
                    WirePoseData(
                        name=str(name),
                        target=submesh_index + 1,
                        vertices=list(vertices),
                    )
                )
        wire.poses = poses
        return wire


class KenshiObjectSerializer(BaseKenshiObjectSerializer):
    def create_mesh(self, filename):
        return MeshData(filename, "General")


def _build_geometry_multi_uv(
    rows,
    *,
    has_normals: bool,
    has_colors: bool,
    texcoord_count: int,
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
    for uv_index in range(texcoord_count):
        declaration.append(
            VertexElement("TEXCOORD", 0, offset, "FLOAT2", index=uv_index)
        )
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
        for _ in range(texcoord_count):
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
    # Overrides
    "KenshiObjectSerializer",
    "MeshData",
    "SubMeshData",
    # Compatibility names consumed by ogre_exporter
    "AnimationData",
    "BoneAssignmentData",
    "BoneData",
    "GeometryData",
    "Matrix3",
    "MeshVersion",
    "OgreQuaternion",
    "OperationType",
    "SkeletonData",
    "SkeletonVersion",
    "V_1_10",
    "V_Latest",
    "V_Legacy",
    "Vector3",
    "triangle_list",
]
