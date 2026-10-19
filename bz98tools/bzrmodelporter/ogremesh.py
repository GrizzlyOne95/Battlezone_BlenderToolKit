from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

import itertools
import numpy as np

BOOL_SIZE = 1
UBYTE_SIZE = 1
SBYTE_SIZE = 1
USHORT_SIZE = 2
SSHORT_SIZE = 2
UINT_SIZE = 4
SINT_SIZE = 4
FLOAT_SIZE = 4
COLOR_SIZE = 3
DOUBLE_SIZE = 8


class OT:
    POINT_LIST = 0x0001
    LINE_LIST = 0x0002
    LINE_STRIP = 0x0003
    TRIANGLE_LIST = 0x0004
    TRIANGLE_STRIP = 0x0005
    TRIANGLE_FAN = 0x0006
    PATCH_1_CONTROL_POINT = 0x0007
    PATCH_2_CONTROL_POINT = 0x0008
    PATCH_3_CONTROL_POINT = 0x0009
    PATCH_4_CONTROL_POINT = 0x000A
    PATCH_5_CONTROL_POINT = 0x000B
    PATCH_6_CONTROL_POINT = 0x000C
    PATCH_7_CONTROL_POINT = 0x000D
    PATCH_8_CONTROL_POINT = 0x000E
    PATCH_9_CONTROL_POINT = 0x000F
    PATCH_10_CONTROL_POINT = 0x0010
    PATCH_11_CONTROL_POINT = 0x0011
    PATCH_12_CONTROL_POINT = 0x0012
    PATCH_13_CONTROL_POINT = 0x0013
    PATCH_14_CONTROL_POINT = 0x0014
    PATCH_15_CONTROL_POINT = 0x0015
    PATCH_16_CONTROL_POINT = 0x0016
    PATCH_17_CONTROL_POINT = 0x0017
    PATCH_18_CONTROL_POINT = 0x0018
    PATCH_19_CONTROL_POINT = 0x0019
    PATCH_20_CONTROL_POINT = 0x001A
    PATCH_21_CONTROL_POINT = 0x001B
    PATCH_22_CONTROL_POINT = 0x001C
    PATCH_23_CONTROL_POINT = 0x001D
    PATCH_24_CONTROL_POINT = 0x001E
    PATCH_25_CONTROL_POINT = 0x001F
    PATCH_26_CONTROL_POINT = 0x0020
    PATCH_27_CONTROL_POINT = 0x0021
    PATCH_28_CONTROL_POINT = 0x0022
    PATCH_29_CONTROL_POINT = 0x0023
    PATCH_30_CONTROL_POINT = 0x0024
    PATCH_31_CONTROL_POINT = 0x0025
    PATCH_32_CONTROL_POINT = 0x0026

    DETAIL_ADJACENCY_BIT = 1 << 6
    POINT_LIST_ADJ = POINT_LIST | DETAIL_ADJACENCY_BIT
    LINE_LIST_ADJ = LINE_LIST | DETAIL_ADJACENCY_BIT
    LINE_STRIP_ADJ = LINE_STRIP | DETAIL_ADJACENCY_BIT
    TRIANGLE_LIST_ADJ = TRIANGLE_LIST | DETAIL_ADJACENCY_BIT
    TRIANGLE_STRIP_ADJ = TRIANGLE_STRIP | DETAIL_ADJACENCY_BIT
    TRIANGLE_FAN_ADJ = TRIANGLE_FAN | DETAIL_ADJACENCY_BIT

    name_map = {
        POINT_LIST: "POINT_LIST",
        LINE_LIST: "LINE_LIST",
        LINE_STRIP: "LINE_STRIP",
        TRIANGLE_LIST: "TRIANGLE_LIST",
        TRIANGLE_STRIP: "TRIANGLE_STRIP",
        TRIANGLE_FAN: "TRIANGLE_FAN",
        POINT_LIST | DETAIL_ADJACENCY_BIT: "POINT_LIST_ADJ",
        LINE_LIST | DETAIL_ADJACENCY_BIT: "LINE_LIST_ADJ",
        LINE_STRIP | DETAIL_ADJACENCY_BIT: "LINE_STRIP_ADJ",
        TRIANGLE_LIST | DETAIL_ADJACENCY_BIT: "TRIANGLE_LIST_ADJ",
        TRIANGLE_STRIP | DETAIL_ADJACENCY_BIT: "TRIANGLE_STRIP_ADJ",
        TRIANGLE_FAN | DETAIL_ADJACENCY_BIT: "TRIANGLE_FAN_ADJ",
    }

    def label(ot, full=False):
        if ot in OT.name_map:
            if full:
                return f"{OT.name_map[ot]}[0x{ot:04X}]"
            else:
                return OT.name_map[ot]
        else:
            return f"OT[0x{ot:04X}]"

    def isvalid(ot):
        return ot in OT.name_map


