import unittest

from bz98tools.ogrefast.pure.enums import (
    OperationType,
    VertexElementSemantic,
    VertexElementType,
    vertex_semantic,
    vertex_type,
)


class OgreEnumTests(unittest.TestCase):
    def test_core_ogre_enum_values(self):
        self.assertEqual(VertexElementType.FLOAT3, 2)
        self.assertEqual(VertexElementSemantic.POSITION, 1)
        self.assertEqual(VertexElementSemantic.NORMAL, 4)
        self.assertEqual(VertexElementSemantic.TEXTURE_COORDINATES, 7)
        self.assertEqual(OperationType.TRIANGLE_LIST, 4)

    def test_string_enum_mapping(self):
        self.assertEqual(vertex_type("float3"), VertexElementType.FLOAT3)
        self.assertEqual(
            vertex_semantic("texcoord"), VertexElementSemantic.TEXTURE_COORDINATES
        )


if __name__ == "__main__":
    unittest.main()
