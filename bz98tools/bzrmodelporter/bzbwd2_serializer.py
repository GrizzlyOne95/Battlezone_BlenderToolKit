from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

# Chunk names:
#   BWD2 - WD2 might stand for World2
#   REV\0- Revision chunk, holds revision number
#   VDFC - Signifies vehicle data
#   VGEO - Holds vehicle geometry structure information
#   ANIM - Holds animations
#   VCHK - Unknown use (not in any stock VDF)
#   COLP - Collision planes used for physics collisions (not ordnance collisions though)
#   SPCS - Unknown use

# For some reason the redux does not care about the revision number.
# 1.5 will display "Bad BWD revision for file %s"

import itertools
from .bzbwd2 import (
    VDF,
    SDF,
    CollisionPlanes,
    AnimObj,
    VGEO,
    SGEO,
)
from .bz_baseserializer import (
    BZBaseSerializer,
    VECTOR3_SIZE,
    QUATERNION_SIZE,
    TRANSFORM_SIZE,
)

from .baseserializer import (
    UINT_SIZE,
    FLOAT_SIZE,
    AbruptEOFError,
)

CHUNK_HEADER_SIZE = 4 + UINT_SIZE
SDFC_DEATH_FIELDS_SIZE = 26  # death animation + death audio names

START_OF_STREAM = 0
CURRENT_STREAM_POSITION = 1


def decode_fixed_string(raw):
    return raw.partition(b"\0")[0].decode("latin-1")


def split_death_fields(raw):
    """Split the 26-byte SDFC death-name region into (animation, audio,
    animation width). Stock files use 13/13. When that split leaves the
    audio name empty but text follows the animation name (CR's files use
    14/12), the audio name starts at that text."""
    width = 13
    animation = decode_fixed_string(raw[:width])
    audio = decode_fixed_string(raw[width:])
    if not audio:
        end = raw.find(b"\0")
        if end >= 0:
            start = end
            while start < len(raw) and raw[start] == 0:
                start += 1
            if start < len(raw) and start > width:
                width = start
                animation = decode_fixed_string(raw[:width])
                audio = decode_fixed_string(raw[width:])
    return animation, audio, width


