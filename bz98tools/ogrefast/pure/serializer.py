from __future__ import annotations

from pathlib import Path

from . import chunks
from .binary import BinaryWriter
from .enums import vertex_semantic, vertex_type
from .model import (
    BoneAssignment,
    GeometryData,
    MeshData,
    MeshVersion,
    PoseData,
    SubMeshData,
)


class OgreMeshSerializer:
    """Direct OGRE v1.10 mesh serializer used by the pure Python backend.

    Static geometry, skin weights, skeleton links and sparse pose/shape-key
    chunks are written directly. LOD, edge lists and mesh animation tracks
    remain intentionally out of scope here.
    """

    HEADER_ID = chunks.HEADER

    def dumps(self, mesh: MeshData, version: MeshVersion = MeshVersion.V_1_10) -> bytes:
        if version is not MeshVersion.V_1_10:
            raise NotImplementedError(f"unsupported mesh version: {version}")

        writer = BinaryWriter()
        # Serializer::writeFileHeader writes a 16-bit stream ID followed by a
        # newline-terminated version string. It is not a normal sized chunk.
        writer.u16(self.HEADER_ID)
        writer.line(f"[{version.value}]")

        with writer.chunk(chunks.M_MESH):
            # OGRE derives this flag from Mesh::hasSkeleton().
            writer.bool(bool(mesh.skeleton_name))

            if mesh.shared_geometry is not None:
                self._write_geometry(writer, mesh.shared_geometry)

            for submesh in mesh.submeshes:
                self._write_submesh(writer, submesh)

            if mesh.skeleton_name:
                with writer.chunk(chunks.M_MESH_SKELETON_LINK):
                    writer.line(mesh.skeleton_name)

                # OGRE only writes shared-geometry assignments when a skeleton
                # is linked.
                for assignment in mesh.bone_assignments:
                    self._write_bone_assignment(
                        writer, chunks.M_MESH_BONE_ASSIGNMENT, assignment
                    )

            if mesh.bounds is not None:
                with writer.chunk(chunks.M_MESH_BOUNDS):
                    writer.vec3(mesh.bounds.minimum)
                    writer.vec3(mesh.bounds.maximum)
                    writer.f32(mesh.bounds.radius)

            self._write_submesh_name_table(writer, mesh)
            self._write_poses(writer, mesh.poses)

        return writer.getvalue()

    def dump(
        self,
        mesh: MeshData,
        path: str | Path,
        version: MeshVersion = MeshVersion.V_1_10,
    ) -> None:
        Path(path).write_bytes(self.dumps(mesh, version))

    def _write_submesh(self, writer: BinaryWriter, submesh: SubMeshData) -> None:
        with writer.chunk(chunks.M_SUBMESH):
            writer.line(submesh.material_name)
            writer.bool(submesh.use_shared_vertices)
            writer.u32(len(submesh.indices))

            use_32bit = bool(submesh.indices and max(submesh.indices) > 0xFFFF)
            writer.bool(use_32bit)
            if use_32bit:
                for index in submesh.indices:
                    writer.u32(index)
            else:
                for index in submesh.indices:
                    writer.u16(index)

            if not submesh.use_shared_vertices:
                if submesh.geometry is None:
                    raise ValueError("submesh without shared vertices requires geometry")
                self._write_geometry(writer, submesh.geometry)

            # OGRE's writer emits this unconditionally even though the reader
            # treats a missing operation chunk as TRIANGLE_LIST.
            with writer.chunk(chunks.M_SUBMESH_OPERATION):
                writer.u16(int(submesh.operation_type))

            for assignment in submesh.bone_assignments:
                self._write_bone_assignment(
                    writer, chunks.M_SUBMESH_BONE_ASSIGNMENT, assignment
                )

    def _write_geometry(self, writer: BinaryWriter, geometry: GeometryData) -> None:
        with writer.chunk(chunks.M_GEOMETRY):
            writer.u32(geometry.vertex_count)

            with writer.chunk(chunks.M_GEOMETRY_VERTEX_DECLARATION):
                for element in geometry.declaration:
                    with writer.chunk(chunks.M_GEOMETRY_VERTEX_ELEMENT):
                        writer.u16(element.source)
                        writer.u16(int(vertex_type(element.component_type)))
                        writer.u16(int(vertex_semantic(element.semantic)))
                        writer.u16(element.offset)
                        writer.u16(element.index)

            for buffer in geometry.buffers:
                expected = geometry.vertex_count * buffer.vertex_size
                if len(buffer.data) != expected:
                    raise ValueError(
                        f"vertex buffer {buffer.bind_index} has {len(buffer.data)} bytes; expected {expected}"
                    )
                with writer.chunk(chunks.M_GEOMETRY_VERTEX_BUFFER):
                    writer.u16(buffer.bind_index)
                    writer.u16(buffer.vertex_size)
                    with writer.chunk(chunks.M_GEOMETRY_VERTEX_BUFFER_DATA):
                        writer.write(buffer.data)

    def _write_submesh_name_table(self, writer: BinaryWriter, mesh: MeshData) -> None:
        with writer.chunk(chunks.M_SUBMESH_NAME_TABLE):
            for index, submesh in enumerate(mesh.submeshes):
                if not submesh.name:
                    continue
                with writer.chunk(chunks.M_SUBMESH_NAME_TABLE_ELEMENT):
                    writer.u16(index)
                    writer.line(submesh.name)

    def _write_poses(self, writer: BinaryWriter, poses: list[PoseData]) -> None:
        if not poses:
            return
        with writer.chunk(chunks.M_POSES):
            for pose in poses:
                if pose.target < 0 or pose.target > 0xFFFF:
                    raise ValueError(f"pose target outside uint16 range: {pose.target}")
                includes_normals = pose.includes_normals
                if includes_normals and any(vertex.normal is None for vertex in pose.vertices):
                    raise ValueError(
                        f"pose {pose.name!r} mixes vertices with and without normal offsets"
                    )
                with writer.chunk(chunks.M_POSE):
                    writer.line(pose.name)
                    writer.u16(pose.target)
                    writer.bool(includes_normals)
                    for vertex in pose.vertices:
                        if vertex.vertex_index < 0 or vertex.vertex_index > 0xFFFFFFFF:
                            raise ValueError(
                                f"pose vertex index outside uint32 range: {vertex.vertex_index}"
                            )
                        with writer.chunk(chunks.M_POSE_VERTEX):
                            writer.u32(vertex.vertex_index)
                            writer.vec3(vertex.offset)
                            if includes_normals:
                                writer.vec3(vertex.normal)

    @staticmethod
    def _write_bone_assignment(
        writer: BinaryWriter, chunk_id: int, assignment: BoneAssignment
    ) -> None:
        with writer.chunk(chunk_id):
            writer.u32(assignment.vertex_index)
            writer.u16(assignment.bone_index)
            writer.f32(assignment.weight)
