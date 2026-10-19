from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

from .ogremesh import (
    Mesh,
    AxisAlignedBoundingBox,
    OT,
    VES,
    VET,
    EdgeData,
    EdgeTriangle,
    EdgeGroup,
    Edge,
)

from .baseserializer import (
    BOOL_SIZE,
    USHORT_SIZE,
    UINT_SIZE,
    FLOAT_SIZE,
)

from .ogre_baseserializer import (
    OgreBaseSerializer,
    CHUNK_HEADER_SIZE,
    UnsupportedVersionError,
)

VERTEX_ELEMENT_SIZE = (
    CHUNK_HEADER_SIZE + 5 * USHORT_SIZE  # source, type, semantic, offset, index
)
SUBMESH_OPERATION_SIZE = CHUNK_HEADER_SIZE + USHORT_SIZE  # operation_type
BONE_ASSIGNMENT_SIZE = (
    CHUNK_HEADER_SIZE
    + UINT_SIZE  # vertex index
    + USHORT_SIZE  # bone index
    + FLOAT_SIZE  # weight
)
BOUNDS_INFO_SIZE = (
    CHUNK_HEADER_SIZE
    + 7 * FLOAT_SIZE  # min_x, min_y, min_z, max_x, max_y, max_z, bound_radius
)


class MeshChunkID:
    HEADER = 0x1000

    MESH = 0x3000

    SUBMESH = 0x4000
    SUBMESH_OPERATION = 0x4010
    SUBMESH_BONE_ASSIGNMENT = 0x4100
    SUBMESH_TEXTURE_ALIAS = 0x4200

    GEOMETRY = 0x5000
    GEOMETRY_VERTEX_DECLARATION = 0x5100
    GEOMETRY_VERTEX_ELEMENT = 0x5110
    GEOMETRY_VERTEX_BUFFER = 0x5200
    GEOMETRY_VERTEX_BUFFER_DATA = 0x5210

    MESH_SKELETON_LINK = 0x6000

    MESH_BONE_ASSIGNMENT = 0x7000

    MESH_LOD_LEVEL = 0x8000
    MESH_LOD_USAGE = 0x8100
    MESH_LOD_MANUAL = 0x8110
    MESH_LOD_GENERATED = 0x8120

    MESH_BOUNDS = 0x9000

    SUBMESH_NAME_TABLE = 0xA000
    SUBMESH_NAME_TABLE_ELEMENT = 0xA100

    EDGE_LISTS = 0xB000
    EDGE_LIST_LOD = 0xB100
    EDGE_GROUP = 0xB110

    POSES = 0xC000
    POSE = 0xC100
    POSE_VERTEX = 0xC111

    ANIMATIONS = 0xD000
    ANIMATION = 0xD100
    ANIMATION_BASEINFO = 0xD105
    ANIMATION_TRACK = 0xD110
    ANIMATION_MORPH_KEYFRAME = 0xD111
    ANIMATION_POSE_KEYFRAME = 0xD112
    ANIMATION_POSE_REF = 0xD113

    EXTREMES = 0xE000

    name_map = {
        HEADER: "HEADER",
        MESH: "MESH",
        SUBMESH: "SUBMESH",
        SUBMESH_OPERATION: "SUBMESH_OPERATION",
        SUBMESH_BONE_ASSIGNMENT: "SUBMESH_BONE_ASSIGNMENT",
        SUBMESH_TEXTURE_ALIAS: "SUBMESH_TEXTURE_ALIAS",
        GEOMETRY: "GEOMETRY",
        GEOMETRY_VERTEX_DECLARATION: "GEOMETRY_VERTEX_DECLARATION",
        GEOMETRY_VERTEX_ELEMENT: "GEOMETRY_VERTEX_ELEMENT",
        GEOMETRY_VERTEX_BUFFER: "GEOMETRY_VERTEX_BUFFER",
        GEOMETRY_VERTEX_BUFFER_DATA: "GEOMETRY_VERTEX_BUFFER_DATA",
        MESH_SKELETON_LINK: "MESH_SKELETON_LINK",
        MESH_BONE_ASSIGNMENT: "MESH_BONE_ASSIGNMENT",
        MESH_LOD_LEVEL: "MESH_LOD_LEVEL",
        MESH_LOD_USAGE: "MESH_LOD_USAGE",
        MESH_LOD_MANUAL: "MESH_LOD_MANUAL",
        MESH_LOD_GENERATED: "MESH_LOD_GENERATED",
        MESH_BOUNDS: "MESH_BOUNDS",
        SUBMESH_NAME_TABLE: "SUBMESH_NAME_TABLE",
        SUBMESH_NAME_TABLE_ELEMENT: "SUBMESH_NAME_TABLE_ELEMENT",
        EDGE_LISTS: "EDGE_LISTS",
        EDGE_LIST_LOD: "EDGE_LIST_LOD",
        EDGE_GROUP: "EDGE_GROUP",
        POSES: "POSES",
        POSE: "POSE",
        POSE_VERTEX: "POSE_VERTEX",
        ANIMATIONS: "ANIMATIONS",
        ANIMATION: "ANIMATION",
        ANIMATION_BASEINFO: "ANIMATION_BASEINFO",
        ANIMATION_TRACK: "ANIMATION_TRACK",
        ANIMATION_MORPH_KEYFRAME: "ANIMATION_MORPH_KEYFRAME",
        ANIMATION_POSE_KEYFRAME: "ANIMATION_POSE_KEYFRAME",
        ANIMATION_POSE_REF: "ANIMATION_POSE_REF",
        EXTREMES: "EXTREMES",
    }

    @staticmethod
    def label(id, full=False):
        if id in MeshChunkID.name_map:
            if full:
                return f"{MeshChunkID.name_map[id]}[0x{id:4X}]"
            else:
                return MeshChunkID.name_map[id]
        else:
            return f"MeshChunkID[0x{id:4X}]"

    def isvalid(id):
        return id in MeshChunkID.name_map


