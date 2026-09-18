from __future__ import annotations

from enum import IntEnum


class VertexElementType(IntEnum):
    """OGRE 1.10 VertexElementType values used by MeshSerializer_v1.100."""

    FLOAT1 = 0
    FLOAT2 = 1
    FLOAT3 = 2
    FLOAT4 = 3
    COLOUR = 4
    SHORT1 = 5
    SHORT2 = 6
    SHORT3 = 7
    SHORT4 = 8
    UBYTE4 = 9
    COLOUR_ARGB = 10
    COLOUR_ABGR = 11
    DOUBLE1 = 12
    DOUBLE2 = 13
    DOUBLE3 = 14
    DOUBLE4 = 15
    USHORT1 = 16
    USHORT2 = 17
    USHORT3 = 18
    USHORT4 = 19
    INT1 = 20
    INT2 = 21
    INT3 = 22
    INT4 = 23
    UINT1 = 24
    UINT2 = 25
    UINT3 = 26
    UINT4 = 27
    BYTE4 = 28
    BYTE4_NORM = 29
    UBYTE4_NORM = 30
    SHORT2_NORM = 31
    SHORT4_NORM = 32
    USHORT2_NORM = 33
    USHORT4_NORM = 34


class VertexElementSemantic(IntEnum):
    POSITION = 1
    BLEND_WEIGHTS = 2
    BLEND_INDICES = 3
    NORMAL = 4
    DIFFUSE = 5
    SPECULAR = 6
    TEXTURE_COORDINATES = 7
    BINORMAL = 8
    TANGENT = 9


class OperationType(IntEnum):
    POINT_LIST = 1
    LINE_LIST = 2
    LINE_STRIP = 3
    TRIANGLE_LIST = 4
    TRIANGLE_STRIP = 5
    TRIANGLE_FAN = 6


_TYPE_NAMES = {member.name: member for member in VertexElementType}
_SEMANTIC_NAMES = {
    "POSITION": VertexElementSemantic.POSITION,
    "BLEND_WEIGHTS": VertexElementSemantic.BLEND_WEIGHTS,
    "BLEND_INDICES": VertexElementSemantic.BLEND_INDICES,
    "NORMAL": VertexElementSemantic.NORMAL,
    "DIFFUSE": VertexElementSemantic.DIFFUSE,
    "COLOUR": VertexElementSemantic.DIFFUSE,
    "SPECULAR": VertexElementSemantic.SPECULAR,
    "TEXTURE_COORDINATES": VertexElementSemantic.TEXTURE_COORDINATES,
    "TEXCOORD": VertexElementSemantic.TEXTURE_COORDINATES,
    "BINORMAL": VertexElementSemantic.BINORMAL,
    "TANGENT": VertexElementSemantic.TANGENT,
}


def vertex_type(value: str | int | VertexElementType) -> VertexElementType:
    if isinstance(value, VertexElementType):
        return value
    if isinstance(value, str):
        return _TYPE_NAMES[value.upper()]
    return VertexElementType(value)


def vertex_semantic(value: str | int | VertexElementSemantic) -> VertexElementSemantic:
    if isinstance(value, VertexElementSemantic):
        return value
    if isinstance(value, str):
        return _SEMANTIC_NAMES[value.upper()]
    return VertexElementSemantic(value)
