from __future__ import annotations

from pathlib import Path

from . import chunks
from .binary import BinaryWriter
from .enums import vertex_semantic, vertex_type
from .model import BoneAssignment, GeometryData, MeshData, MeshVersion, SubMeshData


class OgreMeshSerializer:
    """Minimal direct OGRE v1.10 mesh serializer.

    This intentionally emits only chunks we actively author. Edge lists, LOD,
    poses and animation are omitted until separately implemented and tested.
    """

    HEADER_ID = 0x1000

    def dumps(self, mesh: MeshData, version: MeshVersion = MeshVersion.V_1_10) -> bytes:
        if version is not MeshVersion.V_1_10:
            raise NotImplementedError(f"unsupported mesh version: {version}")

        writer = BinaryWriter()
        # Serializer::writeFileHeader writes the stream ID followed by a newline-
        # terminated version string. The header is not a normal length-prefixed
        # chunk.
        writer.u16(self.HEADER_ID)
        writer.line(f"[{version.value}]")

        with writer.chunk(chunks.M_MESH):
            # skeletalAnimation flag in Ogre's v1.x mesh chunk.
            writer.bool(bool(mesh.skeleton_name or mesh.bone_assignments))

            if mesh.shared_geometry is not None:
                self._write_geometry(writer, mesh.shared_geometry)

            for submesh in mesh.submeshes:
                self._write_submesh(writer, submesh)

            if mesh.skeleton_name:
                with writer.chunk(chunks.M_MESH_SKELETON_LINK):
                    writer.line(mesh.skeleton_name)

            for assignment in mesh.bone_assignments:
                self._write_bone_assignment(writer, chunks.M_MESH_BONE_ASSIGNMENT, assignment)

            if mesh.bounds is not None:
                with writer.chunk(chunks.M_MESH_BOUNDS):
                    writer.vec3(mesh.bounds.minimum)
                    writer.vec3(mesh.bounds.maximum)
                    writer.f32(mesh.bounds.radius)

        return writer.getvalue()

    def dump(self, mesh: MeshData, path: str | Path, version: MeshVersion = MeshVersion.V_1_10) -> None:
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

            if submesh.operation_type != 4:
                with writer.chunk(chunks.M_SUBMESH_OPERATION):
                    writer.u16(int(submesh.operation_type))

            for assignment in submesh.bone_assignments:
                self._write_bone_assignment(writer, chunks.M_SUBMESH_BONE_ASSIGNMENT, assignment)

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

    @staticmethod
    def _write_bone_assignment(writer: BinaryWriter, chunk_id: int, assignment: BoneAssignment) -> None:
        with writer.chunk(chunk_id):
            writer.u32(assignment.vertex_index)
            writer.u16(assignment.bone_index)
            writer.f32(assignment.weight)