# VertexElementSemantic
class VES:
    POSITION = 0x0001
    BLEND_WEIGHTS = 0x0002
    BLEND_INDICES = 0x0003
    NORMAL = 0x0004
    COLOUR = 0x0005
    COLOUR2 = 0x0006
    TEXTURE_COORDINATES = 0x0007
    BINORMAL = 0x0008
    TANGENT = 0x0009

    name_map = {
        POSITION: "POSITION",
        BLEND_WEIGHTS: "BLEND_WEIGHTS",
        BLEND_INDICES: "BLEND_INDICES",
        NORMAL: "NORMAL",
        COLOUR: "COLOUR",
        COLOUR2: "COLOUR2",
        TEXTURE_COORDINATES: "TEXTURE_COORDINATES",
        BINORMAL: "BINORMAL",
        TANGENT: "TANGENT",
    }

    def label(ves, full=False):
        if ves in VES.name_map:
            if full:
                return f"{VES.name_map[ves]}[0x{ves:04X}]"
            else:
                return VES.name_map[ves]
        else:
            return f"VES[0x{ves:04X}]"

    def isvalid(ves):
        return ves in VES.name_map


# VertexElementType
class VET:
    FLOAT1 = 0x00
    FLOAT2 = 0x01
    FLOAT3 = 0x02
    FLOAT4 = 0x03
    COLOUR = 0x04  # Deprecated in favor of VET.UBYTE4_NORM
    SHORT1 = 0x05  # Deprecated outright (alignment issues)
    SHORT2 = 0x06
    SHORT3 = 0x07  # Deprecated outright (alignment issues)
    SHORT4 = 0x08
    UBYTE4 = 0x09
    COLOUR_ARGB = 0x0A  # Deprecated in favor of VET.UBYTE4_NORM
    COLOUR_ABGR = 0x0B  # Deprecated in favor of VET.UBYTE4_NORM
    DOUBLE1 = 0x0C
    DOUBLE2 = 0x0D
    DOUBLE3 = 0x0E
    DOUBLE4 = 0x0F
    USHORT1 = 0x10  # Deprecated outright (alignment issues)
    USHORT2 = 0x11
    USHORT3 = 0x12  # Deprecated outright (alignment issues)
    USHORT4 = 0x13
    INT1 = 0x14
    INT2 = 0x15
    INT3 = 0x16
    INT4 = 0x17
    UINT1 = 0x18
    UINT2 = 0x19
    UINT3 = 0x1A
    UINT4 = 0x1B
    BYTE4 = 0x1C
    BYTE4_NORM = 0x1D
    UBYTE4_NORM = 0x1E
    SHORT2_NORM = 0x1F
    SHORT4_NORM = 0x20
    USHORT2_NORM = 0x21
    USHORT4_NORM = 0x22

    name_map = {
        FLOAT1: "FLOAT1",
        FLOAT2: "FLOAT2",
        FLOAT3: "FLOAT3",
        FLOAT4: "FLOAT4",
        COLOUR: "COLOUR",
        SHORT1: "SHORT1",
        SHORT2: "SHORT2",
        SHORT3: "SHORT3",
        SHORT4: "SHORT4",
        UBYTE4: "UBYTE4",
        COLOUR_ARGB: "COLOUR_ARGB",
        COLOUR_ABGR: "COLOUR_ABGR",
        DOUBLE1: "DOUBLE1",
        DOUBLE2: "DOUBLE2",
        DOUBLE3: "DOUBLE3",
        DOUBLE4: "DOUBLE4",
        USHORT1: "USHORT1",
        USHORT2: "USHORT2",
        USHORT3: "USHORT3",
        USHORT4: "USHORT4",
        INT1: "INT1",
        INT2: "INT2",
        INT3: "INT3",
        INT4: "INT4",
        UINT1: "UINT1",
        UINT2: "UINT2",
        UINT3: "UINT3",
        UINT4: "UINT4",
        BYTE4: "BYTE4",
        BYTE4_NORM: "BYTE4_NORM",
        UBYTE4_NORM: "UBYTE4_NORM",
        SHORT2_NORM: "SHORT2_NORM",
        SHORT4_NORM: "SHORT4_NORM",
        USHORT2_NORM: "USHORT2_NORM",
        USHORT4_NORM: "USHORT4_NORM",
    }

    def label(vet, full=False):
        if vet in VET.name_map:
            if full:
                return f"{VET.name_map[vet]}[0x{vet:04X}]"
            else:
                return VET.name_map[vet]
        else:
            return f"VET[0x{vet:04X}]"

    size_map = {
        FLOAT1: 1 * FLOAT_SIZE,
        FLOAT2: 2 * FLOAT_SIZE,
        FLOAT3: 3 * FLOAT_SIZE,
        FLOAT4: 4 * FLOAT_SIZE,
        DOUBLE1: 1 * DOUBLE_SIZE,
        DOUBLE2: 2 * DOUBLE_SIZE,
        DOUBLE3: 3 * DOUBLE_SIZE,
        DOUBLE4: 4 * DOUBLE_SIZE,
        BYTE4: 4 * SBYTE_SIZE,
        BYTE4_NORM: 4 * SBYTE_SIZE,
        UBYTE4: 4 * UBYTE_SIZE,
        UBYTE4_NORM: 4 * UBYTE_SIZE,
        SHORT1: 1 * SSHORT_SIZE,
        SHORT2: 2 * SSHORT_SIZE,
        SHORT3: 3 * SSHORT_SIZE,
        SHORT4: 4 * SSHORT_SIZE,
        SHORT2_NORM: 2 * SSHORT_SIZE,
        SHORT4_NORM: 4 * SSHORT_SIZE,
        USHORT1: 1 * USHORT_SIZE,
        USHORT2: 2 * USHORT_SIZE,
        USHORT3: 3 * USHORT_SIZE,
        USHORT4: 4 * USHORT_SIZE,
        USHORT2_NORM: 2 * USHORT_SIZE,
        USHORT4_NORM: 4 * USHORT_SIZE,
        INT1: 1 * SINT_SIZE,
        INT2: 2 * SINT_SIZE,
        INT3: 3 * SINT_SIZE,
        INT4: 4 * SINT_SIZE,
        UINT1: 1 * UINT_SIZE,
        UINT2: 2 * UINT_SIZE,
        UINT3: 3 * UINT_SIZE,
        UINT4: 4 * UINT_SIZE,
        COLOUR: 4 * UBYTE_SIZE,
        COLOUR_ARGB: 4 * UBYTE_SIZE,
        COLOUR_ABGR: 4 * UBYTE_SIZE,
    }

    def typesize(vet, default=4):
        if vet in VET.size_map:
            return VET.size_map[vet]
        else:
            return default

    count_map = {
        FLOAT1: 1,
        FLOAT2: 2,
        FLOAT3: 3,
        FLOAT4: 4,
        DOUBLE1: 1,
        DOUBLE2: 2,
        DOUBLE3: 3,
        DOUBLE4: 4,
        BYTE4: 4,
        BYTE4_NORM: 4,
        UBYTE4: 4,
        UBYTE4_NORM: 4,
        SHORT1: 1,
        SHORT2: 2,
        SHORT3: 3,
        SHORT4: 4,
        SHORT2_NORM: 2,
        SHORT4_NORM: 4,
        USHORT1: 1,
        USHORT2: 2,
        USHORT3: 3,
        USHORT4: 4,
        USHORT2_NORM: 2,
        USHORT4_NORM: 4,
        INT1: 1,
        INT2: 2,
        INT3: 3,
        INT4: 4,
        UINT1: 1,
        UINT2: 2,
        UINT3: 3,
        UINT4: 4,
        COLOUR: 4,
        COLOUR_ARGB: 4,
        COLOUR_ABGR: 4,
    }

    def typecount(vet, default=4):
        if vet in VET.count_map:
            return VET.count_map[vet]
        else:
            return default

    count_type_map = {
        FLOAT1: [FLOAT1, FLOAT2, FLOAT3, FLOAT4],
        FLOAT2: [FLOAT1, FLOAT2, FLOAT3, FLOAT4],
        FLOAT3: [FLOAT1, FLOAT2, FLOAT3, FLOAT4],
        FLOAT4: [FLOAT1, FLOAT2, FLOAT3, FLOAT4],
        DOUBLE1: [DOUBLE1, DOUBLE2, DOUBLE3, DOUBLE4],
        DOUBLE2: [DOUBLE1, DOUBLE2, DOUBLE3, DOUBLE4],
        DOUBLE3: [DOUBLE1, DOUBLE2, DOUBLE3, DOUBLE4],
        DOUBLE4: [DOUBLE1, DOUBLE2, DOUBLE3, DOUBLE4],
        BYTE4: [BYTE4, BYTE4, BYTE4, BYTE4],
        BYTE4_NORM: [BYTE4_NORM, BYTE4_NORM, BYTE4_NORM, BYTE4_NORM],
        UBYTE4: [UBYTE4, UBYTE4, UBYTE4, UBYTE4],
        UBYTE4_NORM: [UBYTE4_NORM, UBYTE4_NORM, UBYTE4_NORM, UBYTE4_NORM],
        SHORT1: [SHORT1, SHORT2, SHORT3, SHORT4],
        SHORT2: [SHORT1, SHORT2, SHORT3, SHORT4],
        SHORT3: [SHORT1, SHORT2, SHORT3, SHORT4],
        SHORT4: [SHORT1, SHORT2, SHORT3, SHORT4],
        SHORT2_NORM: [SHORT2_NORM, SHORT2_NORM, SHORT4_NORM, SHORT4_NORM],
        SHORT4_NORM: [SHORT2_NORM, SHORT2_NORM, SHORT4_NORM, SHORT4_NORM],
        USHORT1: [USHORT1, USHORT2, USHORT3, USHORT4],
        USHORT2: [USHORT1, USHORT2, USHORT3, USHORT4],
        USHORT3: [USHORT1, USHORT2, USHORT3, USHORT4],
        USHORT4: [USHORT1, USHORT2, USHORT3, USHORT4],
        USHORT2_NORM: [USHORT2_NORM, USHORT2_NORM, USHORT4_NORM, USHORT4_NORM],
        USHORT4_NORM: [USHORT2_NORM, USHORT2_NORM, USHORT4_NORM, USHORT4_NORM],
        INT1: [INT1, INT2, INT3, INT4],
        INT2: [INT1, INT2, INT3, INT4],
        INT3: [INT1, INT2, INT3, INT4],
        INT4: [INT1, INT2, INT3, INT4],
        UINT1: [UINT1, UINT2, UINT3, UINT4],
        UINT2: [UINT1, UINT2, UINT3, UINT4],
        UINT3: [UINT1, UINT2, UINT3, UINT4],
        UINT4: [UINT1, UINT2, UINT3, UINT4],
        COLOUR: [COLOUR, COLOUR, COLOUR, COLOUR],
        COLOUR_ARGB: [COLOUR_ARGB, COLOUR_ARGB, COLOUR_ARGB, COLOUR_ARGB],
        COLOUR_ABGR: [COLOUR_ABGR, COLOUR_ABGR, COLOUR_ABGR, COLOUR_ABGR],
    }

    def counttype(vet, count, default=BYTE4):
        if vet in VET.count_type_map:
            return VET.count_type_map[vet][count]
        else:
            return default

    base_map = {
        FLOAT1: FLOAT1,
        FLOAT2: FLOAT1,
        FLOAT3: FLOAT1,
        FLOAT4: FLOAT1,
        DOUBLE1: DOUBLE1,
        DOUBLE2: DOUBLE1,
        DOUBLE3: DOUBLE1,
        DOUBLE4: DOUBLE1,
        BYTE4: BYTE4,
        BYTE4_NORM: BYTE4_NORM,
        UBYTE4: UBYTE4,
        UBYTE4_NORM: UBYTE4_NORM,
        SHORT1: SHORT1,
        SHORT2: SHORT1,
        SHORT3: SHORT1,
        SHORT4: SHORT1,
        SHORT2_NORM: SHORT2_NORM,
        SHORT4_NORM: SHORT2_NORM,
        USHORT1: USHORT1,
        USHORT2: USHORT1,
        USHORT3: USHORT1,
        USHORT4: USHORT1,
        USHORT2_NORM: USHORT2_NORM,
        USHORT4_NORM: USHORT2_NORM,
        INT1: INT1,
        INT2: INT1,
        INT3: INT1,
        INT4: INT1,
        UINT1: UINT1,
        UINT2: UINT1,
        UINT3: UINT1,
        UINT4: UINT1,
        COLOUR: COLOUR,
        COLOUR_ARGB: COLOUR_ARGB,
        COLOUR_ABGR: COLOUR_ABGR,
    }

    @staticmethod
    def isvalid(vet):
        return vet in VET.name_map


