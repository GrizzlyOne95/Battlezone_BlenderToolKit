from __future__ import annotations

from dataclasses import replace

from .enums import VertexElementSemantic, vertex_semantic, vertex_type
from .model import GeometryData, MeshData, VertexBuffer, VertexElement


_TYPE_SIZES = {
    "FLOAT1": 4,
    "FLOAT2": 8,
    "FLOAT3": 12,
    "FLOAT4": 16,
    "COLOUR": 4,
    "SHORT1": 2,
    "SHORT2": 4,
    "SHORT3": 6,
    "SHORT4": 8,
    "UBYTE4": 4,
    "COLOUR_ARGB": 4,
    "COLOUR_ABGR": 4,
    "DOUBLE1": 8,
    "DOUBLE2": 16,
    "DOUBLE3": 24,
    "DOUBLE4": 32,
    "USHORT1": 2,
    "USHORT2": 4,
    "USHORT3": 6,
    "USHORT4": 8,
    "INT1": 4,
    "INT2": 8,
    "INT3": 12,
    "INT4": 16,
    "UINT1": 4,
    "UINT2": 8,
    "UINT3": 12,
    "UINT4": 16,
    "BYTE4": 4,
    "BYTE4_NORM": 4,
    "UBYTE4_NORM": 4,
    "SHORT2_NORM": 4,
    "SHORT4_NORM": 8,
    "USHORT2_NORM": 4,
    "USHORT4_NORM": 8,
}


def vertex_element_size(component_type: str | int) -> int:
    """Return the serialized byte width of an Ogre v1.10 vertex element."""

    element_type = vertex_type(component_type)
    try:
        return _TYPE_SIZES[element_type.name]
    except KeyError as exc:
        raise ValueError(f"unsupported Ogre vertex element type: {element_type!r}") from exc


def auto_organise_declaration(
    declaration: list[VertexElement],
    *,
    skeletal_animation: bool,
    vertex_animation: bool = False,
    vertex_animation_normals: bool = False,
) -> list[VertexElement]:
    """Mirror Ogre 1.11.6 VertexDeclaration::getAutoOrganisedDeclaration().

    Ogre first collapses every element to source 0, sorts by semantic/index,
    then splits streams around animated position/normal data and blend data.
    The resulting declaration is the layout OgreMeshUpgrader writes by
    default and is also the layout Battlezone 98 Redux expects for animated
    meshes.
    """

    ordered = sorted(
        declaration,
        key=lambda element: (
            int(vertex_semantic(element.semantic)),
            int(element.index),
        ),
    )

    result: list[VertexElement] = []
    offset = 0
    buffer = 0
    prev_semantic = VertexElementSemantic.POSITION

    for element in ordered:
        semantic = vertex_semantic(element.semantic)
        split_with_prev = False
        split_with_next = False

        if semantic is VertexElementSemantic.POSITION:
            split_with_next = vertex_animation and not vertex_animation_normals
        elif semantic is VertexElementSemantic.NORMAL:
            split_with_prev = prev_semantic in {
                VertexElementSemantic.BLEND_WEIGHTS,
                VertexElementSemantic.BLEND_INDICES,
            }
            split_with_next = skeletal_animation or (
                vertex_animation and vertex_animation_normals
            )
        elif semantic is VertexElementSemantic.BLEND_WEIGHTS:
            split_with_prev = True
        elif semantic is VertexElementSemantic.BLEND_INDICES:
            split_with_next = True
        else:
            split_with_prev = (
                prev_semantic is VertexElementSemantic.POSITION
                and (skeletal_animation or vertex_animation)
            )

        if split_with_prev and offset:
            buffer += 1
            offset = 0

        result.append(replace(element, source=buffer, offset=offset))
        prev_semantic = semantic

        if split_with_next:
            buffer += 1
            offset = 0
        else:
            offset += vertex_element_size(element.component_type)

    return result


