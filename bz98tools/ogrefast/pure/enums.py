from __future__ import annotations

from enum import IntEnum


class VertexElementType(IntEnum):
    FLOAT1 = 0
    FLOAT2 = 1
    FLOAT3 = 2
    FLOAT4 = 3
    COLOUR = 4
    SHORT2 = 5
    SHORT4 = 6
    UBYTE4 = 7
    COLOUR_ARGB = 8
    COLOUR_ABGR = 9
    DOUBLE1 = 10
    DOUBLE2 = 11
    DOUBLE3 = 12
    DOUBLE4 = 13
    USHORT2 = 14
    USHORT4 = 15
    INT1 = 16
    INT2 = 17
    INT3 = 18
    INT4 = 19
    UINT1 = 20
    UINT2 = 21
    UINT3 = 22
    UINT4 = 23
    BYTE4 = 24
    BYTE4_SNORM = 25
    UBYTE4_NORM = 26
    SHORT2_SNORM = 27
    SHORT4_SNORM = 28
    USHORT2_NORM = 29
    USHORT4_NORM = 30
    HALF2 = 31
    HALF4 = 32


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


_TYPE_NAMES = {
    "FLOAT1": VertexElementType.FLOAT1,
    "FLOAT2": VertexElementType.FLOAT2,
    "FLOAT3": VertexElementType.FLOAT3,
    "FLOAT4": VertexElementType.FLOAT4,
    "COLOUR": VertexElementType.COLOUR,
    "COLOUR_ARGB": VertexElementType.COLOUR_ARGB,
    "COLOUR_ABGR": VertexElementType.COLOUR_ABGR,
    "SHORT2": VertexElementType.SHORT2,
    "SHORT4": VertexElementType.SHORT4,
    "UBYTE4": VertexElementType.UBYTE4,
}

_SEMANTIC_NAMES = {
    "POSITION": VertexElementSemantic.POSITION,
    "BLEND_WEIGHTS": VertexElementSemantic.BLEND_WEIGHTS,
    "BLEND_INDICES": VertexElementSemantic.BLEND_INDICES,
    "NORMAL": VertexElementSemantic.NORMAL,
    "DIFFUSE": VertexElementSemantic.DIFFUSE,
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