class BWD2BaseSerializer(BZBaseSerializer):
    def __init__(self, stream, endian="little"):
        super().__init__(stream, endian)
        self.current_chunk_addr = 0
        self.current_chunk_size = 0

    def read_chunk_header(self):
        self.current_chunk_addr = self.stream.tell()
        try:
            chunk_name = self.read_string(4)
        except AbruptEOFError:
            raise
        except EOFError:
            return None
        self.current_chunk_size = self.read_sint()
        if self.current_chunk_size < 8:
            # The size includes the 8-byte header, and the engine walks chunks
            # by size, so anything smaller can never advance.
            raise Exception(
                f'Chunk "{chunk_name}" at address {self.current_chunk_addr} has invalid size {self.current_chunk_size}!'
            )

        return chunk_name

    def check_read_complete(self, chunk_name):
        if chunk_name is not None:
            raise Exception(
                f'Unexpected chunk "{chunk_name}" at address {self.current_chunk_addr}; the file has an unsupported chunk order!'
            )

    def seek_next_chunk(self):
        self.stream.seek(
            self.current_chunk_addr + self.current_chunk_size, START_OF_STREAM
        )

    def read_exit_chunks(self, chunk_name):
        """Skip consecutive EXIT chunks; return (count, next chunk name)."""
        count = 0
        while chunk_name == "EXIT":
            count += 1
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()
        return count, chunk_name

    def read_fixed_string(self, owner, key, length):
        """Read a fixed-width NUL-padded string and keep its raw bytes in
        owner.raw_strings[key], so bytes after the terminator round-trip."""
        raw = self.read_raw(length)
        owner.raw_strings[key] = raw
        return decode_fixed_string(raw)

    def write_fixed_string(self, owner, key, value, length):
        """Write `value` in `length` bytes. If the reader kept raw bytes that
        still decode to `value`, write those so the field is byte-identical."""
        raw = getattr(owner, "raw_strings", {}).get(key)
        if raw is not None and len(raw) == length and decode_fixed_string(raw) == value:
            self.write_raw(raw)
            return
        self.write_padded_string(value, length, key)

    def write_padded_string(self, value, length, label="string"):
        encoded = value.encode("latin-1")
        if len(encoded) > length:
            raise ValueError(f"{label} {value!r} does not fit in {length} bytes")
        self.write_raw(encoded.ljust(length, b"\0"))

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Read Methods

    def read_anim(self, anim_obj):
        anim_obj.name = self.read_fixed_string(anim_obj, "name", 16)
        animation_count = self.read_uint()
        mesh_count = self.read_uint()
        orientation_keyframe_count = self.read_uint()
        scale_keyframe_count = self.read_uint()
        position_keyframe_count = self.read_uint()
        anim_obj._anim_ptr = self.read_uint()
        anim_obj._mesh_ptr = self.read_uint()
        anim_obj._orientation_keyframe_ptr = self.read_uint()
        anim_obj._scale_keyframe_ptr = self.read_uint()
        anim_obj._position_keyframe_ptr = self.read_uint()
        anim_obj._obj = self.read_uint()
        anim_obj._entity = self.read_uint()

        # List of animations
        for i in range(animation_count):
            index = self.read_uint()
            animation = anim_obj.create_animation(index)
            animation.mesh_index_list[:] = self.read_uint_array(32)
            animation.start = self.read_uint()
            animation.length = self.read_sint()
            animation.loop = self.read_uint()
            animation.speed = self.read_float()

        # List of animation meshes
        for i in range(mesh_count):
            raw_name = self.read_raw(8)
            anim_mesh = anim_obj.create_animation_mesh(decode_fixed_string(raw_name))
            anim_mesh.raw_strings["name"] = raw_name
            anim_mesh.flags = self.read_uint()
            anim_mesh.inverse_transform = self.read_transform()
            anim_mesh.frame_transform = self.read_transform()
            anim_mesh.orientation_start = self.read_uint()
            anim_mesh.orientation_length = self.read_uint()
            anim_mesh.scale_start = self.read_uint()
            anim_mesh.scale_length = self.read_uint()
            anim_mesh.position_start = self.read_uint()
            anim_mesh.position_length = self.read_uint()

        # Lists of keyframes
        for i in range(orientation_keyframe_count):
            frame = self.read_uint()
            orientation = self.read_quaternion()
            anim_obj.create_orientation_keyframe(frame, orientation)

        for i in range(scale_keyframe_count):
            frame = self.read_uint()
            scale = self.read_vector3()
            anim_obj.create_scale_keyframe(frame, scale)

        for i in range(position_keyframe_count):
            frame = self.read_uint()
            position = self.read_vector3()
            anim_obj.create_position_keyframe(frame, position)

        # Chunk validation
        end_addr = self.stream.tell()
        addr_delta = end_addr - self.current_chunk_addr
        if addr_delta != self.current_chunk_size:
            print(
                f"ANIM chunk address delta ({addr_delta}) is different from given size ({self.current_chunk_size})! Ended read at address 0x{end_addr:X}"
            )

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Write Methods

    def write_chunk_header(self, chunk_name, chunk_size):
        self.write_string(chunk_name, 4)
        self.write_sint(chunk_size)

    def write_exit(self):
        self.write_chunk_header("EXIT", CHUNK_HEADER_SIZE)

    def write_anim(self, anim_obj):
        self.write_chunk_header("ANIM", self.calc_anim_size(anim_obj))
        self.write_fixed_string(anim_obj, "name", anim_obj.name, 16)
        self.write_uint(len(anim_obj.animation_map))
        self.write_uint(len(anim_obj.mesh_map))
        self.write_uint(len(anim_obj.orientation_keyframe_list))
        self.write_uint(len(anim_obj.scale_keyframe_list))
        self.write_uint(len(anim_obj.position_keyframe_list))
        self.write_uint(anim_obj._anim_ptr)
        self.write_uint(anim_obj._mesh_ptr)
        self.write_uint(anim_obj._orientation_keyframe_ptr)
        self.write_uint(anim_obj._scale_keyframe_ptr)
        self.write_uint(anim_obj._position_keyframe_ptr)
        self.write_uint(anim_obj._obj)
        self.write_uint(anim_obj._entity)

        self.write_animation_map(anim_obj.animation_map)
        self.write_mesh_map(anim_obj.mesh_map)
        self.write_animation_keyframe_lists(anim_obj)

    def write_animation_map(self, animation_map):
        for index, animation in animation_map.items():
            self.write_uint(index)
            self.write_uint_array(animation.mesh_index_list, 32)
            self.write_uint(animation.start)
            self.write_sint(animation.length)
            self.write_uint(animation.loop)
            self.write_float(animation.speed)

    def write_mesh_map(self, mesh_map):
        for name, animation_mesh in mesh_map.items():
            self.write_fixed_string(animation_mesh, "name", name, 8)
            self.write_uint(animation_mesh.flags)
            self.write_transform(animation_mesh.inverse_transform)
            self.write_transform(animation_mesh.frame_transform)
            self.write_uint(animation_mesh.orientation_start)
            self.write_uint(animation_mesh.orientation_length)
            self.write_uint(animation_mesh.scale_start)
            self.write_uint(animation_mesh.scale_length)
            self.write_uint(animation_mesh.position_start)
            self.write_uint(animation_mesh.position_length)

    def write_animation_keyframe_lists(self, anim_obj):
        for keyframe in anim_obj.orientation_keyframe_list:
            self.write_uint(keyframe.frame)
            self.write_quaternion(keyframe.orientation)

        for keyframe in anim_obj.scale_keyframe_list:
            self.write_uint(keyframe.frame)
            self.write_vector3(keyframe.scale)

        for keyframe in anim_obj.position_keyframe_list:
            self.write_uint(keyframe.frame)
            self.write_vector3(keyframe.position)

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Calc Methods

    def calc_animation_map_size(self, animation_map):
        return (
            UINT_SIZE  # animation index
            + 32 * UINT_SIZE  # mesh index list
            + UINT_SIZE  # start
            + UINT_SIZE  # length
            + UINT_SIZE  # loop
            + FLOAT_SIZE  # speed
        ) * len(animation_map)

    def calc_mesh_map_size(self, mesh_map):
        return (
            8  # name
            + UINT_SIZE  # flags
            + TRANSFORM_SIZE  # inverse transform
            + TRANSFORM_SIZE  # frame transform
            + UINT_SIZE  # orientation start
            + UINT_SIZE  # orientation length
            + UINT_SIZE  # scale start
            + UINT_SIZE  # scale length
            + UINT_SIZE  # position start
            + UINT_SIZE  # position length
        ) * len(mesh_map)

    def calc_animation_keyframe_lists_size(self, anim_obj):
        return (
            (UINT_SIZE + QUATERNION_SIZE)  # frame  # orientation
            * len(anim_obj.orientation_keyframe_list)
            + (UINT_SIZE + VECTOR3_SIZE)  # frame  # scale
            * len(anim_obj.scale_keyframe_list)
            + (UINT_SIZE + VECTOR3_SIZE)  # frame  # position
            * len(anim_obj.position_keyframe_list)
        )

    def calc_anim_size(self, anim_obj):
        return (
            CHUNK_HEADER_SIZE
            + 16  # name
            + UINT_SIZE  # animation count
            + UINT_SIZE  # animation mesh count
            + UINT_SIZE  # orientation keyframe count
            + UINT_SIZE  # scale keyframe count
            + UINT_SIZE  # position keyframe count
            + 7
            * UINT_SIZE  # animPtr, meshPtr, rotKeyPtr, sclKeyPtr, posKeyPtr, obj, entity
            + self.calc_animation_map_size(anim_obj.animation_map)
            + self.calc_mesh_map_size(anim_obj.mesh_map)
            + self.calc_animation_keyframe_lists_size(anim_obj)
        )


