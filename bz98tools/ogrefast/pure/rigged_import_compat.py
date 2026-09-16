from __future__ import annotations

"""Rigged/static mesh extension of the pure Ogre import compatibility layer."""

from dataclasses import dataclass

import numpy as np

from .import_compat import (
    ImportedSubMeshData,
    UnsupportedPureImport,
    _decode_semantic,
    _ogre_to_blender_xyz,
    _wire_geometry_from_legacy,
)
from .kenshi_compat import BoneAssignmentData, MeshData, OperationType
from .pose_import_compat import PoseDetectingMeshSerializer
from .skeleton_compat import KenshiObjectSerializer as SkeletonKenshiObjectSerializer
from ...bzrmodelporter.ogremesh import OT


@dataclass
class VertexGroupData:
    name: str
    group: list[tuple[list[int], float]]


class RiggedImportedSubMeshData(ImportedSubMeshData):
    def __init__(self):
        super().__init__()
        self._pose_records = []

    def get_colors(self, is_rgba=True):
        # Blender's foreach_set() consumes a flat scalar sequence.  The pure
        # base reader keeps RGBA values as (loop, 4) arrays for convenient
        # semantic processing, so flatten them at the legacy API boundary.
        colors, alpha = super().get_colors(is_rgba=is_rgba)
        return (
            np.asarray(colors, dtype=np.float32).reshape(-1),
            np.asarray(alpha, dtype=np.float32).reshape(-1),
        )

    def get_vertex_groups(self):
        grouped: dict[int, list[tuple[list[int], float]]] = {}
        for assignment in self.boneassignments:
            grouped.setdefault(int(assignment.bone_index), []).append(
                ([int(assignment.vertex_index)], float(assignment.weight))
            )

        return [
            VertexGroupData(
                self._bone_mapping.get(bone_index, f"Bone{bone_index}"),
                values,
            )
            for bone_index, values in sorted(grouped.items())
        ]

    def get_shapekeys(self):
        if not self._pose_records:
            return []
        base = np.asarray(self.get_positions(), dtype=np.float32).reshape(-1, 3)
        result = []
        for pose in self._pose_records:
            coords = base.copy()
            for vertex in pose.vertices:
                index = int(vertex.vertex_index)
                if index < 0 or index >= len(coords):
                    raise UnsupportedPureImport(
                        f"pose {pose.name!r} references vertex {index}, but target geometry has {len(coords)} vertices"
                    )
                offset = _ogre_to_blender_xyz(
                    np.asarray([vertex.offset], dtype=np.float32)
                )[0]
                coords[index] += offset
            result.append((str(pose.name), coords))
        return result


class RiggedImportedMeshData(MeshData):
    def __init__(self, file="", group="General"):
        super().__init__(file, group)
        self._linked_skeleton = None

    def set_linked_skeleton(self, skeleton):
        self._linked_skeleton = skeleton

    def get_linked_skeleton(self):
        return self._linked_skeleton


class KenshiObjectSerializer(SkeletonKenshiObjectSerializer):
    """Load meshes, poses, skin weights and linked skeleton animations."""

    def load_mesh(self, file):
        path = self._resolve_resource(file)
        with path.open("rb") as stream:
            source = PoseDetectingMeshSerializer(stream).read()
        mesh = _convert_mesh(source, path.name)

        if mesh.get_linked_skeleton_name():
            mesh.set_linked_skeleton(
                self.load_skeleton(mesh.get_linked_skeleton_name())
            )
        return mesh


def _convert_assignments(items):
    return [
        BoneAssignmentData(
            int(item.vertex_index), int(item.bone_index), float(item.weight)
        )
        for item in items
    ]


def _convert_mesh(source, filename: str) -> RiggedImportedMeshData:
    if getattr(source, "_pure_import_contains_animations", False):
        raise UnsupportedPureImport("mesh contains mesh animation data")

    poses = list(getattr(source, "_pure_import_poses", ()))
    for pose in poses:
        if pose.includes_normals:
            # Blender shape keys have no independently authored normal-delta
            # storage. Failing closed avoids silently discarding OGRE data.
            raise UnsupportedPureImport(
                f"pose {pose.name!r} contains normal offsets, which cannot be preserved as Blender shape keys"
            )

    shared_geometry = (
        _wire_geometry_from_legacy(source.shared_vertex_data)
        if source.shared_vertex_data is not None
        else None
    )
    shared_assignments = _convert_assignments(source.bone_assignments())

    valid_pose_targets = set()
    if shared_geometry is not None:
        valid_pose_targets.add(0)
    for index, source_submesh in enumerate(source.submesh_list):
        if not source_submesh.use_shared_vertices:
            valid_pose_targets.add(index + 1)
    unknown_targets = sorted({pose.target for pose in poses} - valid_pose_targets)
    if unknown_targets:
        raise UnsupportedPureImport(
            f"mesh contains pose targets with no matching geometry: {unknown_targets}"
        )

    mesh = RiggedImportedMeshData(filename, "General")
    if source.skeleton_name:
        mesh.set_linked_skeleton_name(str(source.skeleton_name))

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

        index_array = source_submesh.get_index_array()
        indices = [] if index_array is None else [int(value) for value in index_array.tolist()]
        if len(indices) % 3:
            raise UnsupportedPureImport(
                f"submesh {index} triangle-list index count is not divisible by three"
            )

        assignments = _convert_assignments(source_submesh.bone_assignments())
        if source_submesh.use_shared_vertices:
            assignments = shared_assignments + assignments
        if assignments and not source.skeleton_name:
            raise UnsupportedPureImport(
                f"submesh {index} contains bone assignments but the mesh has no skeleton link"
            )

        target = 0 if source_submesh.use_shared_vertices else index + 1
        submesh = RiggedImportedSubMeshData()
        submesh.index = index
        submesh.submesh_name = source_submesh.get_name() or f"SubMesh{index}"
        submesh.material = source_submesh.material_name or ""
        submesh.operation_type = OperationType.triangle_list
        submesh.use_shared_vertices = bool(source_submesh.use_shared_vertices)
        submesh.use_32bit_indexes = bool(source_submesh.indices_32_bit)
        submesh.faces = indices
        submesh.face_count = len(indices) // 3
        submesh.boneassignments = assignments
        submesh._wire_geometry = geometry
        submesh._pose_records = [pose for pose in poses if pose.target == target]
        ogre_positions = _decode_semantic(geometry, {"POSITION"})
        if ogre_positions is not None:
            submesh._positions = ogre_positions[:, :3].copy()
        submeshes.append(submesh)

    mesh.set_submeshes(submeshes)
    return mesh


__all__ = [
    "KenshiObjectSerializer",
    "RiggedImportedMeshData",
    "RiggedImportedSubMeshData",
    "VertexGroupData",
]
