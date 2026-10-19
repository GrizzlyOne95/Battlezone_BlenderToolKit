from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

import numpy as np
import struct

from .baseserializer import (
    USHORT_SIZE,
    UINT_SIZE,
    BaseSerializer,
)
from .spacial import Vector3, Quaternion

CHUNK_HEADER_SIZE = USHORT_SIZE + UINT_SIZE

CURRENT_STREAM_POSITION = 1


class UnsupportedVersionError(Exception):
    pass


class ChunkInfo:
    def __init__(self, addr, id, size):
        self.addr = addr
        self.id = id
        self.size = size
        self.ignore_mode = False
        self.ignore_addr = 0
        self.ignore_size = 0


class OgreBaseSerializer(BaseSerializer):
    ChunkID = None

    def __init__(
        self,
        stream,
        endian="little",
        validate_chunk_sizes=False,
        log_chunks=False,
        ogre_space=False,
    ):
        """`ogre_space=True` reads and writes vectors and quaternions exactly
        as Ogre stores them (x, y, z and x, y, z, w). The default converts
        them to BZ space by mirroring x."""
        super().__init__(stream, endian)
        self.validate_chunk_sizes = validate_chunk_sizes
        self.log_chunks = log_chunks
        self.chunk_stack = []
        self.version = None
        self.ogre_space = ogre_space
        if ogre_space:
            self.read_vector3 = self.read_vector3_ogre
            self.write_vector3 = self.write_vector3_ogre
            self.read_quaternion = self.read_quaternion_ogre
            self.write_quaternion = self.write_quaternion_ogre

    def push_chunk(self, chunk_addr, chunk_id, chunk_size):
        ci = ChunkInfo(chunk_addr, chunk_id, chunk_size)
        if self.log_chunks:
            print(
                f"{'  '*len(self.chunk_stack)}> Pushing chunk {self.ChunkID.label(ci.id, full=True)} at @0x{ci.addr:X}, length 0x{ci.size:X}"
            )
        self.chunk_stack.append(ci)

    def pop_chunk(self, expected_id):
        ci = self.chunk_stack.pop()
        if self.log_chunks:
            print(
                f"{'  '*len(self.chunk_stack)}< Popping chunk {self.ChunkID.label(ci.id, full=True)} at @0x{ci.addr:X}, length 0x{ci.size:X}"
            )
        if not self.validate_chunk_sizes:
            return
        if ci.id != expected_id:
            raise Exception(
                f"Pop expected {self.ChunkID.label(expected_id, full=True)} at address @0x{ci.addr:X} but got {self.ChunkID.label(ci.id, full=True)} instead"
            )
        if ci.ignore_mode:
            raise Exception(
                f"Attempt to pop chunk {self.ChunkID.label(expected_id, full=True)} at address @0x{ci.addr:X} while in ignore mode"
            )
        delta = self.stream.tell() - ci.addr - ci.ignore_size
        true_delta = self.stream.tell() - ci.addr
        if delta != ci.size:
            raise Exception(
                f"Chunk {self.ChunkID.label(ci.id, full=True)} at address @0x{ci.addr:X} expected length 0x{ci.size:X} did not match length 0x{delta:X} (streamed byte length 0x{true_delta:X})"
            )

    def rollback_chunk_header(self):
        self.stream.seek(-6, CURRENT_STREAM_POSITION)
        self.chunk_stack.pop()
        if self.log_chunks:
            print(f"{'  '*len(self.chunk_stack)}<*Rollback chunk")

    def start_ignore_chunk(self):
        ci = self.chunk_stack[-1]
        if ci.ignore_mode:
            raise Exception("attempt to start ignore inside ignore mode")
        ci.ignore_mode = True
        ci.ignore_addr = self.stream.tell()

    def stop_ignore_chunk(self):
        ci = self.chunk_stack[-1]
        if not ci.ignore_mode:
            raise Exception("attempt to stop ignore outside ignore mode")
        ci.ignore_mode = False
        addr = self.stream.tell()
        ci.ignore_size += addr - ci.ignore_addr

    def current_chunk_size_consumed(self):
        ci = self.chunk_stack[-1]
        if ci.ignore_mode:
            addr = ci.ignore_addr
        else:
            addr = self.stream.tell()
        return addr - ci.addr - ci.ignore_size

    def current_chunk_address(self):
        return self.chunk_stack[-1].addr

    def current_chunk_id(self):
        return self.chunk_stack[-1].id

    def current_chunk_size(self):
        return self.chunk_stack[-1].size

    def are_chunks_remaining(self):
        return len(self.chunk_stack) > 0

    def read_chunk_header(self):
        addr = self.stream.tell()
        chunk_id = self.read_ushort()
        chunk_size = self.read_uint()
        self.push_chunk(addr, chunk_id, chunk_size)
        return chunk_id

    def read_specific_chunk_header(self, expected_id):
        addr = self.stream.tell()
        chunk_id = self.read_ushort()
        if chunk_id != expected_id:
            raise Exception(
                f"Expected {self.ChunkID.label(expected_id, full=True)} chunk at address {addr}; got {self.ChunkID.label(chunk_id, full=True)}"
            )
        chunk_size = self.read_uint()
        self.push_chunk(addr, chunk_id, chunk_size)
        return chunk_id

    def write_chunk_header(self, chunk_id, chunk_size):
        self.push_chunk(self.stream.tell(), chunk_id, chunk_size)
        self.write_ushort(chunk_id)
        self.write_uint(chunk_size)

    read_string = BaseSerializer.read_string_nlt
    write_string = BaseSerializer.write_string_nlt
    calc_string_size = BaseSerializer.calc_string_nlt_size
    read_vector3 = BaseSerializer.read_vector3_luf
    write_vector3 = BaseSerializer.write_vector3_luf
    read_quaternion = BaseSerializer.read_quaternion_lufs_left
    write_quaternion = BaseSerializer.write_quaternion_lufs_left

    def read_vector3_ogre(self):
        return Vector3(*struct.unpack(self.endiansymbol + "3f", self.read_raw(12)))

    def write_vector3_ogre(self, val):
        self.stream.write(struct.pack(self.endiansymbol + "3f", val.x, val.y, val.z))

    def read_quaternion_ogre(self):
        x, y, z, w = struct.unpack(self.endiansymbol + "4f", self.read_raw(16))
        return Quaternion(w, x, y, z)

    def write_quaternion_ogre(self, val):
        self.stream.write(
            struct.pack(self.endiansymbol + "4f", val.x, val.y, val.z, val.w)
        )