class MeshSerializer(OgreBaseSerializer):
    ChunkID = MeshChunkID

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Read Methods

    def read(self, mesh=None):
        if mesh is None:
            mesh = Mesh()
        self.read_file_header()
        self.read_specific_chunk_header(MeshChunkID.MESH)
        self.read_mesh(mesh)
        return mesh

    def read_file_header(self):
        header_id = self.read_raw(2)
        if header_id == b"\x00\x10":
            self.set_endian("little")
        elif header_id == b"\x10\x00":
            self.set_endian("big")
        else:
            raise Exception(f"Invalid header id {header_id}")

        self.version = self.read_string_nlt()
        if self.version not in {
            "[MeshSerializer_v1.100]",
            "[MeshSerializer_v1.8]",  # Am I actually doing anything
            "[MeshSerializer_v1.41]",  #   to support these versions?
        }:
            raise UnsupportedVersionError(
                f"Unsupported mesh file version {self.version}"
            )

    def read_mesh(self, mesh):
        mesh.skeletally_animated = self.read_bool()
        mesh.preserve_bone_assignment_order = True

        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break

            if chunk_id == MeshChunkID.GEOMETRY:
                self.read_geometry(mesh.create_shared_vertex_data())

            elif chunk_id == MeshChunkID.SUBMESH:
                self.read_submesh(mesh)

            elif chunk_id == MeshChunkID.MESH_SKELETON_LINK:
                self.read_skeleton_link(mesh)

            elif chunk_id == MeshChunkID.MESH_BONE_ASSIGNMENT:
                self.read_mesh_bone_assignement(mesh)

            elif chunk_id == MeshChunkID.MESH_LOD_LEVEL:
                self.read_mesh_lod_level(mesh)

            elif chunk_id == MeshChunkID.MESH_BOUNDS:
                self.read_mesh_bounds(mesh)

            elif chunk_id == MeshChunkID.SUBMESH_NAME_TABLE:
                mesh.write_empty_name_table = True
                self.read_submesh_name_table(mesh)

            elif chunk_id == MeshChunkID.EDGE_LISTS:
                self.read_edge_list(mesh)

            elif chunk_id == MeshChunkID.POSES:
                self.read_poses(mesh)

            elif chunk_id == MeshChunkID.ANIMATIONS:
                self.read_animations(mesh)

            elif chunk_id == MeshChunkID.EXTREMES:
                self.read_table_extremes(mesh)

            else:
                self.rollback_chunk_header()
                break

        self.pop_chunk(MeshChunkID.MESH)

    def read_geometry(self, vertex_data):
        vertex_data.set_vertex_count(self.read_uint())

        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id == MeshChunkID.GEOMETRY_VERTEX_DECLARATION:
                self.read_geometry_vertex_declaration(vertex_data.vertex_declaration)

            elif chunk_id == MeshChunkID.GEOMETRY_VERTEX_BUFFER:
                self.read_geometry_vertex_buffer(vertex_data)

            else:
                self.rollback_chunk_header()
                break

        self.pop_chunk(MeshChunkID.GEOMETRY)

    def read_geometry_vertex_declaration(self, vertex_declaration):
        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id == MeshChunkID.GEOMETRY_VERTEX_ELEMENT:
                self.read_geometry_vertex_element(vertex_declaration)
            else:
                self.rollback_chunk_header()
                break

        self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_DECLARATION)

    def read_geometry_vertex_element(self, vertex_declaration):
        source = self.read_ushort()
        type = self.read_ushort()
        semantic = self.read_ushort()
        offset = self.read_ushort()
        index = self.read_ushort()
        vertex_declaration.create_vertex_element(source, type, semantic, offset, index)
        self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_ELEMENT)

    def read_geometry_vertex_buffer(self, vertex_data):
        bind_index = self.read_ushort()
        vertex_size = self.read_ushort()

        self.read_specific_chunk_header(MeshChunkID.GEOMETRY_VERTEX_BUFFER_DATA)
        dec_vertex_size = vertex_data.vertex_declaration.compute_vertex_size(bind_index)
        if dec_vertex_size != vertex_size:
            print(
                f"WARNING: Buffer (bind index {bind_index}) vertex size {vertex_size} does not aggree with declaration {dec_vertex_size}"
            )

        vertex_data.create_vertex_buffer(
            self.read_raw(vertex_size * vertex_data.vertex_count),
            bind_index,
            vertex_size,
        )

        self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_BUFFER_DATA)
        self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_BUFFER)

    def read_submesh(self, mesh):
        # print(f"reading SUBMESH data at address {self.stream.tell()}")
        submesh = mesh.create_submesh()
        submesh.material_name = self.read_string_nlt()
        submesh.use_shared_vertices = self.read_bool()
        submesh.index_count = self.read_uint()
        submesh.indices_32_bit = self.read_bool()
        buffer_size = submesh.compute_index_buffer_size()
        submesh.set_index_buffer(self.read_raw(buffer_size))
        if submesh.get_index_buffer().nbytes < buffer_size:
            raise EOFError()

        # Geometry (VertexData)
        if not submesh.use_shared_vertices:
            self.read_specific_chunk_header(MeshChunkID.GEOMETRY)
            self.read_geometry(submesh.create_vertex_data())

        # Submesh Operations, Bone Assignments, and Texture Aliases
        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id == MeshChunkID.SUBMESH_OPERATION:
                self.read_submesh_operation(submesh)

            elif chunk_id == MeshChunkID.SUBMESH_BONE_ASSIGNMENT:
                self.read_submesh_bone_assignment(submesh)

            elif chunk_id == MeshChunkID.SUBMESH_TEXTURE_ALIAS:
                self.read_submesh_texture_alias(submesh)

            else:
                self.rollback_chunk_header()
                break

        self.pop_chunk(MeshChunkID.SUBMESH)

    def read_submesh_operation(self, submesh):
        submesh.operation_type = self.read_ushort()
        if not OT.isvalid(submesh.operation_type):
            raise Exception(
                f"Invalid operation type in SUBMESH_OPERATION: {submesh.operation_type} @{self.current_chunk_address()}"
            )
        self.pop_chunk(MeshChunkID.SUBMESH_OPERATION)

    def read_submesh_bone_assignment(self, submesh):
        vertex_index = self.read_uint()
        bone_index = self.read_ushort()
        weight = self.read_float()
        submesh.create_bone_assignment(vertex_index, bone_index, weight)
        self.pop_chunk(MeshChunkID.SUBMESH_BONE_ASSIGNMENT)

    def read_submesh_texture_alias(self, submesh):
        # Apparently texture aliases are deprecated
        alias_name = self.read_string_nlt()
        texture_name = self.read_string_nlt()
        submesh.texture_alias_map[alias_name] = texture_name
        self.pop_chunk(MeshChunkID.SUBMESH_TEXTURE_ALIAS)

    def read_skeleton_link(self, mesh):
        mesh.skeleton_name = self.read_string_nlt()
        self.pop_chunk(MeshChunkID.MESH_SKELETON_LINK)

    def read_mesh_bone_assignement(self, mesh):
        vertex_index = self.read_uint()
        bone_index = self.read_ushort()
        weight = self.read_float()
        # print(vertex_index, bone_index, weight)
        mesh.create_bone_assignment(vertex_index, bone_index, weight)
        self.pop_chunk(MeshChunkID.MESH_BONE_ASSIGNMENT)

    def read_raw_chunk(self, mesh, chunk_id):
        """Keep the current chunk verbatim in mesh.raw_chunks."""
        size = self.current_chunk_size()
        if size < CHUNK_HEADER_SIZE:
            raise ValueError(
                f"{MeshChunkID.label(chunk_id, full=True)} chunk at "
                f"@0x{self.current_chunk_address():X} has invalid size {size}"
            )
        body = self.read_raw(size - CHUNK_HEADER_SIZE)
        header = self.encode_chunk_header(chunk_id, size)
        mesh.raw_chunks.append((chunk_id, header + body))
        self.pop_chunk(chunk_id)

    def read_mesh_lod_level(self, mesh):
        self.read_raw_chunk(mesh, MeshChunkID.MESH_LOD_LEVEL)

    def read_mesh_bounds(self, mesh):
        mesh.aabb = AxisAlignedBoundingBox()
        mesh.aabb.min_x = self.read_float()
        mesh.aabb.min_y = self.read_float()
        mesh.aabb.min_z = self.read_float()
        mesh.aabb.max_x = self.read_float()
        mesh.aabb.max_y = self.read_float()
        mesh.aabb.max_z = self.read_float()
        mesh.bound_radius = self.read_float()
        self.pop_chunk(MeshChunkID.MESH_BOUNDS)

    def read_submesh_name_table(self, mesh):
        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id == MeshChunkID.SUBMESH_NAME_TABLE_ELEMENT:
                index = self.read_ushort()
                mesh.name_table[index] = self.read_string_nlt()
                # print(index, mesh.name_table[index])
                self.pop_chunk(MeshChunkID.SUBMESH_NAME_TABLE_ELEMENT)
            else:
                self.rollback_chunk_header()
                break
        self.pop_chunk(MeshChunkID.SUBMESH_NAME_TABLE)

    def read_edge_list(self, mesh):
        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id == MeshChunkID.EDGE_LIST_LOD:
                lod_index = self.read_ushort()
                is_manual = self.read_bool()
                edge_data = EdgeData()
                mesh.edge_lists[lod_index] = edge_data
                if not is_manual:
                    self.read_edge_list_lod_info(edge_data)
                else:
                    edge_data.is_manual = True
                    self.pop_chunk(MeshChunkID.EDGE_LIST_LOD)
            else:
                self.rollback_chunk_header()
                break
        self.pop_chunk(MeshChunkID.EDGE_LISTS)

    def read_edge_list_lod_info(self, edge_data):
        edge_data.is_closed = self.read_bool()
        triangle_count = self.read_uint()
        edge_group_count = self.read_uint()
        for i in range(triangle_count):
            et = EdgeTriangle()
            et.index_set = self.read_uint()
            et.vertex_set = self.read_uint()

            et.vert_indices[0] = self.read_uint()
            et.vert_indices[1] = self.read_uint()
            et.vert_indices[2] = self.read_uint()

            et.shared_vert_indices[0] = self.read_uint()
            et.shared_vert_indices[1] = self.read_uint()
            et.shared_vert_indices[2] = self.read_uint()

            et.normal[0] = self.read_float()
            et.normal[1] = self.read_float()
            et.normal[2] = self.read_float()
            et.normal[3] = self.read_float()
            edge_data.triangles.append(et)

        while True:
            try:
                chunk_id = self.read_chunk_header()
            except EOFError:
                break
            if chunk_id == MeshChunkID.EDGE_GROUP:
                eg = EdgeGroup()
                eg.vertex_set = self.read_uint()
                eg.tri_start = self.read_uint()
                eg.tri_count = self.read_uint()
                edge_count = self.read_uint()
                for i in range(edge_count):
                    e = Edge()
                    e.tri_index0 = self.read_uint()
                    e.tri_index1 = self.read_uint()

                    e.vert_index0 = self.read_uint()
                    e.vert_index1 = self.read_uint()

                    e.shared_vert_index0 = self.read_uint()
                    e.shared_vert_index1 = self.read_uint()

                    e.degenerate = self.read_bool()
                    eg.edge_list.append(e)
                edge_data.edge_groups.append(eg)
                self.pop_chunk(MeshChunkID.EDGE_GROUP)
            else:
                self.rollback_chunk_header()
                break
        self.pop_chunk(MeshChunkID.EDGE_LIST_LOD)

    def read_poses(self, mesh):
        self.read_raw_chunk(mesh, MeshChunkID.POSES)

    def read_animations(self, mesh):
        self.read_raw_chunk(mesh, MeshChunkID.ANIMATIONS)

    def read_table_extremes(self, mesh):
        self.read_raw_chunk(mesh, MeshChunkID.EXTREMES)

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Write Methods

    def write(self, mesh, version="[MeshSerializer_v1.100]"):
        self.version = version
        if self.version not in {"[MeshSerializer_v1.100]"}:
            raise UnsupportedVersionError(
                f"Version {self.version} does not currently have write support!"
            )
        self.write_file_header()
        self.write_mesh(mesh)
        if self.validate_chunk_sizes and self.are_chunks_remaining():
            print(
                f"WARNING: {len(self.chunk_stack)} chunks still remain in the chunk stack!"
            )
            print(self.chunk_stack)

    def write_file_header(self):
        self.write_ushort(MeshChunkID.HEADER)
        self.write_string_nlt(self.version)

    def write_mesh(self, mesh):
        # MESH
        size = self.calc_mesh_size(mesh)
        start_addr = self.stream.tell()
        self.write_chunk_header(MeshChunkID.MESH, size)
        self.write_bool(mesh.skeleton_name != None)  # skeletally animated
        if mesh.shared_vertex_data != None:
            self.write_geometry(mesh.shared_vertex_data)  # GEOMETRY
        for submesh in mesh.submesh_list:
            self.write_submesh(submesh, mesh)  # SUBMESH
        if mesh.skeleton_name != None:
            self.write_skeleton_link(mesh.skeleton_name)  # MESH_SKELETON_LINK
            for vba in self.ordered_bone_assignments(mesh, mesh):
                self.write_mesh_bone_assignment(vba)  # MESH_BONE_ASSIGNMENT

        self.write_raw_chunks(mesh, lod=True)  # MESH_LOD_LEVEL (kept from read)
        self.write_bounds_info(mesh)  # MESH_BOUNDS
        self.write_submesh_name_table(mesh)  # SUBMESH_NAME_TABLE
        self.write_edge_list(mesh)  # EDGE_LISTS
        self.write_raw_chunks(mesh, lod=False)  # POSES, ANIMATIONS, EXTREMES
        self.pop_chunk(MeshChunkID.MESH)

    @staticmethod
    def ordered_bone_assignments(mesh, owner):
        if mesh.preserve_bone_assignment_order:
            return owner.bone_assignments_in_creation_order()
        return owner.bone_assignments()

    @staticmethod
    def selected_raw_chunks(mesh, lod):
        for chunk_id, data in mesh.raw_chunks:
            if (chunk_id == MeshChunkID.MESH_LOD_LEVEL) == lod:
                yield data

    def write_raw_chunks(self, mesh, lod):
        for data in self.selected_raw_chunks(mesh, lod):
            self.write_raw(data)

    def encode_chunk_header(self, chunk_id, size):
        return chunk_id.to_bytes(USHORT_SIZE, self.endian) + size.to_bytes(
            UINT_SIZE, self.endian
        )

    def write_geometry(self, vertex_data):
        # GEOMETRY
        size = self.calc_geometry_size(vertex_data)
        self.write_chunk_header(MeshChunkID.GEOMETRY, size)
        self.write_uint(vertex_data.vertex_count)

        # GEOMETRY_VERTEX_DECLARATION
        size = self.calc_vertex_declaration_size(vertex_data.vertex_declaration)
        self.write_chunk_header(MeshChunkID.GEOMETRY_VERTEX_DECLARATION, size)

        for ve in vertex_data.vertex_declaration.vertex_element_list:
            # GEOMETRY_VERTEX_ELEMENT
            self.write_chunk_header(
                MeshChunkID.GEOMETRY_VERTEX_ELEMENT, VERTEX_ELEMENT_SIZE
            )
            self.write_ushort(ve.source)  # source (bind index)
            self.write_ushort(ve.type)  # type
            self.write_ushort(ve.semantic)  # semantic
            self.write_ushort(ve.offset)  # offset
            self.write_ushort(ve.index)  # index
            self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_ELEMENT)

        self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_DECLARATION)

        for vb in vertex_data.vertex_buffer_map.values():
            # GEOMETRY_VERTEX_BUFFER
            size = self.calc_vertex_buffer_size(vb)
            self.write_chunk_header(MeshChunkID.GEOMETRY_VERTEX_BUFFER, size)
            self.write_ushort(vb.bind_index)
            self.write_ushort(vb.vertex_size)

            # GEOMETRY_VERTEX_BUFFER_DATA
            size = self.calc_vertex_buffer_data_size(vb)
            self.write_chunk_header(MeshChunkID.GEOMETRY_VERTEX_BUFFER_DATA, size)
            self.write_raw(vb.buffer)
            self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_BUFFER_DATA)

            self.pop_chunk(MeshChunkID.GEOMETRY_VERTEX_BUFFER)

        self.pop_chunk(MeshChunkID.GEOMETRY)

    def write_submesh(self, submesh, mesh=None):
        # SUBMESH
        size = self.calc_submesh_size(submesh)
        self.write_chunk_header(MeshChunkID.SUBMESH, size)
        self.write_string_nlt(submesh.material_name)  # material name
        self.write_bool(submesh.use_shared_vertices)  # use shared vertices
        self.write_uint(submesh.index_count)  # index count
        self.write_bool(submesh.indices_32_bit)  # 16/32 bit indices
        self.write_raw(submesh.get_index_buffer())  # face index buffer

        if not submesh.use_shared_vertices:
            self.write_geometry(submesh.vertex_data)  # GEOMETRY

        for alias_name, texture_name in submesh.texture_alias_map.items():
            self.write_submesh_texture_alias(alias_name, texture_name)

        self.write_submesh_operation(submesh)  # SUBMESH_OPERATION

        if mesh is None:
            mesh = submesh.parent
        for vba in self.ordered_bone_assignments(mesh, submesh):
            self.write_submesh_bone_assignment(vba)  # SUBMESH_BONE_ASSIGNMENT

        self.pop_chunk(MeshChunkID.SUBMESH)

    def write_submesh_operation(self, submesh):
        # SUBMESH_OPERATION
        self.write_chunk_header(MeshChunkID.SUBMESH_OPERATION, SUBMESH_OPERATION_SIZE)
        self.write_ushort(submesh.operation_type)
        self.pop_chunk(MeshChunkID.SUBMESH_OPERATION)

    def write_submesh_bone_assignment(self, vba):
        # SUBMESH_BONE_ASSIGNMENT
        self.write_chunk_header(
            MeshChunkID.SUBMESH_BONE_ASSIGNMENT, BONE_ASSIGNMENT_SIZE
        )
        self.write_uint(vba.vertex_index)  # vertex index
        self.write_ushort(vba.bone_index)  # bone index
        self.write_float(vba.weight)  # weight
        self.pop_chunk(MeshChunkID.SUBMESH_BONE_ASSIGNMENT)

    def write_submesh_texture_alias(self, alias_name, texture_name):
        # SUBMESH_TEXTURE_ALIAS
        size = (
            CHUNK_HEADER_SIZE
            + self.calc_string_size(alias_name)
            + self.calc_string_size(texture_name)
        )
        self.write_chunk_header(MeshChunkID.SUBMESH_TEXTURE_ALIAS, size)
        self.write_string_nlt(alias_name)
        self.write_string_nlt(texture_name)
        self.pop_chunk(MeshChunkID.SUBMESH_TEXTURE_ALIAS)

    def write_skeleton_link(self, skeleton_name):
        # MESH_SKELETON_LINK
        size = self.calc_skeleton_link_size(skeleton_name)
        self.write_chunk_header(MeshChunkID.MESH_SKELETON_LINK, size)
        self.write_string_nlt(skeleton_name)  # skeleton name
        self.pop_chunk(MeshChunkID.MESH_SKELETON_LINK)

    def write_mesh_bone_assignment(self, vba):
        # MESH_BONE_ASSIGNMENT
        self.write_chunk_header(MeshChunkID.MESH_BONE_ASSIGNMENT, BONE_ASSIGNMENT_SIZE)
        self.write_uint(vba.vertex_index)  # vertex index
        self.write_ushort(vba.bone_index)  # bone index
        self.write_float(vba.weight)  # weight
        self.pop_chunk(MeshChunkID.MESH_BONE_ASSIGNMENT)

    def write_bounds_info(self, mesh):
        # MESH_BOUNDS
        self.write_chunk_header(MeshChunkID.MESH_BOUNDS, BOUNDS_INFO_SIZE)
        self.write_float(mesh.aabb.min_x)  # min_x
        self.write_float(mesh.aabb.min_y)  # min_y
        self.write_float(mesh.aabb.min_z)  # min_z
        self.write_float(mesh.aabb.max_x)  # max_x
        self.write_float(mesh.aabb.max_y)  # max_y
        self.write_float(mesh.aabb.max_z)  # max_z
        self.write_float(mesh.bound_radius)  # bound radius
        self.pop_chunk(MeshChunkID.MESH_BOUNDS)

    @staticmethod
    def writes_submesh_name_table(mesh):
        if mesh.write_empty_name_table:
            return True
        return any(name != None for name in mesh.name_table.values())

    def write_submesh_name_table(self, mesh):
        # SUBMESH_NAME_TABLE, only when a submesh is named (Ogre does the same)
        if not self.writes_submesh_name_table(mesh):
            return
        size = self.calc_submesh_name_table_size(mesh)
        self.write_chunk_header(MeshChunkID.SUBMESH_NAME_TABLE, size)
        for index, name in mesh.name_table.items():
            if name != None:
                self.write_submesh_name_table_element(
                    index, name
                )  # SUBMESH_NAME_TABLE_ELEMENT
        self.pop_chunk(MeshChunkID.SUBMESH_NAME_TABLE)

    def write_submesh_name_table_element(self, index, name):
        # SUBMESH_NAME_TABLE_ELEMENT
        size = self.calc_submesh_name_table_element_size(name)
        self.write_chunk_header(MeshChunkID.SUBMESH_NAME_TABLE_ELEMENT, size)
        self.write_ushort(index)  # submesh index
        self.write_string_nlt(name)  # submesh name
        self.pop_chunk(MeshChunkID.SUBMESH_NAME_TABLE_ELEMENT)

    def write_edge_list(self, mesh):
        if not mesh.edge_lists:
            return
        # EDGE_LISTS
        size = self.calc_edge_list_size(mesh)
        self.write_chunk_header(MeshChunkID.EDGE_LISTS, size)
        for lod_index, edge_data in mesh.edge_lists.items():
            self.write_edge_list_lod(lod_index, edge_data)
        self.pop_chunk(MeshChunkID.EDGE_LISTS)

    def write_edge_list_lod(self, lod_index, edge_data):
        # EDGE_LIST_LOD
        size = self.calc_edge_list_lod_size(edge_data)
        self.write_chunk_header(MeshChunkID.EDGE_LIST_LOD, size)
        self.write_ushort(lod_index)
        self.write_bool(edge_data.is_manual)  # is_manual
        if not edge_data.is_manual:
            self.write_edge_list_lod_info(edge_data)
        self.pop_chunk(MeshChunkID.EDGE_LIST_LOD)

    def write_edge_list_lod_info(self, edge_data):
        self.write_bool(edge_data.is_closed)
        self.write_uint(len(edge_data.triangles))
        self.write_uint(len(edge_data.edge_groups))
        for et in edge_data.triangles:
            self.write_uint(et.index_set)
            self.write_uint(et.vertex_set)
            self.write_uint(et.vert_indices[0])
            self.write_uint(et.vert_indices[1])
            self.write_uint(et.vert_indices[2])
            self.write_uint(et.shared_vert_indices[0])
            self.write_uint(et.shared_vert_indices[1])
            self.write_uint(et.shared_vert_indices[2])
            self.write_float(et.normal[0])
            self.write_float(et.normal[1])
            self.write_float(et.normal[2])
            self.write_float(et.normal[3])
        for eg in edge_data.edge_groups:
            self.write_edge_group(eg)

    def write_edge_group(self, eg):
        # EDGE_GROUP
        size = self.calc_edge_group_size(eg)
        self.write_chunk_header(MeshChunkID.EDGE_GROUP, size)
        self.write_uint(eg.vertex_set)
        self.write_uint(eg.tri_start)
        self.write_uint(eg.tri_count)
        self.write_uint(len(eg.edge_list))
        for e in eg.edge_list:
            self.write_uint(e.tri_index0)
            self.write_uint(e.tri_index1)
            self.write_uint(e.vert_index0)
            self.write_uint(e.vert_index1)
            self.write_uint(e.shared_vert_index0)
            self.write_uint(e.shared_vert_index1)
            self.write_bool(e.degenerate)
        self.pop_chunk(MeshChunkID.EDGE_GROUP)

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Calc Methods

    def calc_mesh_size(self, mesh):
        # MESH
        size = CHUNK_HEADER_SIZE + BOOL_SIZE  # skeletally_animated
        if mesh.shared_vertex_data != None:
            size += self.calc_geometry_size(mesh.shared_vertex_data)  # GEOMETRY
        for submesh in mesh.submesh_list:
            size += self.calc_submesh_size(submesh)  # SUBMESH
        if mesh.skeleton_name != None:
            size += self.calc_skeleton_link_size(
                mesh.skeleton_name
            )  # MESH_SKELETON_LINK
            size += (
                mesh.get_bone_assignment_count() * BONE_ASSIGNMENT_SIZE
            )  # MESH_BONE_ASSIGNMENT

        size += BOUNDS_INFO_SIZE  # MESH_BOUNDS
        if self.writes_submesh_name_table(mesh):
            size += self.calc_submesh_name_table_size(mesh)  # SUBMESH_NAME_TABLE
        size += self.calc_edge_list_size(mesh)  # EDGE_LISTS
        size += sum(len(data) for _, data in mesh.raw_chunks)  # kept chunks

        return size

    def calc_geometry_size(self, vertex_data):
        # GEOMETRY
        size = CHUNK_HEADER_SIZE + UINT_SIZE  # vertex count
        size += self.calc_vertex_declaration_size(
            vertex_data.vertex_declaration
        )  # GEOMETRY_VERTEX_DECLARATION
        for vertex_buffer in vertex_data.vertex_buffer_map.values():
            size += self.calc_vertex_buffer_size(
                vertex_buffer
            )  # GEOMETRY_VERTEX_BUFFER
        return size

    def calc_vertex_declaration_size(self, vertex_declaration):
        # GEOMETRY_VERTEX_DECLARATION
        return (
            CHUNK_HEADER_SIZE
            + len(vertex_declaration.vertex_element_list)
            * VERTEX_ELEMENT_SIZE  # list of GEOMETRY_VERTEX_ELEMENT
        )

    def calc_vertex_buffer_size(self, vertex_buffer):
        # GEOMETRY_VERTEX_BUFFER
        return (
            CHUNK_HEADER_SIZE
            + 2 * USHORT_SIZE  # bind index, vertex size
            + self.calc_vertex_buffer_data_size(
                vertex_buffer
            )  # GEOMETRY_VERTEX_BUFFER_DATA
        )

    def calc_vertex_buffer_data_size(self, vertex_buffer):
        # GEOMETRY_VERTEX_BUFFER_DATA
        return CHUNK_HEADER_SIZE + vertex_buffer.buffer.nbytes  # buffer data

    def calc_submesh_size(self, submesh):
        # SUBMESH
        size = (
            CHUNK_HEADER_SIZE
            + self.calc_string_size(submesh.material_name)  # material name
            + BOOL_SIZE  # use_shared_vertices
            + UINT_SIZE  # index_count
            + BOOL_SIZE  # indices_32_bit
            + submesh.get_index_buffer().nbytes
        )
        if not submesh.use_shared_vertices:
            size += self.calc_geometry_size(submesh.vertex_data)  # GEOMETRY

        size += self.calc_submesh_texture_aliases_size(
            submesh
        )  # handles all SUBMESH_TEXTURE_ALIAS
        size += SUBMESH_OPERATION_SIZE  # SUBMESH_OPERATION
        size += (
            submesh.get_bone_assignment_count() * BONE_ASSIGNMENT_SIZE
        )  # SUBMESH_BONE_ASSIGNMENT

        return size

    def calc_submesh_texture_aliases_size(self, submesh):
        size = 0
        for alias_name, texture_name in submesh.texture_alias_map.items():
            # SUBMESH_TEXTURE_ALIAS
            size += (
                CHUNK_HEADER_SIZE
                + self.calc_string_size(alias_name)  # alias name
                + self.calc_string_size(texture_name)  # texture name
            )
        return size

    def calc_skeleton_link_size(self, skeleton_name):
        # MESH_SKELETON_LINK
        return CHUNK_HEADER_SIZE + self.calc_string_size(skeleton_name)  # skeleton name

    def calc_lod_levels_size(self, mesh):
        return 0

    def calc_submesh_name_table_size(self, mesh):
        # SUBMESH_NAME_TABLE
        size = CHUNK_HEADER_SIZE
        for name in mesh.name_table.values():
            if name != None:
                size += self.calc_submesh_name_table_element_size(
                    name
                )  # SUBMESH_NAME_TABLE_ELEMENT
        return size

    def calc_submesh_name_table_element_size(self, name):
        # SUBMESH_NAME_TABLE_ELEMENT
        return (
            CHUNK_HEADER_SIZE
            + USHORT_SIZE  # submesh index
            + self.calc_string_size(name)  # submesh name
        )

    def calc_edge_list_size(self, mesh):
        if not mesh.edge_lists:
            return 0
        size = CHUNK_HEADER_SIZE
        for edge_data in mesh.edge_lists.values():
            size += self.calc_edge_list_lod_size(edge_data)
        return size

    def calc_edge_list_lod_size(self, edge_data):
        size = CHUNK_HEADER_SIZE + USHORT_SIZE + BOOL_SIZE  # lod_index, is_manual
        if edge_data.is_manual:
            return size
        # edge_list_lod_info
        size += BOOL_SIZE + 2 * UINT_SIZE  # is_closed, tri_count, eg_count
        size += len(edge_data.triangles) * (
            8 * UINT_SIZE + 4 * FLOAT_SIZE
        )  # EdgeTriangle
        for eg in edge_data.edge_groups:
            size += self.calc_edge_group_size(eg)
        return size

    def calc_edge_group_size(self, eg):
        size = CHUNK_HEADER_SIZE + 4 * UINT_SIZE  # vertex_set, tri_start, tri_count, edge_count
        size += len(eg.edge_list) * (6 * UINT_SIZE + BOOL_SIZE)  # Edge
        return size

    def calc_poses_size(self, mesh):
        return 0

    def calc_animations_size(self, mesh):
        return 0

    def calc_extremes_size(self, mesh):
        return 0
