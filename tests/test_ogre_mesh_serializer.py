import struct
import unittest

from bz98tools.ogrefast.pure import chunks
from bz98tools.ogrefast.pure.model import (
    GeometryData,
    MeshBounds,
    MeshData,
    SubMeshData,
    VertexBuffer,
    VertexElement,
)
from bz98tools.ogrefast.pure.serializer import OgreMeshSerializer


_HEADER = b"\x00\x10[MeshSerializer_v1.100]\n"


def _chunk(data, offset):
    chunk_id, length = struct.unpack_from("<HI", data, offset)
    return chunk_id, length, offset + 6, offset + length


def _find_direct_child(data, start, end, wanted):
    offset = start
    while offset < end:
        chunk_id, length, payload_start, chunk_end = _chunk(data, offset)
        if chunk_id == wanted:
            return offset, length, payload_start, chunk_end
        if length < 6:
            raise AssertionError(f"invalid chunk length {length} at {offset}")
        offset = chunk_end
    return None


class OgreMeshSerializerTests(unittest.TestCase):
    def _triangle(self, *, name="body", skeleton_name=""):
        # Interleaved position + normal + UV: 3f + 3f + 2f = 32 bytes.
        vertices = [
            (0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0),
        ]
        raw = b"".join(struct.pack("<8f", *vertex) for vertex in vertices)
        geometry = GeometryData(
            vertex_count=3,
            declaration=[
                VertexElement("POSITION", 0, 0, "FLOAT3"),
                VertexElement("NORMAL", 0, 12, "FLOAT3"),
                VertexElement("TEXCOORD", 0, 24, "FLOAT2"),
            ],
            buffers=[VertexBuffer(0, 32, raw)],
        )
        return MeshData(
            submeshes=[
                SubMeshData(
                    material_name="battlezone/test",
                    indices=[0, 1, 2],
                    name=name,
                    geometry=geometry,
                )
            ],
            skeleton_name=skeleton_name,
            bounds=MeshBounds((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), 1.4142135),
        )

    def test_static_triangle_has_ogre_v110_core_chunks(self):
        data = OgreMeshSerializer().dumps(self._triangle())
        self.assertTrue(data.startswith(_HEADER))

        mesh_offset = len(_HEADER)
        mesh_id, mesh_len, mesh_payload, mesh_end = _chunk(data, mesh_offset)
        self.assertEqual(mesh_id, chunks.M_MESH)
        self.assertEqual(mesh_end, len(data))
        self.assertEqual(mesh_len, len(data) - mesh_offset)
        self.assertEqual(data[mesh_payload], 0)  # no skeleton

        child_start = mesh_payload + 1
        submesh = _find_direct_child(data, child_start, mesh_end, chunks.M_SUBMESH)
        bounds = _find_direct_child(data, child_start, mesh_end, chunks.M_MESH_BOUNDS)
        names = _find_direct_child(data, child_start, mesh_end, chunks.M_SUBMESH_NAME_TABLE)
        self.assertIsNotNone(submesh)
        self.assertIsNotNone(bounds)
        self.assertIsNotNone(names)

        _, _, sub_payload, sub_end = submesh
        material_end = data.index(b"\n", sub_payload) + 1
        self.assertEqual(data[sub_payload:material_end], b"battlezone/test\n")
        use_shared = data[material_end]
        index_count = struct.unpack_from("<I", data, material_end + 1)[0]
        indexes32 = data[material_end + 5]
        self.assertEqual(use_shared, 0)
        self.assertEqual(index_count, 3)
        self.assertEqual(indexes32, 0)
        self.assertEqual(
            struct.unpack_from("<3H", data, material_end + 6), (0, 1, 2)
        )

        sub_chunks_start = material_end + 12
        geometry = _find_direct_child(data, sub_chunks_start, sub_end, chunks.M_GEOMETRY)
        operation = _find_direct_child(
            data, sub_chunks_start, sub_end, chunks.M_SUBMESH_OPERATION
        )
        self.assertIsNotNone(geometry)
        self.assertIsNotNone(operation)

        _, _, geom_payload, geom_end = geometry
        self.assertEqual(struct.unpack_from("<I", data, geom_payload)[0], 3)
        declaration = _find_direct_child(
            data, geom_payload + 4, geom_end, chunks.M_GEOMETRY_VERTEX_DECLARATION
        )
        vertex_buffer = _find_direct_child(
            data, geom_payload + 4, geom_end, chunks.M_GEOMETRY_VERTEX_BUFFER
        )
        self.assertIsNotNone(declaration)
        self.assertIsNotNone(vertex_buffer)

        _, _, op_payload, _ = operation
        self.assertEqual(struct.unpack_from("<H", data, op_payload)[0], 4)

        _, _, name_payload, name_end = names
        name_element = _find_direct_child(
            data, name_payload, name_end, chunks.M_SUBMESH_NAME_TABLE_ELEMENT
        )
        self.assertIsNotNone(name_element)
        _, _, element_payload, _ = name_element
        self.assertEqual(struct.unpack_from("<H", data, element_payload)[0], 0)
        self.assertEqual(data[element_payload + 2 :].split(b"\n", 1)[0], b"body")

    def test_skeleton_link_sets_mesh_skeletal_flag(self):
        data = OgreMeshSerializer().dumps(
            self._triangle(skeleton_name="avtank.skeleton")
        )
        _, _, mesh_payload, mesh_end = _chunk(data, len(_HEADER))
        self.assertEqual(data[mesh_payload], 1)
        skeleton = _find_direct_child(
            data, mesh_payload + 1, mesh_end, chunks.M_MESH_SKELETON_LINK
        )
        self.assertIsNotNone(skeleton)
        _, _, skel_payload, _ = skeleton
        self.assertEqual(data[skel_payload:].split(b"\n", 1)[0], b"avtank.skeleton")

    def test_invalid_vertex_buffer_size_is_rejected(self):
        mesh = self._triangle()
        mesh.submeshes[0].geometry.buffers[0].data = b"too short"
        with self.assertRaisesRegex(ValueError, "vertex buffer 0"):
            OgreMeshSerializer().dumps(mesh)


if __name__ == "__main__":
    unittest.main()
