from bz98tools.ogrefast.pure.enums import (
    OperationType,
    VertexElementSemantic,
    VertexElementType,
    vertex_semantic,
    vertex_type,
)


def test_core_ogre_enum_values():
    assert VertexElementType.FLOAT3 == 2
    assert VertexElementSemantic.POSITION == 1
    assert VertexElementSemantic.NORMAL == 4
    assert VertexElementSemantic.TEXTURE_COORDINATES == 7
    assert OperationType.TRIANGLE_LIST == 4


def test_string_enum_mapping():
    assert vertex_type("float3") == VertexElementType.FLOAT3
    assert vertex_semantic("texcoord") == VertexElementSemantic.TEXTURE_COORDINATES
