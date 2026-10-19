# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Read -> write byte fidelity of the bzrmodelporter SDF, Ogre mesh and
Ogre skeleton serializers, and the SDF animation authoring helpers.

Every fixture is built in the test from struct-packed bytes; no game
files are needed.

Run:
    python tests/run_tests.py
"""

import io
import math
import os
import struct
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools.bzrmodelporter.bzbwd2 import (  # noqa: E402
    SDF,
    AnimObj,
    axis_rotation_transform,
    orientation_key_from_transform,
    spin_orientation_key,
    transform_from_orientation_key,
)
from bz98tools.bzrmodelporter.bzbwd2_serializer import SDFSerializer  # noqa: E402
from bz98tools.bzrmodelporter.ogremesh_serializer import (  # noqa: E402
    MeshChunkID,
    MeshSerializer,
)
from bz98tools.bzrmodelporter.ogreskeleton import Skeleton  # noqa: E402
from bz98tools.bzrmodelporter.ogreskeleton_serializer import (  # noqa: E402
    SkeletonSerializer,
)
from bz98tools.bzrmodelporter.spacial import (  # noqa: E402
    Quaternion,
    Transform,
    Vector3,
)


# -- SDF fixtures ---------------------------------------------------------------


def _bwd2_chunk(name, payload=b""):
    return name.ljust(4, b"\0") + struct.pack("<i", 8 + len(payload)) + payload


def _sgeo_record(name, parent, transform=(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0), class_id=0):
    return (
        name
        + struct.pack("<12f", *transform)
        + parent
        + struct.pack("<3f", 0.0, 0.5, 0.0)  # center
        + struct.pack("<f", 1.5)  # radius
        + struct.pack("<3f", 1.0, 1.0, 1.0)  # half size
        + struct.pack("<3I", class_id, 0, 0)  # class, flags, ddr
        + struct.pack("<3f", 0.0, 0.0, 0.0)  # target
        + struct.pack("<f", 0.0)  # time
    )


LOD_DISTANCES = [100.0, 200.0, 400.0, 800.0, 1600.0]

# CR writes null records as NULL, a zero transform, NULL, then zeros (class 0).
NULL_RECORD = b"NULL\0\0\0\0" + bytes(48) + b"NULL\0\0\0\0" + bytes(56)


def cr_style_sdf():
    """An SDF laid out like CR's hand-built files: REV 8, a 14/12 death
    name split, two EXIT chunks only at the end, and null records whose
    name fields carry garbage after the terminator."""
    sdfc = (
        b"xcrtst\0\0\0\0\0\0\0\0\0\0"
        + struct.pack("<I", 0)
        + struct.pack("<5f", 100, 200, 400, 800, 1600)
        + struct.pack("<I", 0)
        + b"xbldx1.xdf".ljust(14, b"\0")
        + b"null".ljust(12, b"\0")
    )
    records = []
    for lod in range(3):
        for rep in range(2):
            if lod == 0 and rep == 0:
                records.append(
                    _sgeo_record(
                        b"xcrtst1\0",
                        b"WORLD\0zz",  # bytes after NUL must survive
                        transform=(1, 0, 0, 0, 1, 0, 0, 0, 1, 2.0, 0.0, -3.0),
                        class_id=1,
                    )
                )
            else:
                records.append(NULL_RECORD)
    sgeo = struct.pack("<I", 1) + b"".join(records)
    return (
        _bwd2_chunk(b"BWD2")
        + _bwd2_chunk(b"REV", struct.pack("<I", 8))
        + _bwd2_chunk(b"SDFC", sdfc)
        + _bwd2_chunk(b"SGEO", sgeo)
        + _bwd2_chunk(b"EXIT")
        + _bwd2_chunk(b"EXIT")
    )


def stock_style_sdf():
    """Stock layout: 13/13 death names, EXITs after SDFC and at the end."""
    sdfc = (
        b"xstock\0\0\0\0\0\0\0\0\0\0"
        + struct.pack("<I", 0)
        + struct.pack("<5f", 100, 200, 400, 800, 1600)
        + struct.pack("<I", 0)
        + b"xdeath\0".ljust(13, b"\0")
        + b"xboom.wav".ljust(13, b"\0")
    )
    records = [_sgeo_record(b"xstock1\0", b"WORLD\0\0\0", class_id=1)]
    records += [NULL_RECORD] * 5
    return (
        _bwd2_chunk(b"BWD2")
        + _bwd2_chunk(b"REV", struct.pack("<I", 8))
        + _bwd2_chunk(b"SDFC", sdfc)
        + _bwd2_chunk(b"EXIT")
        + _bwd2_chunk(b"SGEO", struct.pack("<I", 1) + b"".join(records))
        + _bwd2_chunk(b"EXIT")
        + _bwd2_chunk(b"EXIT")
        + _bwd2_chunk(b"EXIT")
    )


def _read_sdf(data):
    return SDFSerializer(io.BytesIO(data)).read()


def _write_sdf(sdf):
    out = io.BytesIO()
    SDFSerializer(out).write(sdf)
    return out.getvalue()


class SDFFidelityTests(unittest.TestCase):
    def test_cr_style_sdf_round_trips(self):
        data = cr_style_sdf()
        sdf = _read_sdf(data)
        self.assertEqual(sdf.revision, 8)
        self.assertEqual(sdf.exit_count_after_sdfc, 0)
        self.assertEqual(sdf.exit_count, 2)
        self.assertEqual(sdf.death_animation, "xbldx1.xdf")
        self.assertEqual(sdf.death_audio, "null")
        self.assertEqual(sdf.death_animation_width, 14)
        self.assertEqual(sdf.get_object(0, 0, 0, raw=True).parent_name(), "WORLD")
        self.assertEqual(_write_sdf(sdf), data)

    def test_stock_style_sdf_round_trips(self):
        data = stock_style_sdf()
        sdf = _read_sdf(data)
        self.assertEqual(sdf.exit_count_after_sdfc, 1)
        self.assertEqual(sdf.exit_count, 3)
        self.assertEqual(sdf.death_animation_width, 13)
        self.assertEqual(sdf.death_audio, "xboom.wav")
        self.assertEqual(_write_sdf(sdf), data)

    def test_trailing_sgeo_bytes_round_trip(self):
        # Some shipped SDFs declare fewer GEOs than the SGEO chunk holds.
        data = cr_style_sdf()
        extra = NULL_RECORD * 2
        start = data.index(b"SGEO")
        size = struct.unpack_from("<i", data, start + 4)[0]
        patched = (
            data[: start + 4]
            + struct.pack("<i", size + len(extra))
            + data[start + 8 : start + size]
            + extra
            + data[start + size :]
        )
        self.assertEqual(_write_sdf(_read_sdf(patched)), patched)

    def test_edited_string_falls_back_to_padded_value(self):
        sdf = _read_sdf(cr_style_sdf())
        sdf.get_object(0, 0, 0, raw=True).parent = "xother"
        sdf.death_audio = "xnew.wav"
        sdf = _read_sdf(_write_sdf(sdf))
        self.assertEqual(sdf.get_object(0, 0, 0, raw=True).parent_name(), "xother")
        self.assertEqual(sdf.death_audio, "xnew.wav")
        self.assertEqual(sdf.death_animation, "xbldx1.xdf")

    def test_overlong_string_is_rejected(self):
        sdf = _read_sdf(cr_style_sdf())
        sdf.death_audio = "x" * 13  # only 12 bytes in a 14/12 split
        with self.assertRaises(ValueError):
            _write_sdf(sdf)

    def test_new_sdf_keeps_old_writer_layout(self):
        sdf = SDF()
        sdf.name = "xnew"
        sdf.lod_dist_list[:] = LOD_DISTANCES
        sdf.append_object("xnew1")
        data = _write_sdf(sdf)
        self.assertEqual(data.count(b"EXIT"), 3)
        self.assertNotIn(b"EXIT\x08\x00\x00\x00SGEO", data)

    def test_authored_null_records_match_cr_layout(self):
        sdf = SDF()
        sdf.name = "xnull"
        sdf.lod_dist_list[:] = LOD_DISTANCES
        sdf.revision = 8
        sdf.exit_count = 2
        obj = sdf.append_object("xnull1", parent="WORLD")
        self.assertEqual(obj.parent, "WORLD")
        for lod in range(3):
            for rep in range(2):
                if (lod, rep) != (0, 0):
                    sdf.get_object(lod, rep, 0, raw=True).make_null()
        data = _write_sdf(sdf)
        self.assertEqual(data.count(NULL_RECORD), 5)
        self.assertEqual(data.count(b"EXIT"), 2)
        self.assertEqual(_write_sdf(_read_sdf(data)), data)


# -- SDF animation authoring ----------------------------------------------------


def _assert_quat_close(test, a, b, places=5):
    # q and -q are the same rotation.
    same = all(abs(x - y) < 10**-places for x, y in zip((a.w, a.x, a.y, a.z), (b.w, b.x, b.y, b.z)))
    flipped = all(abs(x + y) < 10**-places for x, y in zip((a.w, a.x, a.y, a.z), (b.w, b.x, b.y, b.z)))
    test.assertTrue(same or flipped, f"{a!r} != {b!r}")


def _assert_rotation_close(test, a, b, places=5):
    for name in ("rx", "ry", "rz", "ux", "uy", "uz", "fx", "fy", "fz"):
        test.assertAlmostEqual(getattr(a, name), getattr(b, name), places=places, msg=name)


class AnimationAuthoringTests(unittest.TestCase):
    def test_identity_rest_key(self):
        key = orientation_key_from_transform(Transform())
        _assert_quat_close(self, key, Quaternion(1.0, 0.0, 0.0, 0.0))

    def test_orientation_key_round_trips_a_rotation(self):
        rest = axis_rotation_transform((0.3, 1.0, -0.2), 0.7) @ axis_rotation_transform(
            (1.0, 0.0, 0.0), -0.4
        )
        key = orientation_key_from_transform(rest)
        _assert_rotation_close(self, transform_from_orientation_key(key), rest)

    def test_key_convention_mirrors_x_and_y(self):
        rest = axis_rotation_transform((0.0, 1.0, 0.0), 0.9)
        q = rest.compute_orientation()
        key = orientation_key_from_transform(rest)
        _assert_quat_close(self, key, Quaternion(q.w, -q.x, -q.y, q.z))

    def test_spin_key_is_rest_turned_about_local_axis(self):
        rest = axis_rotation_transform((0.0, 0.0, 1.0), 0.5)
        _assert_quat_close(
            self, spin_orientation_key(rest, (0, 1, 0), 0.0), orientation_key_from_transform(rest)
        )
        turned = transform_from_orientation_key(spin_orientation_key(rest, (0, 1, 0), math.pi / 2))
        # Spinning about local up leaves the part's up axis where it was.
        self.assertAlmostEqual(turned.ux, rest.ux, places=5)
        self.assertAlmostEqual(turned.uy, rest.uy, places=5)
        self.assertAlmostEqual(turned.uz, rest.uz, places=5)
        # ...and swings local right a quarter turn onto local back/forward.
        dot = turned.rx * rest.rx + turned.ry * rest.ry + turned.rz * rest.rz
        self.assertAlmostEqual(dot, 0.0, places=5)

    def test_append_object_sets_parent(self):
        sdf = SDF()
        sdf.name = "xpar"
        root = sdf.append_object("xpar1", parent="WORLD")
        child = sdf.append_object("xpara1", parent=root)
        self.assertEqual(root.parent_name(), "WORLD")
        self.assertEqual(child.parent_name(), "xpar1")
        self.assertEqual(sdf.get_object(1, 0, 1, raw=True).parent_name(), "NULL")

    def test_old_pointer_field_names_still_work(self):
        anim = AnimObj()
        anim._orientation_keyframes_ptr = 7
        anim._scale_keyframes_ptr = 8
        anim._position_keyframes_ptr = 9
        self.assertEqual(
            (anim._orientation_keyframe_ptr, anim._scale_keyframe_ptr, anim._position_keyframe_ptr),
            (7, 8, 9),
        )

    def test_authored_anim_serializes_and_reads_back(self):
        sdf = SDF()
        sdf.name = "xspin"
        sdf.lod_dist_list[:] = LOD_DISTANCES
        sdf.revision = 8
        sdf.exit_count = 2
        root = sdf.append_object("xspin1", parent="WORLD")
        blade = sdf.append_object("xspina1", parent=root)
        rest = axis_rotation_transform((0, 0, 1), 0.25)
        rest.px, rest.py, rest.pz = 0.0, 2.0, 0.5
        blade.transform = rest.copy()

        anim = AnimObj()
        anim.name = "xspin"
        frames = [0, 5, 10, 15]
        keys = [
            (frame, spin_orientation_key(rest, (0, 1, 0), 2 * math.pi * frame / 15))
            for frame in frames
        ]
        anim.add_mesh_keys(
            "xspina1",
            orientations=keys,
            positions=[(0, Vector3(rest.px, rest.py, rest.pz))],
            frame_transform=rest,
        )
        anim.add_mesh_keys(
            "xspin1",
            orientations=[(0, orientation_key_from_transform(Transform()))],
        )
        anim.add_animation(0, start=0, length=16, loop=0, speed=15.0)
        sdf.anim_obj = anim

        data = _write_sdf(sdf)
        back = _read_sdf(data)
        self.assertEqual(_write_sdf(back), data)

        banim = back.anim_obj
        self.assertEqual(banim.name, "xspin")
        self.assertEqual(list(banim.mesh_map), ["xspina1", "xspin1"])
        animation = banim.animation_map[0]
        self.assertEqual((animation.start, animation.length, animation.speed), (0, 16, 15.0))

        mesh = banim.mesh_map["xspina1"]
        self.assertEqual((mesh.orientation_start, mesh.orientation_length), (0, 4))
        self.assertEqual((mesh.position_start, mesh.position_length), (0, 1))
        _assert_rotation_close(self, mesh.frame_transform, rest)
        identity = mesh.frame_transform @ mesh.inverse_transform
        _assert_rotation_close(self, identity, Transform())
        self.assertAlmostEqual(identity.py, 0.0, places=5)

        read_keys = banim.orientation_keyframe_list[0:4]
        for (frame, expected), got in zip(keys, read_keys):
            self.assertEqual(got.frame, frame)
            _assert_quat_close(self, got.orientation, expected)
        _assert_rotation_close(self, transform_from_orientation_key(read_keys[0].orientation), rest)
        self.assertEqual(banim.mesh_map["xspin1"].orientation_start, 4)
        self.assertEqual(back.get_object(0, 0, 1, raw=True).parent_name(), "xspin1")


# -- Ogre mesh fixtures ---------------------------------------------------------


def _ogre_chunk(chunk_id, payload=b""):
    return struct.pack("<HI", chunk_id, 6 + len(payload)) + payload


def _bone_assignment(chunk_id, vertex, bone, weight):
    return _ogre_chunk(chunk_id, struct.pack("<IHf", vertex, bone, weight))


def ogre_mesh(*, name_table=None, lod=False, extras=False, manual_edges=False):
    """A one-triangle skinned v1.100 mesh. Bone assignments are stored in
    an order a (vertex, bone) sort would change."""
    geometry = _ogre_chunk(
        MeshChunkID.GEOMETRY,
        struct.pack("<I", 3)
        + _ogre_chunk(
            MeshChunkID.GEOMETRY_VERTEX_DECLARATION,
            _ogre_chunk(MeshChunkID.GEOMETRY_VERTEX_ELEMENT, struct.pack("<5H", 0, 2, 1, 0, 0)),
        )
        + _ogre_chunk(
            MeshChunkID.GEOMETRY_VERTEX_BUFFER,
            struct.pack("<2H", 0, 12)
            + _ogre_chunk(
                MeshChunkID.GEOMETRY_VERTEX_BUFFER_DATA,
                struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0),
            ),
        ),
    )
    submesh = _ogre_chunk(
        MeshChunkID.SUBMESH,
        b"xtest/material\n"
        + b"\x00"  # use shared vertices
        + struct.pack("<I", 3)
        + b"\x00"  # 16-bit indices
        + struct.pack("<3H", 0, 1, 2)
        + geometry
        + _ogre_chunk(MeshChunkID.SUBMESH_OPERATION, struct.pack("<H", 4))
        + _bone_assignment(MeshChunkID.SUBMESH_BONE_ASSIGNMENT, 2, 0, 1.0)
        + _bone_assignment(MeshChunkID.SUBMESH_BONE_ASSIGNMENT, 0, 1, 0.25)
        + _bone_assignment(MeshChunkID.SUBMESH_BONE_ASSIGNMENT, 0, 0, 0.75)
        + _bone_assignment(MeshChunkID.SUBMESH_BONE_ASSIGNMENT, 1, 1, 1.0),
    )
    body = b"\x01" + submesh + _ogre_chunk(MeshChunkID.MESH_SKELETON_LINK, b"xtest.skeleton\n")
    if lod:
        body += _ogre_chunk(MeshChunkID.MESH_LOD_LEVEL, b"\x02\x00lod-payload")
    body += _ogre_chunk(MeshChunkID.MESH_BOUNDS, struct.pack("<7f", 0, 0, 0, 1, 1, 0, 1.4142135))
    if name_table is not None:
        elements = b"".join(
            _ogre_chunk(
                MeshChunkID.SUBMESH_NAME_TABLE_ELEMENT,
                struct.pack("<H", index) + name.encode() + b"\n",
            )
            for index, name in name_table
        )
        body += _ogre_chunk(MeshChunkID.SUBMESH_NAME_TABLE, elements)
    if manual_edges:
        body += _ogre_chunk(
            MeshChunkID.EDGE_LISTS,
            _ogre_chunk(MeshChunkID.EDGE_LIST_LOD, struct.pack("<H", 1) + b"\x01"),
        )
    if extras:
        body += _ogre_chunk(MeshChunkID.POSES, b"pose-bytes")
        body += _ogre_chunk(MeshChunkID.ANIMATIONS, b"anim-bytes")
        body += _ogre_chunk(MeshChunkID.EXTREMES, struct.pack("<H3f", 0, 1, 2, 3))
    return b"\x00\x10[MeshSerializer_v1.100]\n" + _ogre_chunk(MeshChunkID.MESH, body)


def _mesh_round_trip(data):
    mesh = MeshSerializer(io.BytesIO(data), validate_chunk_sizes=True).read()
    out = io.BytesIO()
    MeshSerializer(out, validate_chunk_sizes=True).write(mesh)
    return mesh, out.getvalue()


class MeshFidelityTests(unittest.TestCase):
    def test_mesh_without_name_table_round_trips(self):
        data = ogre_mesh()
        mesh, out = _mesh_round_trip(data)
        self.assertEqual(out, data)
        self.assertNotIn(struct.pack("<H", MeshChunkID.SUBMESH_NAME_TABLE) + b"\x06", out)

    def test_empty_and_named_name_tables_round_trip(self):
        for table in ([], [(0, "xbody")]):
            with self.subTest(table=table):
                data = ogre_mesh(name_table=table)
                self.assertEqual(_mesh_round_trip(data)[1], data)

    def test_bone_assignment_file_order_is_kept(self):
        data = ogre_mesh()
        mesh, out = _mesh_round_trip(data)
        self.assertEqual(out, data)
        # The old sorted order is still what new meshes get.
        mesh.preserve_bone_assignment_order = False
        resorted = io.BytesIO()
        MeshSerializer(resorted).write(mesh)
        self.assertNotEqual(resorted.getvalue(), data)
        self.assertEqual(len(resorted.getvalue()), len(data))

    def test_unmodelled_chunks_are_kept(self):
        data = ogre_mesh(lod=True, extras=True, name_table=[(0, "xbody")])
        mesh, out = _mesh_round_trip(data)
        self.assertEqual(
            [chunk_id for chunk_id, _ in mesh.raw_chunks],
            [MeshChunkID.MESH_LOD_LEVEL, MeshChunkID.POSES, MeshChunkID.ANIMATIONS, MeshChunkID.EXTREMES],
        )
        self.assertEqual(out, data)

    def test_manual_edge_list_round_trips(self):
        data = ogre_mesh(manual_edges=True, extras=True)
        mesh, out = _mesh_round_trip(data)
        self.assertTrue(mesh.edge_lists[1].is_manual)
        self.assertEqual(out, data)


# -- Ogre skeleton --------------------------------------------------------------


def _skeleton_bytes(skeleton, **kwargs):
    out = io.BytesIO()
    SkeletonSerializer(out, **kwargs).write(skeleton)
    return out.getvalue()


class SkeletonSpaceTests(unittest.TestCase):
    def _skeleton(self):
        skeleton = Skeleton()
        skeleton.create_bone(
            "xroot",
            0,
            pos=Vector3(1.0, 2.0, 3.0),
            ori=Quaternion(0.5, 0.5, 0.5, 0.5),
        )
        return skeleton

    def test_ogre_space_writes_values_as_given(self):
        data = _skeleton_bytes(self._skeleton(), ogre_space=True)
        self.assertIn(struct.pack("<3f", 1.0, 2.0, 3.0), data)
        self.assertIn(struct.pack("<4f", 0.5, 0.5, 0.5, 0.5), data)

        back = SkeletonSerializer(io.BytesIO(data), ogre_space=True).read()
        bone = back.get_bone(0)
        self.assertEqual((bone.position.x, bone.position.y, bone.position.z), (1.0, 2.0, 3.0))
        self.assertEqual(_skeleton_bytes(back, ogre_space=True), data)

    def test_default_still_mirrors_x(self):
        data = _skeleton_bytes(self._skeleton())
        self.assertIn(struct.pack("<3f", -1.0, 2.0, 3.0), data)
        self.assertIn(struct.pack("<4f", -0.5, 0.5, 0.5, 0.5), data)

        bz = SkeletonSerializer(io.BytesIO(data)).read().get_bone(0)
        raw = SkeletonSerializer(io.BytesIO(data), ogre_space=True).read().get_bone(0)
        self.assertEqual(bz.position.x, 1.0)
        self.assertEqual(raw.position.x, -1.0)
        self.assertEqual(_skeleton_bytes(SkeletonSerializer(io.BytesIO(data)).read()), data)


if __name__ == "__main__":
    unittest.main()