class AxisAlignedBoundingBox:
    def __init__(
        self, min_x=0.0, min_y=0.0, min_z=0.0, max_x=0.0, max_y=0.0, max_z=0.0
    ):
        self.min_x = min_x
        self.min_y = min_y
        self.min_z = min_z
        self.max_x = max_x
        self.max_y = max_y
        self.max_z = max_z

    def scale_from_midpoint(self, scale_x, scale_y, scale_z):
        mid_x = (self.min_x + self.max_x) * 0.5
        mid_y = (self.min_y + self.max_y) * 0.5
        mid_z = (self.min_z + self.max_z) * 0.5
        size_x = self.max_x - self.min_x
        size_y = self.max_y - self.min_y
        size_z = self.max_z - self.min_z
        self.min_x = mid_x - size_x * scale_x * 0.5
        self.min_y = mid_y - size_y * scale_y * 0.5
        self.min_z = mid_z - size_z * scale_z * 0.5
        self.max_x = mid_x + size_x * scale_x * 0.5
        self.max_y = mid_y + size_y * scale_y * 0.5
        self.max_z = mid_z + size_z * scale_z * 0.5

    def __repr__(self):
        return f"AxisAlignedBoundingBox(min_x={self.min_x}, min_y={self.min_y}, min_z={self.min_z}, max_x={self.max_x}, max_y={self.max_y}, max_z{self.max_z})"


