import unittest

from bz98tools.ogrefast.pure.enums import (
    OperationType,
    VertexElementSemantic,
    VertexElementType,
    vertex_semantic,
    vertex_type,
)


class OgreEnumTests(unittest.TestCase):
    def test_core_ogre_110_enum_values(self):
        self.assertEqual(VertexElementType.FLOAT3, 2)
        self.assertEqual(VertexElementType.SHORT2, 6)
        self.assertEqual(VertexElementType.UBYTE4, 9)
        self.assertEqual(VertexElementType.COLOUR_ARGB, 10)
        self.assertEqual(VertexElementType.COLOUR_ABGR, 11)
        self.assertEqual(VertexElementType.UBYTE4_NORM, 30)
        self.assertEqual(VertexElementSemantic.POSITION, 1)
        self.assertEqual(VertexElementSemantic.NORMAL, 4)
        self.assertEqual(VertexElementSemantic.TEXTURE_COORDINATES, 7)
        self.assertEqual(OperationType.TRIANGLE_LIST, 4)

    def test_string_enum_mapping(self):
        self.assertEqual(vertex_type("float3"), VertexElementType.FLOAT3)
        self.assertEqual(vertex_type("colour_abgr"), VertexElementType.COLOUR_ABGR)
        self.assertEqual(
            vertex_semantic("texcoord"), VertexElementSemantic.TEXTURE_COORDINATES
        )
        self.assertEqual(vertex_semantic("colour"), VertexElementSemantic.DIFFUSE)


if __name__ == "__main__":
    unittest.main()
