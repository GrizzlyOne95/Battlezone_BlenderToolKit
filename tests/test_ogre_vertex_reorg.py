import struct
import unittest

from bz98tools.ogrefast.pure.model import GeometryData, VertexBuffer, VertexElement
from bz98tools.ogrefast.pure.vertex_reorg import (
    auto_organise_declaration,
    reorganise_geometry,
)


def _pilot_geometry():
    rows = [
        (
            (1.0, 2.0, 3.0),
            (0.1, 0.2, 0.3),
            b"\x11\x22\x33\x44",
            (0.25, 0.5),
            (0.75, 1.0),
            (4.0, 5.0, 6.0, -1.0),
            (7.0, 8.0, 9.0),
        ),
        (
            (10.0, 20.0, 30.0),
            (0.4, 0.5, 0.6),
            b"\xaa\xbb\xcc\xdd",
            (0.125, 0.625),
            (0.875, 0.375),
            (40.0, 50.0, 60.0, 1.0),
            (70.0, 80.0, 90.0),
        ),
    ]

    raw = bytearray()
    for position, normal, colour, uv0, uv1, tangent, binormal in rows:
        raw.extend(struct.pack("<3f", *position))
        raw.extend(struct.pack("<3f", *normal))
        raw.extend(colour)
        raw.extend(struct.pack("<2f", *uv0))
        raw.extend(struct.pack("<2f", *uv1))
        raw.extend(struct.pack("<4f", *tangent))
        raw.extend(struct.pack("<3f", *binormal))

    return GeometryData(
        vertex_count=2,
        declaration=[
            VertexElement("POSITION", 0, 0, "FLOAT3"),
            VertexElement("NORMAL", 0, 12, "FLOAT3"),
            VertexElement("DIFFUSE", 0, 24, "COLOUR_ABGR"),
            VertexElement("TEXCOORD", 0, 28, "FLOAT2", 0),
            VertexElement("TEXCOORD", 0, 36, "FLOAT2", 1),
            VertexElement("TANGENT", 0, 44, "FLOAT4"),
            VertexElement("BINORMAL", 0, 60, "FLOAT3"),
        ],
        buffers=[VertexBuffer(0, 72, bytes(raw))],
    )


class OgreVertexReorganisationTests(unittest.TestCase):
    def test_skeletal_layout_matches_ogre_1116_auto_organisation(self):
        geometry = _pilot_geometry()
        reorganised = reorganise_geometry(
            geometry,
            skeletal_animation=True,
        )

        self.assertEqual(
            [
                (element.semantic, element.source, element.offset, element.index)
                for element in reorganised.declaration
            ],
            [
                ("POSITION", 0, 0, 0),
                ("NORMAL", 0, 12, 0),
                ("DIFFUSE", 1, 0, 0),
                ("TEXCOORD", 1, 4, 0),
                ("TEXCOORD", 1, 12, 1),
                ("BINORMAL", 1, 20, 0),
                ("TANGENT", 1, 32, 0),
            ],
        )
        self.assertEqual(
            [(buffer.bind_index, buffer.vertex_size) for buffer in reorganised.buffers],
            [(0, 24), (1, 48)],
        )

        old = geometry.buffers[0].data
        expected_vb0 = bytearray()
        expected_vb1 = bytearray()
        for vertex in range(geometry.vertex_count):
            base = vertex * 72
            expected_vb0.extend(old[base : base + 24])
            expected_vb1.extend(old[base + 24 : base + 44])
            expected_vb1.extend(old[base + 60 : base + 72])
            expected_vb1.extend(old[base + 44 : base + 60])

        self.assertEqual(reorganised.buffers[0].data, bytes(expected_vb0))
        self.assertEqual(reorganised.buffers[1].data, bytes(expected_vb1))

        # Reorganisation returns a new view and leaves the collector geometry
        # untouched, which makes serializer-side application safe.
        self.assertEqual(geometry.buffers[0].vertex_size, 72)
        self.assertEqual(geometry.declaration[5].semantic, "TANGENT")
        self.assertEqual(geometry.declaration[6].semantic, "BINORMAL")

    def test_static_layout_stays_one_buffer_but_uses_ogre_semantic_order(self):
        geometry = _pilot_geometry()
        reorganised = reorganise_geometry(
            geometry,
            skeletal_animation=False,
        )

        self.assertEqual(len(reorganised.buffers), 1)
        self.assertEqual(reorganised.buffers[0].vertex_size, 72)
        self.assertEqual(
            [element.semantic for element in reorganised.declaration],
            [
                "POSITION",
                "NORMAL",
                "DIFFUSE",
                "TEXCOORD",
                "TEXCOORD",
                "BINORMAL",
                "TANGENT",
            ],
        )
        self.assertEqual(
            [element.offset for element in reorganised.declaration],
            [0, 12, 24, 28, 36, 44, 56],
        )

    def test_blend_stream_boundaries_follow_ogre_rules(self):
        declaration = [
            VertexElement("POSITION", 0, 0, "FLOAT3"),
            VertexElement("BLEND_WEIGHTS", 0, 12, "FLOAT4"),
            VertexElement("BLEND_INDICES", 0, 28, "UBYTE4"),
            VertexElement("NORMAL", 0, 32, "FLOAT3"),
            VertexElement("TEXCOORD", 0, 44, "FLOAT2"),
        ]

        organised = auto_organise_declaration(
            declaration,
            skeletal_animation=True,
        )
        self.assertEqual(
            [(e.semantic, e.source, e.offset) for e in organised],
            [
                ("POSITION", 0, 0),
                ("BLEND_WEIGHTS", 1, 0),
                ("BLEND_INDICES", 1, 16),
                ("NORMAL", 2, 0),
                ("TEXCOORD", 3, 0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