class VertexElement:
    def __init__(self, source=0, type=0, semantic=0, offset=0, index=0):
        if not VET.isvalid(type):
            raise ValueError(f"Invalid VET: {VET.label(type)}")
        if not VES.isvalid(semantic):
            raise ValueError(f"Invalid VES: {VES.label(semantic)}")
        self.source = source
        self.type = type  # VertexElementType
        self.semantic = semantic  # VertexElementSemantic
        self.offset = offset
        self.index = index

    def get_type_size(self):
        return VET.size_map[self.type]

    def get_type_count(self):
        return VET.count_map[self.type]

    def get_base_type(self):
        return VET.base_map[self.type]

    def __repr__(self):
        return f"VertexElement(source={self.source}, type={self.type}, semantic={self.semantic}, offset={self.offset}, index={self.index})"


class VertexBuffer:
    def __init__(self, buffer, bind_index, vertex_size):
        self.set_buffer(buffer)
        self.bind_index = bind_index
        self.vertex_size = vertex_size

    def set_buffer(self, buf):
        self.buffer = memoryview(buf)

    def validate_buffer_size(self, vertex_count):
        """Verify that the buffer size matches the expected size for the given vertex count."""
        expected_size = vertex_count * self.vertex_size
        actual_size = self.buffer.nbytes
        if actual_size != expected_size:
            raise ValueError(
                f"VertexBuffer size mismatch for bind index {self.bind_index}: "
                f"expected {expected_size} bytes ({vertex_count} vertices * {self.vertex_size} bytes), "
                f"but got {actual_size} bytes."
            )

    def get_buffer(self):
        return self.buffer.obj

    def get_buffer_size(self):
        return memview(self.buffer).nbytes

    def __repr__(self):
        return f"VertexBuffer(bind_index={self.bind_index}, vertex_size: {self.vertex_size}, buffer: [{len(self.buffer)}])"