def reorganise_geometry(
    geometry: GeometryData,
    *,
    skeletal_animation: bool,
    vertex_animation: bool = False,
    vertex_animation_normals: bool = False,
) -> GeometryData:
    """Repack vertex bytes into Ogre's recommended declaration/buffer layout.

    This is the pure-Python equivalent of VertexData::reorganiseBuffers().
    Attribute values are copied byte-for-byte by semantic and semantic index;
    no float, UV, normal, tangent, or colour conversion occurs.
    """

    new_declaration = auto_organise_declaration(
        geometry.declaration,
        skeletal_animation=skeletal_animation,
        vertex_animation=vertex_animation,
        vertex_animation_normals=vertex_animation_normals,
    )

    if new_declaration == geometry.declaration:
        return geometry

    old_buffers = {buffer.bind_index: buffer for buffer in geometry.buffers}
    old_elements: dict[tuple[int, int], VertexElement] = {}

    for element in geometry.declaration:
        key = (int(vertex_semantic(element.semantic)), int(element.index))
        if key in old_elements:
            raise ValueError(
                "duplicate Ogre vertex semantic/index in declaration: "
                f"{element.semantic}[{element.index}]"
            )
        old_elements[key] = element

    for buffer in geometry.buffers:
        expected = geometry.vertex_count * buffer.vertex_size
        if len(buffer.data) != expected:
            raise ValueError(
                f"vertex buffer {buffer.bind_index} has {len(buffer.data)} bytes; "
                f"expected {expected}"
            )

    new_strides: dict[int, int] = {}
    for element in new_declaration:
        end = element.offset + vertex_element_size(element.component_type)
        new_strides[element.source] = max(new_strides.get(element.source, 0), end)

    new_data = {
        source: bytearray(geometry.vertex_count * stride)
        for source, stride in new_strides.items()
    }

    for new_element in new_declaration:
        key = (
            int(vertex_semantic(new_element.semantic)),
            int(new_element.index),
        )
        try:
            old_element = old_elements[key]
        except KeyError as exc:
            raise ValueError(
                "new Ogre vertex declaration element has no source element: "
                f"{new_element.semantic}[{new_element.index}]"
            ) from exc

        try:
            old_buffer = old_buffers[old_element.source]
        except KeyError as exc:
            raise ValueError(
                f"vertex declaration references missing buffer {old_element.source}"
            ) from exc

        size = vertex_element_size(new_element.component_type)
        old_size = vertex_element_size(old_element.component_type)
        if old_size != size:
            raise ValueError(
                "Ogre vertex reorganisation cannot change element size for "
                f"{new_element.semantic}[{new_element.index}]"
            )
        if old_element.offset + size > old_buffer.vertex_size:
            raise ValueError(
                f"vertex element {old_element.semantic}[{old_element.index}] "
                f"extends past buffer {old_buffer.bind_index} stride"
            )

        new_stride = new_strides[new_element.source]
        target = new_data[new_element.source]
        source = old_buffer.data

        for vertex_index in range(geometry.vertex_count):
            src_start = vertex_index * old_buffer.vertex_size + old_element.offset
            dst_start = vertex_index * new_stride + new_element.offset
            target[dst_start : dst_start + size] = source[src_start : src_start + size]

    buffers = [
        VertexBuffer(source, new_strides[source], bytes(new_data[source]))
        for source in sorted(new_strides)
    ]
    return replace(
        geometry,
        declaration=new_declaration,
        buffers=buffers,
    )


def auto_organise_mesh(mesh: MeshData) -> MeshData:
    """Return a mesh with OgreMeshUpgrader-compatible vertex organisation.

    The current pure serializer does not emit Ogre mesh-animation tracks, so
    vertex_animation is false here. Skeleton links still trigger Ogre's
    skeletal stream split exactly as they do in Ogre 1.11.6.
    """

    skeletal_animation = bool(mesh.skeleton_name)

    shared_geometry = mesh.shared_geometry
    if shared_geometry is not None:
        shared_geometry = reorganise_geometry(
            shared_geometry,
            skeletal_animation=skeletal_animation,
        )

    submeshes = []
    for submesh in mesh.submeshes:
        geometry = submesh.geometry
        if geometry is not None and not submesh.use_shared_vertices:
            geometry = reorganise_geometry(
                geometry,
                skeletal_animation=skeletal_animation,
            )
        submeshes.append(replace(submesh, geometry=geometry))

    return replace(
        mesh,
        shared_geometry=shared_geometry,
        submeshes=submeshes,
    )