class VDFSerializer(BWD2BaseSerializer):
    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Read Methods

    def read(self, vdf=None):
        if vdf is None:
            vdf = VDF()

        # Read header chunks
        chunk_name = self.read_chunk_header()
        if chunk_name != "BWD2":
            raise Exception(
                f'Expected chunk BWD2 at address {self.current_chunk_addr}; got "{chunk_name}"!'
            )
        self.seek_next_chunk()
        chunk_name = self.read_chunk_header()  # REV\0  - no need to check chunk name
        vdf._revision = self.read_uint()
        self.seek_next_chunk()

        # Read the rest of the VDF
        chunk_name = self.read_chunk_header()
        if chunk_name == "VDFC":
            self.read_vdfc(vdf)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        for _ in range(3):
            if chunk_name == "EXIT":
                self.seek_next_chunk()
                chunk_name = self.read_chunk_header()
            else:
                break
        if chunk_name == "VGEO":
            self.read_vgeo(vdf)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        if chunk_name == "ANIM":
            anim_obj = AnimObj()
            vdf.anim_obj = anim_obj
            self.read_anim(anim_obj)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        if chunk_name == "VCHK":
            self.read_vchk(vdf)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        for _ in range(2):
            if chunk_name == "EXIT":
                self.seek_next_chunk()
                chunk_name = self.read_chunk_header()
            else:
                break

        if chunk_name == "COLP":
            colp = CollisionPlanes()
            vdf.collision_planes = colp
            self.read_colp(colp)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        if chunk_name == "EXIT":  # Unknown how many EXIT chunks are acceptable here
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        if chunk_name == "SPCS":
            self.read_spcs(vdf)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        for _ in range(2):  # Unknown how many EXIT chunks are acceptable here
            if chunk_name == "EXIT":
                self.seek_next_chunk()
                chunk_name = self.read_chunk_header()
            else:
                break

        self.check_read_complete(chunk_name)
        return vdf

    def read_vdfc(self, vdf):
        vdf.name = self.read_fixed_string(vdf, "name", 16)
        vdf.entity_class = self.read_uint()
        vdf.vehicle_size = self.read_uint()
        vdf.lod_dist_list[:] = self.read_float_array(5)
        vdf.mass = self.read_float()
        vdf.cdm = self.read_float()
        vdf.drag_coeff = self.read_float()
        vdf.hardpoint_count = self.read_uint()  # unused

    def read_vgeo(self, vdf):
        geo_count = self.read_uint()

        for lod, rep, index in itertools.product(
            range(VGEO.LOD.COUNT), range(VGEO.REP.COUNT), range(geo_count)
        ):
            raw_name = self.read_raw(8)
            transform = self.read_transform()
            raw_parent = self.read_raw(8)

            geo_object = vdf.create_geo_object(
                lod,
                rep,
                index,
                decode_fixed_string(raw_name),
                decode_fixed_string(raw_parent),
            )
            geo_object.raw_strings["name"] = raw_name
            geo_object.raw_strings["parent"] = raw_parent
            geo_object.transform = transform

            geo_object.center_pos = self.read_vector3()
            geo_object.radius = self.read_float()
            geo_object.half_size = self.read_vector3()
            geo_object.class_id = self.read_uint()
            geo_object.object_flags = self.read_uint()

            # # # # # # # # # # # # # # # #
            geo_object.ddr = self.read_uint()  # ddr, unknown type (what is this?)
            geo_object.target = (
                self.read_vector3()
            )  # When the object is a spinner, this is the angular velocity divided by 2*pi
            geo_object.time = (
                self.read_float()
            )  # Purpose unknown. Maybe an I76 holdover?
            self.stream.seek(
                -20, CURRENT_STREAM_POSITION
            )  # UINT_SIZE + VECTOR3_SIZE + FLOAT_SIZE = 4 + 12 + 4 = 20

    def read_vchk(self, vdf):
        pass  # Unknown

    def read_colp(self, colp):
        colp.front = self.read_float()
        colp.front_middle = self.read_float()
        colp.back_middle = self.read_float()
        colp.back = self.read_float()
        colp.right = self.read_float()
        colp.right_middle = self.read_float()
        colp.left_middle = self.read_float()
        colp.left = self.read_float()
        colp.top = self.read_float()
        colp.top_middle = self.read_float()
        colp.bottom_middle = self.read_float()
        colp.bottom = self.read_float()

    def read_spcs(self, vdf):
        vdf.spcs_data = self.read_uint_array(3)

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Write Methods

    def write(self, vdf):
        # Write header
        self.write_chunk_header("BWD2", CHUNK_HEADER_SIZE)
        self.write_chunk_header("REV", CHUNK_HEADER_SIZE + UINT_SIZE)  # revision
        self.write_uint(vdf._revision)

        # Write rest of VDF
        self.write_vdfc(vdf)
        self.write_exit()
        self.write_vgeo(vdf)
        if vdf.anim_obj is not None:
            self.write_anim(vdf.anim_obj)
        self.write_exit()
        self.write_exit()
        if vdf.collision_planes is not None:
            self.write_colp(vdf.collision_planes)
        self.write_exit()
        if vdf.spcs_data is not None:
            self.write_spcs(vdf)
        self.write_exit()

    def write_vdfc(self, vdf):
        self.write_chunk_header("VDFC", self.calc_vdfc_size())
        self.write_fixed_string(vdf, "name", vdf.name, 16)
        self.write_uint(vdf.entity_class)  # unused?
        self.write_uint(vdf.vehicle_size)  # unused?
        self.write_float_array(vdf.lod_dist_list, 5)  # unused?
        self.write_float(vdf.mass)
        self.write_float(vdf.cdm)
        self.write_float(vdf.drag_coeff)
        self.write_uint(vdf.hardpoint_count)  # unused

    def write_vgeo(self, vdf):
        self.write_chunk_header("VGEO", self.calc_vgeo_size(vdf))
        self.write_uint(vdf.objcount)
        prev_geo_object = None
        for geo_object in vdf.all_geo_objects(raw=True):
            start_addr = self.stream.tell()
            self.write_fixed_string(geo_object, "name", geo_object.name, 8)
            self.write_transform(geo_object.transform)
            self.write_fixed_string(
                geo_object, "parent", geo_object.parent_name(), 8
            )
            self.write_vector3(geo_object.center_pos)
            self.write_float(geo_object.radius)
            self.write_vector3(geo_object.half_size)
            self.write_uint(geo_object.class_id)
            self.write_uint(geo_object.object_flags)

            if prev_geo_object is not None and (
                prev_geo_object.write_ddr
                or prev_geo_object.write_target_r
                or prev_geo_object.write_target_u
                or prev_geo_object.write_target_f
                or prev_geo_object.write_time
            ):
                end_addr = self.stream.tell()
                self.stream.seek(start_addr, START_OF_STREAM)
                if prev_geo_object.write_ddr:
                    self.write_uint(prev_geo_object.ddr)
                else:
                    self.stream.seek(UINT_SIZE, CURRENT_STREAM_POSITION)

                if prev_geo_object.write_target_r:
                    self.write_float(prev_geo_object.target.r)
                else:
                    self.stream.seek(FLOAT_SIZE, CURRENT_STREAM_POSITION)

                if prev_geo_object.write_target_u:
                    self.write_float(prev_geo_object.target.u)
                else:
                    self.stream.seek(FLOAT_SIZE, CURRENT_STREAM_POSITION)

                if prev_geo_object.write_target_f:
                    self.write_float(prev_geo_object.target.f)
                else:
                    self.stream.seek(FLOAT_SIZE, CURRENT_STREAM_POSITION)

                if prev_geo_object.write_time:
                    self.write_float(prev_geo_object.time)

                self.stream.seek(end_addr, START_OF_STREAM)
            prev_geo_object = geo_object

        count = 0
        if prev_geo_object is not None:
            if prev_geo_object.write_time:
                count = 5
            elif prev_geo_object.write_target_f:
                count = 4
            elif prev_geo_object.write_target_u:
                count = 3
            elif prev_geo_object.write_target_r:
                count = 2
            elif prev_geo_object.write_ddr:
                count = 1

        if count > 0:
            if count >= 1:
                if prev_geo_object.write_time:
                    self.write_uint(prev_geo_object.ddr)
                else:
                    self.write_uint(0)

            if count >= 2:
                if prev_geo_object.write_target_r:
                    self.write_uint(prev_geo_object.target.r)
                else:
                    self.write_float(0)

            if count >= 3:
                if prev_geo_object.write_target_u:
                    self.write_uint(prev_geo_object.target.u)
                else:
                    self.write_float(0)

            if count >= 4:
                if prev_geo_object.write_target_f:
                    self.write_uint(prev_geo_object.target.f)
                else:
                    self.write_float(0)

            if count >= 5:
                if prev_geo_object.write_time:
                    self.write_float(prev_geo_object.time)
                else:
                    self.write_float(0)

    def write_colp(self, colp):
        self.write_chunk_header("COLP", self.calc_colp_size())
        self.write_float(colp.front)
        self.write_float(colp.front_middle)
        self.write_float(colp.back_middle)
        self.write_float(colp.back)
        self.write_float(colp.right)
        self.write_float(colp.right_middle)
        self.write_float(colp.left_middle)
        self.write_float(colp.left)
        self.write_float(colp.top)
        self.write_float(colp.top_middle)
        self.write_float(colp.bottom_middle)
        self.write_float(colp.bottom)

    def write_spcs(self, vdf):
        self.write_chunk_header("SPCS", self.calc_spcs_size())
        self.write_uint_array(vdf.spcs_data, 3)

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Calc Methods

    def calc_vdfc_size(self):
        return (
            CHUNK_HEADER_SIZE
            + 16  # name
            + UINT_SIZE  # entity_class
            + UINT_SIZE  # vehicle_size
            + 5 * FLOAT_SIZE  # lod_dist_list
            + FLOAT_SIZE  # mass
            + FLOAT_SIZE  # cdm
            + FLOAT_SIZE  # drag_coeff
            + UINT_SIZE  # hardpoint_count
        )

    def calc_vgeo_size(self, vdf):
        extra = 0
        if vdf.objcount > 0:
            geo_object = vdf.get_end_object()
            if geo_object.write_time:
                extra = 5 * 4
            elif geo_object.write_target_f:
                extra = 4 * 4
            elif geo_object.write_target_u:
                extra = 3 * 4
            elif geo_object.write_target_r:
                extra = 2 * 4
            elif geo_object.write_ddr:
                extra = 1 * 4

        return (
            CHUNK_HEADER_SIZE
            + UINT_SIZE  # geo element count
            + (
                8  # name
                + TRANSFORM_SIZE  # transform
                + 8  # parent
                + VECTOR3_SIZE  # center_pos
                + FLOAT_SIZE  # radius
                + VECTOR3_SIZE  # half_size
                + UINT_SIZE  # class_id
                + UINT_SIZE  # object_flags
            )
            * VGEO.LOD.COUNT
            * VGEO.REP.COUNT
            * vdf.objcount
            + extra
        )

    def calc_colp_size(self):
        return (
            CHUNK_HEADER_SIZE
            + FLOAT_SIZE  # front
            + FLOAT_SIZE  # front mid
            + FLOAT_SIZE  # back mid
            + FLOAT_SIZE  # back
            + FLOAT_SIZE  # right
            + FLOAT_SIZE  # right mid
            + FLOAT_SIZE  # left mid
            + FLOAT_SIZE  # left
            + FLOAT_SIZE  # top
            + FLOAT_SIZE  # top mid
            + FLOAT_SIZE  # bottom mid
            + FLOAT_SIZE  # bottom
        )

    def calc_spcs_size(self):
        return CHUNK_HEADER_SIZE + 12  # Unknown 12 bytes / 3 words.