class VertexDeclaration:
    def __init__(self):
        self.vertex_element_list = []

    def create_vertex_element(self, source=0, type=0, semantic=0, offset=0, index=0):
        ve = VertexElement(source, type, semantic, offset, index)
        self.vertex_element_list.append(ve)
        return ve

    def add_element(self, source, type, semantic, index=0):
        """Add a vertex element, automatically calculating the offset based on existing elements in the same source."""
        offset = self.compute_vertex_size(source)
        return self.create_vertex_element(source, type, semantic, offset, index)

    def clear(self):
        self.vertex_element_list.clear()

    def find_by_semantic(self, semantic):
        for ve in self.vertex_element_list:
            if ve.semantic == semantic:
                return ve
        return None

    def list_by_source(self, source):
        return [ve for ve in self.vertex_element_list if ve.source == source]

    def compute_vertex_size(self, source):
        size = 0
        for ve in self.vertex_element_list:
            if ve.source == source:
                size += ve.get_type_size()
        return size

    def sort(self):
        self.vertex_element_list.sort(key=lambda ve: (ve.source, ve.semantic, ve.index))

    def get_position_vertex_element(self):
        return self.find_by_semantic(VES.POSITION)

    def get_normal_vertex_element(self):
        return self.find_by_semantic(VES.NORMAL)

    def get_texcoord_vertex_element(self):
        return self.find_by_semantic(VES.TEXTURE_COORDINATES)


