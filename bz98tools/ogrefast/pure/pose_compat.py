from __future__ import annotations

"""Shape-key compatibility layer for the pure Ogre mesh serializer.

The established Blender collector calls SubMeshData.append_shapekey with
source-vertex deltas plus the exported-vertex -> source-vertex mapping returned
by set_vertex(). This module turns that into OGRE's sparse pose representation.
"""

import numpy as np

from .kenshi_compat import *  # noqa: F401,F403 - deliberate compatibility surface
from .kenshi_compat import (
    KenshiObjectSerializer as BaseKenshiObjectSerializer,
    MeshData as BaseMeshData,
    SubMeshData as BaseSubMeshData,
    _blender_to_ogre_xyz,
)
from .model import PoseData as WirePoseData
from .model import PoseVertex as WirePoseVertex


class SubMeshData(BaseSubMeshData):
    def __init__(self):
        super().__init__()
        self._wire_poses: list[tuple[str, list[WirePoseVertex]]] = []

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
