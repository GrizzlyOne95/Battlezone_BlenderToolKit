from __future__ import annotations

"""Pose-aware extension of the existing pure Ogre mesh reader."""

from dataclasses import dataclass

from ...bzrmodelporter.ogremesh_serializer import MeshChunkID
from .import_compat import _DetectingMeshSerializer


@dataclass(frozen=True)
class ImportedPoseVertex:
    vertex_index: int
    offset: tuple[float, float, float]
    normal: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class ImportedPose:
    name: str
    target: int
    includes_normals: bool
    vertices: tuple[ImportedPoseVertex, ...]


class PoseDetectingMeshSerializer(_DetectingMeshSerializer):
    """Parse pose chunks instead of marking them as unsupported/skipped."""

    def read_poses(self, mesh):
        poses = []
        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id != MeshChunkID.POSE:
                self.rollback_chunk_header()
                break

            name = self.read_string_nlt()
            target = self.read_ushort()
            includes_normals = self.read_bool()
            vertices = []
            while True:
                try:
                    child_id = self.read_chunk_header()
                except EOFError:
                    break
                if child_id != MeshChunkID.POSE_VERTEX:
                    self.rollback_chunk_header()
                    break

                vertex_index = self.read_uint()
                offset = (
                    self.read_float(),
                    self.read_float(),
                    self.read_float(),
                )
                normal = None
                if includes_normals:
                    normal = (
                        self.read_float(),
                        self.read_float(),
                        self.read_float(),
                    )
                vertices.append(
                    ImportedPoseVertex(
                        vertex_index=int(vertex_index),
                        offset=tuple(float(value) for value in offset),
                        normal=(
                            tuple(float(value) for value in normal)
                            if normal is not None
                            else None
                        ),
                    )
                )
                self.pop_chunk(MeshChunkID.POSE_VERTEX)

            poses.append(
                ImportedPose(
                    name=str(name),
                    target=int(target),
                    includes_normals=bool(includes_normals),
                    vertices=tuple(vertices),
                )
            )
            self.pop_chunk(MeshChunkID.POSE)

        mesh._pure_import_poses = poses
        self.pop_chunk(MeshChunkID.POSES)


__all__ = [
    "ImportedPose",
    "ImportedPoseVertex",
    "PoseDetectingMeshSerializer",
]