class VertexData:
    def __init__(self, vertex_count=0):
        self.set_vertex_count(vertex_count)
        self.vertex_declaration = (
            VertexDeclaration()
        )  # Declaration has list of VertexElement objects
        self.vertex_buffer_map = {}  # Maps bind index (source) to vertex buffer

    def set_vertex_count(self, vertex_count):
        self.vertex_count = vertex_count

    def create_vertex_buffer(self, buffer, bind_index, vertex_size):
        vb = VertexBuffer(buffer, bind_index, vertex_size)
        self.vertex_buffer_map[vb.bind_index] = vb
        return vb

    def get_vertex_buffer(self, bind_index):
        return self.vertex_buffer_map[bind_index]

    def vertex_buffers(self):
        return sorted(self.vertex_buffer_map.items())

    def __repr__(self):
        return f"VertexData()"


_bone_assignment_sequence = itertools.count()


class VertexBoneAssignment:
    def __init__(self, vertex_index=0, bone_index=0, weight=1.0):
        self.vertex_index = vertex_index
        self.bone_index = bone_index
        self.weight = weight
        # Creation order, so a writer can keep the order assignments were read in.
        self.sequence = next(_bone_assignment_sequence)

    def __str__(self):
        return f"{self.vertex_index}->{self.bone_index}: {self.weight}"

    def __repr__(self):
        return f"VertexBoneAssignment(vertex_index={self.vertex_index}, bone_index={self.bone_index}, weight={self.weight})"


class SubMesh:
    def __init__(self, parent, index):
        self.parent = parent  # Parent Mesh object
        self.submesh_index = index
        self.material_name = None
        self.use_shared_vertices = False
        self.index_count = 0
        self.indices_32_bit = False
        self.index_array = None

        self.operation_type = OT.TRIANGLE_LIST
        self.vertex_data = None
        self.bone_assignment_map = (
            {}
        )  # Maps vertex indices from vertex bone assignments onto lists of those VertexBoneAssignment objects.

        self.texture_alias_map = {}  # Maps texture alias names to texture names

    def get_name(self):
        if self.submesh_index in self.parent.name_table:
            return self.parent.name_table[self.submesh_index]
        return None

    def compute_index_buffer_size(self):
        return self.index_count * (4 if self.indices_32_bit else 2)

    def set_index_buffer(self, buf):
        buf = memoryview(buf)
        if buf.nbytes != self.compute_index_buffer_size():
            raise ValueError("Index buffer is not the correct size!")
        if self.indices_32_bit:
            self.index_array = np.frombuffer(buf, dtype="<I")
        else:
            self.index_array = np.frombuffer(buf, dtype="<H")

    def get_index_buffer(self):
        return self.index_array.data

    def get_index_array(self):
        return self.index_array

    def get_index_buffer_size(self):
        return self.index_array.nbytes

    def create_vertex_data(self, vertex_count=0):
        self.vertex_data = VertexData(vertex_count)
        return self.vertex_data

    # VertexBoneAssignment Methods
    def get_bone_assignment_count(self):
        count = 0
        for vba_list in self.bone_assignment_map.values():
            count += len(vba_list)
        return count

    def bone_assignments(self):
        return sorted(
            itertools.chain.from_iterable(self.bone_assignment_map.values()),
            key=lambda vba: (vba.vertex_index, vba.bone_index),
        )

    def bone_assignments_unsorted(self):
        return itertools.chain.from_iterable(self.bone_assignment_map.values())

    def bone_assignments_in_creation_order(self):
        """Bone assignments in the order they were created (file order for
        a mesh the serializer read)."""
        return sorted(
            itertools.chain.from_iterable(self.bone_assignment_map.values()),
            key=lambda vba: vba.sequence,
        )

    def bone_assignments_by_vertex_index(self, vertex_index):
        if vertex_index not in self.bone_assignment_map:
            return iter(())  # empty iterator
        return iter(self.bone_assignment_map[vertex_index])

    def bone_assignments_by_bone_index(self, bone_index):
        return filter(
            lambda vba: vba.bone_index == bone_index,
            itertools.chain.from_iterable(self.bone_assignment_map.values()),
        )

    def assigned_vertices(self):
        return set(self.bone_assignments.keys())

    def assigned_bones(self):
        return set(
            vba.bone_index
            for vba in itertools.chain.from_iterable(self.bone_assignment_mape.values())
        )

    def assigned_bones_by_vertex(self, vertex_index):
        return (
            vba.bone_index
            for vba in self.bone_assignments_by_vertex_index(vertex_index)
        )

    def assigned_vertices_by_bone(self, bone_index):
        return (
            vba.vertex_index for vba in self.bone_assignments_by_bone_index(bone_index)
        )

    def create_bone_assignment(self, vertex_index, bone_index, weight):
        vba = VertexBoneAssignment(vertex_index, bone_index, weight)
        if vertex_index not in self.bone_assignment_map:
            self.bone_assignment_map[vertex_index] = []
        self.bone_assignment_map[vertex_index].append(vba)

    def clear_bone_assignments(self):
        self.bone_assignment_map.clear()

    def remap_assigned_bones(self, map):
        for vba in self.bone_assignments_unsorted():
            if vba.bone_index in map:
                vba.bone_index = map[vba.bone_index]

    def remap_assigned_vertices(self, map):
        self.bone_assignment_map = {
            map.get(vertex_index, vertex_index): vba_list
            for vertex_index, vba_list in self.bone_assignment_map.items()
        }
        for vertex_index, vba_list in self.bone_assignment_map.items():
            for vba in vba_list:
                vba.vertex_index = vertex_index

    def delete_bone_assignments(self, bone_handles):
        remove_list = []
        for vindex, vba_list in self.bone_assignment_map.items():
            for i in range(len(vba_list) - 1, -1, -1):
                if vba_list[i].bone_index in bone_handles:
                    del vba_list[i]
            if len(vba_list) == 0:
                remove_list.append(vindex)
        for vindex in remove_list:
            del self.bone_assignment_map[vindex]