class SDFSerializer(BWD2BaseSerializer):
    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Read Methods

    def read(self, sdf=None):
        if sdf is None:
            sdf = SDF()

        # Read header chunks
        chunk_name = self.read_chunk_header()
        if chunk_name != "BWD2":
            raise Exception(
                f'Expected chunk BWD2 at address {self.current_chunk_addr}; got "{chunk_name}"!'
            )
        self.seek_next_chunk()
        chunk_name = self.read_chunk_header()  # REV\0  - no need to check chunk name
        sdf._revision = self.read_uint()
        self.seek_next_chunk()

        # Read the rest of the SDF
        chunk_name = self.read_chunk_header()
        if chunk_name == "SDFC":
            self.read_sdfc(sdf)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        sdf.exit_count_after_sdfc, chunk_name = self.read_exit_chunks(chunk_name)

        if chunk_name == "SGEO":
            self.read_sgeo(sdf)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        if chunk_name == "ANIM":
            anim_obj = AnimObj()
            sdf.anim_obj = anim_obj
            self.read_anim(anim_obj)
            self.seek_next_chunk()
            chunk_name = self.read_chunk_header()

        sdf.exit_count, chunk_name = self.read_exit_chunks(chunk_name)

        self.check_read_complete(chunk_name)
        return sdf

    def read_sdfc(self, sdf):
        sdf.name = self.read_fixed_string(sdf, "name", 16)
        sdf.struct_class = self.read_uint()
        sdf.lod_dist_list[:] = self.read_float_array(5)
        sdf.ddr = self.read_uint()  # Type?
        raw = self.read_raw(SDFC_DEATH_FIELDS_SIZE)
        sdf.raw_strings["death_fields"] = raw
        (
            sdf.death_animation,
            sdf.death_audio,
            sdf.death_animation_width,
        ) = split_death_fields(raw)

    def read_sgeo(self, sdf):
        geo_count = self.read_uint()

        for lod, rep, index in itertools.product(
            range(SGEO.LOD.COUNT), range(SGEO.REP.COUNT), range(geo_count)
        ):
            raw_name = self.read_raw(8)
            transform = self.read_transform()
            raw_parent = self.read_raw(8)

            geo_object = sdf.create_geo_object(
                lod,
                rep,
                index,
                decode_fixed_string(raw_name),
                decode_fixed_string(raw_parent),
            )
            geo_object.raw_strings["name"] = raw_name
            geo_object.raw_strings["parent"] = raw_parent
            geo_object.transform = transform

            geo_object.center_pos = self.read_vector3()
            geo_object.radius = self.read_float()
            geo_object.half_size = self.read_vector3()
            geo_object.class_id = self.read_uint()
            geo_object.object_flags = self.read_uint()
            geo_object.ddr = self.read_uint()  # ddr, unknown type (what is this?)
            geo_object.target = (
                self.read_vector3()
            )  # When the object is a spinner, this is the angular velocity divided by 2*pi
            geo_object.time = (
                self.read_float()
            )  # Purpose unknown. Maybe an I76 holdover?

        # Keep any bytes the chunk holds past the declared records.
        extra = self.current_chunk_addr + self.current_chunk_size - self.stream.tell()
        sdf.sgeo_trailing_bytes = self.read_raw(extra) if extra > 0 else b""

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Write Methods

    def write(self, sdf):
        # Write header
        self.write_chunk_header("BWD2", CHUNK_HEADER_SIZE)
        self.write_chunk_header("REV", CHUNK_HEADER_SIZE + UINT_SIZE)  # revision
        self.write_uint(sdf._revision)

        # Write rest of SDF
        self.write_sdfc(sdf)
        for _ in range(sdf.exit_count_after_sdfc):
            self.write_exit()
        self.write_sgeo(sdf)
        if sdf.anim_obj is not None:
            self.write_anim(sdf.anim_obj)
        for _ in range(sdf.exit_count):
            self.write_exit()

    def write_sdfc(self, sdf):
        if sdf.name is None:
            raise ValueError("SDF.name must be set before writing")
        if len(sdf.lod_dist_list) != 5:
            raise ValueError("SDF.lod_dist_list must hold 5 distances")
        self.write_chunk_header("SDFC", self.calc_sdfc_size())
        self.write_fixed_string(sdf, "name", sdf.name, 16)
        self.write_uint(sdf.struct_class)  # unused?
        self.write_float_array(sdf.lod_dist_list, 5)  # unused?
        self.write_uint(int(sdf.ddr))
        self.write_death_fields(sdf)

    def write_death_fields(self, sdf):
        width = sdf.death_animation_width
        if not 0 < width < SDFC_DEATH_FIELDS_SIZE:
            raise ValueError(f"death_animation_width {width} is outside 1..25")
        raw = sdf.raw_strings.get("death_fields")
        if raw is not None and len(raw) == SDFC_DEATH_FIELDS_SIZE:
            animation = decode_fixed_string(raw[:width])
            audio = decode_fixed_string(raw[width:])
            if (animation, audio) == (sdf.death_animation, sdf.death_audio):
                self.write_raw(raw)
                return
        self.write_padded_string(sdf.death_animation, width, "death_animation")
        self.write_padded_string(
            sdf.death_audio, SDFC_DEATH_FIELDS_SIZE - width, "death_audio"
        )

    def write_sgeo(self, sdf):
        self.write_chunk_header("SGEO", self.calc_sgeo_size(sdf))
        self.write_uint(sdf.objcount)
        for lod, rep, index in itertools.product(
            range(SGEO.LOD.COUNT), range(SGEO.REP.COUNT), range(sdf.objcount)
        ):
            geo_object = sdf.get_object(lod, rep, index, raw=True)
            self.write_fixed_string(geo_object, "name", geo_object.name, 8)
            self.write_transform(geo_object.transform)
            self.write_fixed_string(
                geo_object, "parent", geo_object.parent_name(), 8
            )
            self.write_vector3(geo_object.center_pos)
            self.write_float(geo_object.radius)
            self.write_vector3(geo_object.half_size)
            self.write_uint(geo_object.class_id)
            self.write_uint(geo_object.object_flags)
            self.write_uint(geo_object.ddr)
            self.write_vector3(geo_object.target)
            self.write_float(geo_object.time)
        self.write_raw(sdf.sgeo_trailing_bytes)

    # -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
    # -# Calc Methods

    def calc_sdfc_size(self):
        return (
            CHUNK_HEADER_SIZE
            + 16  # name
            + UINT_SIZE  # struct_class
            + 5 * FLOAT_SIZE  # lod_dist_list
            + UINT_SIZE  # ddr
            + SDFC_DEATH_FIELDS_SIZE  # death_animation + death_sound
        )

    def calc_sgeo_size(self, sdf):
        return (
            CHUNK_HEADER_SIZE
            + UINT_SIZE  # geo element count
            + (
                8  # name
                + TRANSFORM_SIZE  # transform
                + 8  # parent
                + VECTOR3_SIZE  # center_pos
                + FLOAT_SIZE  # radius
                + VECTOR3_SIZE  # half_size
                + UINT_SIZE  # class_id
                + UINT_SIZE  # object_flags
                + UINT_SIZE  # ddr
                + VECTOR3_SIZE  # target
                + FLOAT_SIZE  # time
            )
            * SGEO.LOD.COUNT
            * SGEO.REP.COUNT
            * sdf.objcount
            + len(sdf.sgeo_trailing_bytes)
        )