class Edge:
    def __init__(self):
        self.tri_index0 = 0
        self.tri_index1 = 0
        self.vert_index0 = 0
        self.vert_index1 = 0
        self.shared_vert_index0 = 0
        self.shared_vert_index1 = 0
        self.degenerate = False


class EdgeGroup:
    def __init__(self):
        self.vertex_set = 0
        self.tri_start = 0
        self.tri_count = 0
        self.edge_list = []


class EdgeTriangle:
    def __init__(self):
        self.index_set = 0
        self.vertex_set = 0
        self.vert_indices = [0, 0, 0]
        self.shared_vert_indices = [0, 0, 0]
        self.normal = [0.0, 0.0, 0.0, 0.0]


class EdgeData:
    def __init__(self):
        self.is_manual = False  # Manual LOD edge lists carry no data
        self.is_closed = False
        self.triangles = []
        self.edge_groups = []


class Mesh:
    def __init__(self):
        self.shared_vertex_data = None
        self.submesh_list = []
        self.aabb = AxisAlignedBoundingBox()
        self.bound_radius = 0.0
        self.skeletally_animated = (
            True  # This property does not exist in the real OGRE Mesh class
        )
        self.skeleton_name = None
        self.bone_assignment_map = (
            {}
        )  # Maps vertex indices from VBAs onto lists of those VertexBoneAssignment objects.
        self.name_table = {}
        self.edge_lists = {}  # Map LOD index to EdgeData
        # When True the writer keeps bone assignments in creation order
        # instead of sorting them by (vertex, bone). MeshSerializer.read sets
        # it so a read mesh writes back unchanged.
        self.preserve_bone_assignment_order = False
        # Chunks this toolkit does not model (LOD levels, poses, animations,
        # extremes), kept verbatim as (chunk_id, bytes incl. header) in file
        # order and written back where Ogre's MeshSerializer puts them.
        self.raw_chunks = []
        # Write SUBMESH_NAME_TABLE even when no submesh is named. The reader
        # sets it when the file had the chunk; new meshes write it only for names.
        self.write_empty_name_table = False

    def create_shared_vertex_data(self, vertex_count=0):
        self.shared_vertex_data = VertexData(vertex_count)
        return self.shared_vertex_data

    def create_submesh(self, name=None):
        index = len(self.submesh_list)
        submesh = SubMesh(self, index)
        self.submesh_list.append(submesh)
        if name is not None:
            self.name_table[index] = name
        return submesh

    def get_submesh(self, index):
        return self.submesh_list[index]

    def get_submesh_by_name(self, name):
        for index, name2 in self.name_table.items():
            if name2.lower() == name.lower():
                if 0 <= index < len(self.submesh_list):
                    return self.submesh_list[index]
        return None

    def submeshes(self):
        return iter(self.submesh_list)

    # VertexBoneAssignment Methods
    def get_bone_assignment_count(self):
        count = 0
        for vba_list in self.bone_assignment_map.values():
            count += len(vba_list)
        return count

    def bone_assignments(self):
        return sorted(
            itertools.chain.from_iterable(self.bone_assignment_map.values()),
            key=lambda vba: (vba.vertex_index, vba.bone_index),
        )

    def all_bone_assignments(self):  # Is this iterator actually useful?
        return itertools.chain(
            self.bone_assignments(),
            itertools.chain.from_iterable(
                submesh.bone_assignments() for submesh in self.submesh_list
            ),
        )

    def bone_assignments_unsorted(self):
        return itertools.chain.from_iterable(self.bone_assignment_map.values())

    def bone_assignments_in_creation_order(self):
        """Bone assignments in the order they were created (file order for
        a mesh the serializer read)."""
        return sorted(
            itertools.chain.from_iterable(self.bone_assignment_map.values()),
            key=lambda vba: vba.sequence,
        )

    def all_bone_assignments_unsorted(self):
        return itertools.chain(
            self.bone_assignments_unsorted(),
            itertools.chain.from_iterable(
                submesh.bone_assignments_unsorted() for submesh in self.submesh_list
            ),
        )

    def bone_assignments_by_vertex_index(self, vertex_index):
        if vertex_index not in self.bone_assignment_map:
            return iter(())  # empty iterator
        return iter(self.bone_assignment_map[vertex_index])

    def bone_assignments_by_bone_index(self, bone_index):
        return filter(
            lambda vba: vba.bone_index == bone_index,
            itertools.chain.from_iterable(self.bone_assignment_map.values()),
        )

    def all_bone_assignments_by_bone_index(self, bone_index):
        return itertools.chain(
            self.bone_assignments_by_bone_index(bone_index),
            itertools.chain.from_iterable(
                submesh.bone_assignments_by_bone_index(bone_index)
                for submesh in self.submesh_list
            ),
        )

    def assigned_vertices(self):
        return set(self.bone_assignments.keys())

    def assigned_bones(self):
        return set(
            vba.bone_index
            for vba in itertools.chain.from_iterable(self.bone_assignment_mape.values())
        )

    def all_assigned_bones(self):
        return set.union(
            self.assigned_bones(),
            *(submesh.assigned_bones for submesh in self.submesh_list),
        )

    def assigned_bones_by_vertex(self, vertex_index):
        return (
            vba.bone_index
            for vba in self.bone_assignments_by_vertex_index(vertex_index)
        )

    def assigned_vertices_by_bone(self, bone_index):
        return (
            vba.vertex_index for vba in self.bone_assignments_by_bone_index(bone_index)
        )

    def all_assigned_vertices_by_bone(self, bone_index):
        return (
            vba.vertex_index
            for vba in self.all_bone_assignments_by_bone_index(self.bone_index)
        )

    def create_bone_assignment(self, vertex_index, bone_index, weight):
        vba = VertexBoneAssignment(vertex_index, bone_index, weight)
        if vertex_index not in self.bone_assignment_map:
            self.bone_assignment_map[vertex_index] = []
        self.bone_assignment_map[vertex_index].append(vba)

    def clear_bone_assignments(self):
        self.bone_assignment_map.clear()

    def clear_all_bone_assignments(self):
        self.clear_bone_assignments()
        for submesh in self.submesh_list:
            submesh.clear_bone_assignments()

    def remap_assigned_bones(self, map):
        for vba in self.bone_assignments_unsorted():
            if vba.bone_index in map:
                vba.bone_index = map[vba.bone_index]

    def remap_all_assigned_bones(self, map):
        self.remap_assigned_bones(map)
        for submesh in self.submesh_list:
            submesh.remap_assigned_bones(map)

    def remap_assigned_vertices(self, map):
        self.bone_assignment_map = {
            map.get(vertex_index, vertex_index): vba_list
            for vertex_index, vba_list in self.bone_assignment_map.items()
        }
        for vertex_index, vba_list in self.bone_assignment_map.items():
            for vba in vba_list:
                vba.vertex_index = vertex_index

    def delete_bone_assignments(self, bone_handles):
        remove_list = []
        for vindex, vba_list in self.bone_assignment_map.items():
            for i in range(len(vba_list) - 1, -1, -1):
                if vba_list[i].bone_index in bone_handles:
                    del vba_list[i]
            if len(vba_list) == 0:
                remove_list.append(vindex)
        for vindex in remove_list:
            del self.bone_assignment_map[vindex]

    def delete_all_bone_assignments(self, bone_handles):
        self.delete_bone_assignments(bone_handles)
        for submesh in self.submesh_list:
            submesh.delete_bone_assignments(bone_handles)
