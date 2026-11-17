# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Maya-free core of the Maya plugin (maya/bz98maya/core).

Round-trips VDF+GEO and Ogre .mesh files through the neutral scene model using
the toolkit's own serializers. Sources are the synthetic VDF fixtures plus GEO
and mesh files built in the test; no game files are needed. The Maya layer
(maya_io.py, the plug-in) cannot run here and is not covered.

Run:
    python tests/run_tests.py
"""

import math
import os
import shutil
import struct
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "maya"))

import _bootstrap  # noqa: F401,E402

import numpy as np  # noqa: E402

from bz98maya.core import authoring, axes, ogre_io, schema, sdf_io, validate, vdf_io  # noqa: E402
from bz98maya.core.model import (  # noqa: E402
    KIND_EMPTY,
    KIND_HARDPOINT,
    KIND_LOD,
    KIND_PART,
    Mesh,
    Node,
    Scene,
)

from bz98tools import sdf_classes, vdf_file  # noqa: E402
from bz98tools.bzrmodelporter import bzgeo, spacial  # noqa: E402
from bz98tools.bzrmodelporter.bzgeo_serializer import GeoSerializer  # noqa: E402
from bz98tools.ogrefast.pure import import_compat, kenshi_compat  # noqa: E402

FIXTURES = os.path.join(_HERE, "fixtures")
TOL = 1e-5


def _identity_translated(x, y, z):
    m = axes.identity4()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def _write_cube_geo(path, name):
    """A GEO shaped like stock files: BZ axes, inward vertex normals, one normal per vertex."""
    corners = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
    quads = [  # candidate corner index quads per cube face
        (0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3),
    ]
    geo = bzgeo.Geo()
    geo.name = name
    geo._checksum = 69
    geo.vert_count = len(corners)
    geo.vertex_pos_buffer = np.asarray(corners, dtype="<f4")
    geo.vertex_normal_buffer = np.asarray([[-c for c in p] for p in corners], dtype="<f4")  # inward
    for index, quad in enumerate(quads):
        pts = [np.array(corners[i]) for i in quad]
        centroid = sum(pts) / 4.0
        if np.dot(np.cross(pts[1] - pts[0], pts[2] - pts[0]), -centroid) < 0:
            quad = tuple(reversed(quad))  # stock: right-handed cross points inward in BZ space
        face = geo.create_face(index)
        face.color = spacial.Color3(204, 204, 204)
        face.plane = bzgeo.SurfacePlane(spacial.Vector3(0.0, 1.0, 0.0), 1.0)
        face.texture_name = "tex_a" if index < 3 else "tex_b"
        for corner, vi in enumerate(quad):
            u, v = (0.25 * corner, 0.125 * index)
            face.create_face_node(vi, vi, spacial.UV(u, v))
    with open(path, "wb") as handle:
        GeoSerializer(handle).write(geo)


def _expanded(mesh):
    """Per-loop (position, normal, uv0) tuples, independent of vertex sharing."""
    rows = []
    loop = 0
    for face in mesh.faces:
        for vi in face:
            rows.append((
                mesh.positions[vi],
                mesh.normals[loop] if mesh.normals else None,
                mesh.uv_sets[0][loop] if mesh.uv_sets else None,
            ))
            loop += 1
    return rows


class MappingTests(unittest.TestCase):
    def test_known_vertex_roundtrips_through_maya_space(self):
        bz = (1.5, 2.0, 3.0)
        maya = axes.bz_point_to_maya(bz)
        self.assertEqual(maya, (1.5, 2.0, -3.0))  # nose (+Z) mirrors to -Z
        self.assertEqual(axes.maya_point_to_bz(maya), bz)
        # Ogre space is Maya space
        self.assertEqual(axes.ogre_point_to_maya(bz), bz)

    def test_normal_uv_and_matrix_roundtrip(self):
        n = (0.0, 0.6, 0.8)
        self.assertEqual(axes.maya_normal_to_bz(axes.bz_normal_to_maya(n)), n)
        self.assertEqual(axes.bz_normal_to_maya(n), (-0.0, -0.6, 0.8))
        self.assertEqual(axes.maya_uv_to_bz(axes.bz_uv_to_maya((0.25, 0.75))), (0.25, 0.75))
        m12 = [0.9, 0.1, 0.0, 0.0, 1.0, 0.1, 0.1, 0.0, 0.9, 0.0, 0.85, 0.45]
        back = axes.maya_matrix_to_bz12(axes.bz_matrix12_to_maya(m12))
        np.testing.assert_allclose(back, m12, atol=1e-12)

    def test_rotation_keeps_handedness_and_translation_mirrors(self):
        # 90 degrees about BZ Y: x axis -> -z. Rows are the images of the local axes.
        m12 = [0.0, 0.0, -1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 2.0, 3.0]
        maya = axes.bz_matrix12_to_maya(m12)
        self.assertGreater(axes.determinant3(maya), 0.0)  # still a proper rotation
        self.assertEqual(axes.transform_point(maya, (0, 0, 0)), (1.0, 2.0, -3.0))
        # BZ local +X maps to BZ -Z; in Maya that is +Z.
        mx = axes.transform_point(maya, (1, 0, 0))
        np.testing.assert_allclose(mx, (1.0, 2.0, -2.0), atol=1e-12)

    def test_row_vector_flat_roundtrip(self):
        m = _identity_translated(1.0, 2.0, 3.0)
        flat = axes.matrix_to_row_vector_flat(m)
        self.assertEqual(flat[12:15], [1.0, 2.0, 3.0])  # Maya keeps translation in row 3
        self.assertEqual(axes.row_vector_flat_to_matrix(flat), m)


class VdfRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _copy_fixture(self, name):
        folder = os.path.join(self.tmp, "src_" + name.replace(".", "_"))
        os.makedirs(folder)
        shutil.copy(os.path.join(FIXTURES, name), folder)
        return folder, os.path.join(folder, name)

    def test_vdf_with_geo_roundtrip(self):
        folder, path = self._copy_fixture("eyepoint.vdf")
        _write_cube_geo(os.path.join(folder, "fxh11.geo"), "fxh11")

        scene = vdf_io.read_vdf(path)
        # The COLP box becomes the inner_col / outer_col helper meshes, as in Blender.
        self.assertEqual([n.name for n in scene.nodes], ["fxh11", "fxp11", "inner_col", "outer_col"])
        hull, pov = scene.nodes[:2]
        self.assertEqual(hull.kind, KIND_PART)
        self.assertIsNone(hull.parent)
        self.assertEqual(pov.parent, "fxh11")
        self.assertEqual(pov.kind, KIND_HARDPOINT)  # type 40 eyepoint, no GEO
        self.assertIsNone(pov.mesh)
        self.assertEqual(len(hull.mesh.positions), 8)
        self.assertEqual(len(hull.mesh.faces), 6)
        self.assertEqual({m for m in hull.mesh.face_materials}, {"tex_a", "tex_b"})

        # Neutral space is CCW / outward: every face normal points away from the centre.
        for face in hull.mesh.faces:
            pts = [np.array(hull.mesh.positions[i]) for i in face]
            normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
            self.assertGreater(float(np.dot(normal, sum(pts) / 4.0)), 0.0)
        for p, n, _uv in _expanded(hull.mesh):
            self.assertGreater(float(np.dot(n, p)), 0.0)  # loop normals outward in Maya space

        out_dir = os.path.join(self.tmp, "out")
        os.makedirs(out_dir)
        out_path = os.path.join(out_dir, "fxv.vdf")
        warnings = vdf_io.write_vdf(scene, out_path)
        self.assertEqual(warnings, [])

        # Re-read with the toolkit's own serializers.
        with open(path, "rb") as a, open(out_path, "rb") as b:
            src, dst = vdf_file.parse_vdf(a.read()), vdf_file.parse_vdf(b.read())
        self.assertEqual(dst.geocount, src.geocount)
        self.assertEqual(dst.vdfc_name, src.vdfc_name)
        self.assertEqual(dst.vdfc_vehicletype, src.vdfc_vehicletype)
        np.testing.assert_allclose(dst.vdfc_lod_dists, src.vdfc_lod_dists)
        np.testing.assert_allclose(dst.colp_data, src.colp_data)
        for slot in range(src.geocount):
            from bz98tools import vdf_classes

            a_rec, b_rec = vdf_classes.GEOData(), vdf_classes.GEOData()
            a_rec.Read(src.records[slot], 0)
            b_rec.Read(dst.records[slot], 0)
            self.assertEqual(b_rec.name.lower(), a_rec.name.lower())
            self.assertEqual(b_rec.parent.lower(), a_rec.parent.lower())
            self.assertEqual(b_rec.type, a_rec.type)
            self.assertEqual(b_rec.geoflags, a_rec.geoflags)
            np.testing.assert_allclose(b_rec.matrix, a_rec.matrix, atol=TOL)

        # Through the neutral layer again: same hierarchy, transforms, geometry.
        again = vdf_io.read_vdf(out_path)
        self.assertEqual([n.name.lower() for n in again.nodes], ["fxh11", "fxp11", "inner_col", "outer_col"])
        self.assertEqual(again.nodes[1].parent.lower(), "fxh11")
        self.assertEqual(again.nodes[1].kind, KIND_HARDPOINT)
        for before, after in zip(scene.nodes, again.nodes):
            np.testing.assert_allclose(after.matrix, before.matrix, atol=TOL)
        new_mesh = again.nodes[0].mesh
        # welded on (position, normal, UV): the cube's 24 face corners have 24 different UVs
        self.assertEqual(len(new_mesh.positions), 24)
        self.assertEqual(len(new_mesh.faces), 6)
        self.assertEqual(new_mesh.face_materials, hull.mesh.face_materials)
        for old, new in zip(_expanded(hull.mesh), _expanded(new_mesh)):
            np.testing.assert_allclose(new[0], old[0], atol=TOL)
            np.testing.assert_allclose(new[1], old[1], atol=TOL)
            np.testing.assert_allclose(new[2], old[2], atol=TOL)

    def test_geo_vertices_split_on_uv_but_weld_when_everything_matches(self):
        cube = vdf_io.box_mesh((0, 0, 0), (1, 1, 1))
        cube.face_materials = ["t"] * 6
        loops = cube.loop_count()
        cube.normals = [(0.0, 1.0, 0.0)] * loops  # one normal everywhere
        cube.uv_sets = [[(0.5, 0.5)] * loops]  # and one UV everywhere
        self.assertEqual(vdf_io.mesh_to_geo("c", cube).vert_count, 8)
        cube.uv_sets = [[(0.1 * (i % 7), 0.0) for i in range(loops)]]
        self.assertGreater(vdf_io.mesh_to_geo("c", cube).vert_count, 8)

    def test_stock_plane_convention_is_written(self):
        mesh = Mesh(
            positions=[(0, 0, 2), (1, 0, 2), (1, 1, 2), (0, 1, 2)],
            faces=[(0, 1, 2, 3)],
            face_materials=["t"],
        )
        geo = vdf_io.mesh_to_geo("pl", mesh)
        face = geo.face_list[0]
        # Stock: plane normal = normalize(cross(b - a, c - a)) of the BZ-space winding
        # (inward-facing; +Z here), d = -n . a. The quad sits at Maya z = 2 = BZ z = -2,
        # so d = -(0,0,1) . (0,0,-2) = +2.
        np.testing.assert_allclose(face.plane.surface_normal.to_ruf(), (0.0, 0.0, 1.0), atol=TOL)
        self.assertAlmostEqual(face.plane.distance, 2.0, places=5)
        self.assertEqual(geo.vert_count, 4)  # normals synthesised from the face

    def test_newell_normal_survives_collinear_leading_vertices(self):
        pts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
        np.testing.assert_allclose(axes.newell_normal(pts), (0, 0, 1), atol=1e-12)
        mesh = Mesh(positions=pts, faces=[(0, 1, 2, 3, 4)], face_materials=["t"])
        np.testing.assert_allclose(vdf_io.with_normals(mesh)[0], (0, 0, 1), atol=1e-12)
        geo = vdf_io.mesh_to_geo("co", mesh)
        np.testing.assert_allclose(
            geo.face_list[0].plane.surface_normal.to_ruf(), (0, 0, 1), atol=TOL
        )

    def test_polygon_issues(self):
        square = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        self.assertEqual(axes.polygon_issues(square), (False, False))
        lshape = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0)]
        self.assertEqual(axes.polygon_issues(lshape), (False, True))
        warped = [(0, 0, 0), (1, 0, 0), (1, 1, 0.5), (0, 1, 0)]
        self.assertEqual(axes.polygon_issues(warped)[0], True)

    def test_write_warns_once_per_part_for_concave_or_warped_faces(self):
        pts = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0)]
        mesh = Mesh(positions=pts, faces=[(0, 1, 2, 3, 4, 5)] * 2, face_materials=["t", "t"])
        scene = Scene(nodes=[Node("tst11bdy", None, axes.identity4(), KIND_PART, mesh)])
        warnings = vdf_io.write_vdf(scene, os.path.join(self.tmp, "w.vdf"))
        concave = [w for w in warnings if "concave" in w]
        self.assertEqual(len(concave), 1)
        self.assertIn("2 non-planar or concave", concave[0])

    def test_skipped_group_is_baked_into_children(self):
        cube = Mesh(
            positions=[(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)],
            faces=[(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)],
            face_materials=["hull"] * 6,
        )
        rot_y = np.array([[0, 0, 1, 0], [0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 0, 1]], dtype=float)
        rot_y[:3, 3] = (5.0, 2.0, -3.0)
        group_matrix = rot_y.tolist()
        child_matrix = _identity_translated(0.0, 1.0, 4.0)
        grand_matrix = _identity_translated(1.0, 0.0, 0.0)
        scene = Scene(nodes=[
            Node("grp", None, group_matrix, KIND_EMPTY, None),
            Node("tst11bdy", "grp", child_matrix, KIND_PART, cube),
            Node("tst11hp1", "tst11bdy", grand_matrix, KIND_HARDPOINT, None, {"type": 70}),
        ])
        out = os.path.join(self.tmp, "baked.vdf")
        vdf_io.write_vdf(scene, out)
        back = {n.name: n for n in vdf_io.read_vdf(out).nodes}
        self.assertIsNone(back["tst11bdy"].parent)
        self.assertEqual(back["tst11hp1"].parent, "tst11bdy")
        expected = axes.matmul(group_matrix, child_matrix)
        np.testing.assert_allclose(back["tst11bdy"].matrix, expected, atol=TOL)
        np.testing.assert_allclose(
            axes.matmul(back["tst11bdy"].matrix, back["tst11hp1"].matrix),
            axes.matmul(expected, grand_matrix),
            atol=TOL,
        )

    def test_bounds_follow_the_mesh_but_meshless_parts_keep_stored_values(self):
        from bz98tools import vdf_classes

        cube = Mesh(
            positions=[(x, y, z) for x in (0, 2) for y in (0, 2) for z in (0, 2)],
            faces=[(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)],
            face_materials=["h"] * 6,
        )
        stale = {"type": 0, "sphereradius": 999.0, "geocenter": [50.0, 50.0, 50.0], "boxhalfheight": [9.0, 9.0, 9.0]}
        scene = Scene(nodes=[
            Node("tst11bdy", None, axes.identity4(), KIND_PART, cube, dict(stale)),
            Node("tst11dmy", None, axes.identity4(), KIND_EMPTY, None, dict(stale)),
        ])
        out = os.path.join(self.tmp, "b.vdf")
        vdf_io.write_vdf(scene, out)
        with open(out, "rb") as handle:
            parsed = vdf_file.parse_vdf(handle.read())
        records = {}
        for slot in range(parsed.geocount):
            rec = vdf_classes.GEOData()
            rec.Read(parsed.records[slot], 0)
            records[rec.name] = rec
        body, dummy = records["tst11bdy"], records["tst11dmy"]
        np.testing.assert_allclose(body.boxhalfheight, (1.0, 1.0, 1.0), atol=TOL)
        self.assertAlmostEqual(body.sphereradius, 2.0, places=5)  # export_vdf.py:62-79: largest absolute coordinate of the 0..2 cube
        self.assertEqual(dummy.sphereradius, 999.0)
        np.testing.assert_allclose(dummy.geocenter, (50.0, 50.0, 50.0))

    def test_hardpoints_lods_and_hierarchy_from_maya_scene(self):
        cube = Mesh(
            positions=[(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)],
            faces=[(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)],
            face_materials=["hull"] * 6,
        )
        scene = Scene(meta={"name": "tstveh", "vehicletype": 1, "vehiclesize": 2, "mass": 5.0})
        scene.nodes = [
            Node("tst11bdy", None, _identity_translated(0, 1, 0), KIND_PART, cube),
            Node("tst21bdy", None, _identity_translated(0, 1, 0), KIND_LOD, cube),
            Node("tst11tur", "tst11bdy", _identity_translated(0, 2, -1), KIND_PART, cube),
            Node("tst11hp1", "tst11tur", _identity_translated(0.5, 0, -3), KIND_HARDPOINT, None, {"type": 71}),
            Node("tst11pov", "tst11bdy", _identity_translated(0, 1, -2), KIND_HARDPOINT, None, {"type": 40}),
            Node("gun", None, axes.identity4(), KIND_PART, cube),  # no LOD digit -> skipped
            Node("tst31xyz", None, axes.identity4(), KIND_LOD, cube),  # no LOD1 twin -> skipped
        ]
        out = os.path.join(self.tmp, "tstveh.vdf")
        warnings = vdf_io.write_vdf(scene, out)
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "tst21bdy.geo")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "tst11hp1.geo")))

        back = vdf_io.read_vdf(out)
        by_name = {n.name: n for n in back.nodes}
        self.assertEqual(
            set(by_name), {"tst11bdy", "tst21bdy", "tst11tur", "tst11hp1", "tst11pov", "inner_col", "outer_col"}
        )
        self.assertEqual(back.meta["name"], "tstveh")
        self.assertEqual(back.meta["vehicletype"], 1)
        self.assertAlmostEqual(back.meta["mass"], 5.0)
        self.assertEqual(by_name["tst11hp1"].kind, KIND_HARDPOINT)
        self.assertEqual(by_name["tst11hp1"].props["type"], 71)
        self.assertEqual(by_name["tst11pov"].kind, KIND_HARDPOINT)
        self.assertEqual(by_name["tst21bdy"].kind, KIND_LOD)
        self.assertEqual(by_name["tst11tur"].kind, KIND_PART)
        self.assertEqual(by_name["tst11hp1"].parent, "tst11tur")
        self.assertEqual(by_name["tst11tur"].parent, "tst11bdy")
        self.assertIsNone(by_name["tst11bdy"].parent)
        np.testing.assert_allclose(
            by_name["tst11hp1"].matrix, _identity_translated(0.5, 0, -3), atol=TOL
        )
        self.assertEqual(len(by_name["tst11bdy"].mesh.faces), 6)
        # Parents precede children in slot order.
        order = [n.name for n in back.nodes if n.name.startswith("tst1")]
        self.assertLess(order.index("tst11bdy"), order.index("tst11tur"))
        self.assertLess(order.index("tst11tur"), order.index("tst11hp1"))
        # No helpers were given: the collision box defaults to the parts' bounding box.
        inner = by_name["inner_col"].mesh.positions
        self.assertEqual(len(inner), 8)

    def test_combination_fixture_keeps_part_classes(self):
        _folder, path = self._copy_fixture("combination.vdf")
        scene = vdf_io.read_vdf(path)
        kinds = {n.name: n.kind for n in scene.nodes}
        self.assertEqual(kinds["cxp11"], KIND_HARDPOINT)  # eyepoint
        self.assertEqual(kinds["cxa11"], KIND_EMPTY)  # no GEO beside the fixture
        self.assertEqual({n.props["type"] for n in scene.nodes if "type" in n.props}, {60, 40, 66, 9})


def _sdf_record(name, parent="WORLD", matrix=None, type_=61, ddr=0, x=0.0, y=0.0, z=0.0, time=0.0):
    geo = sdf_classes.GEOData()
    geo.name, geo.parent = name, parent
    geo.matrix = list(matrix or [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
    geo.geocenter, geo.sphereradius, geo.boxhalfheight = [0.0, 0.0, 0.0], 1.0, [1.0, 1.0, 1.0]
    geo.type, geo.geoflags = type_, 0
    geo.ddr, geo.x, geo.y, geo.z, geo.time = ddr, x, y, z, time
    return geo


def _write_sdf(path, names_by_band, parts, geocount):
    """A minimal SDF the way stock files are laid out (bands 0/2/4 = LOD1/2/3, 1/3/5 blank)."""
    sdfc = sdf_classes.SDFCHeader()
    sdfc.name, sdfc.structuretype = "Test Depot", 1
    sdfc.lod1dist, sdfc.lod2dist, sdfc.lod3dist, sdfc.lod4dist, sdfc.lod5dist = 400.0, 800.0, 50000.0, 50000.0, 50000.0
    sdfc.ddr, sdfc.explosioneffect, sdfc.explosionsound = 500000, "xbldx1.xdf", "null"
    sgeo = sdf_classes.SGEOHeader()
    sgeo.geocount = geocount
    sgeo.sectionlength = geocount * 120 * 6 + sgeo.binlength
    null = _sdf_record("NULL", "NULL", [0] * 12, 0, 50000)
    with open(path, "wb") as handle:
        sdf_classes.SDFHeader().Write(handle, 0)
        sdfc.Write(handle, 0)
        sgeo.Write(handle, 0)
        for band in range(6):
            names = names_by_band.get(band, [])
            for slot in range(geocount):
                if slot < len(names):
                    parts[names[slot]].Write(handle, 0)
                else:
                    null.Write(handle, 0)
        exit_section = sdf_classes.EXITSection()
        exit_section.Write(handle, 0)
        exit_section.Write(handle, 0)


class SdfRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _make_source(self):
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        record = _sdf_record
        parts = {
            "tst11bdy": record("tst11bdy", ddr=3, time=1.5),
            "tst11dck": record(
                "tst11dck", "tst11bdy", [0, 0, -1, 0, 1, 0, 1, 0, 0, 1.0, 2.0, 3.0], 9, 0, 0.25, 0.5, 0.75, 2.0
            ),
            "tst11hp1": record("tst11hp1", "tst11bdy", None, 70, 0),
            "tst21bdy": record("tst21bdy", ddr=3),
            "op1bda01": record("op1bda01"),
        }
        path = os.path.join(src, "tstdep.sdf")
        _write_sdf(
            path,
            {
                0: ["tst11bdy", "tst11dck", "tst11hp1", "op1bda01"],
                2: ["tst21bdy", "tst11dck", "tst11hp1"],  # stock style: LOD1 names repeated
            },
            parts,
            4,
        )
        for name in ("tst11bdy", "tst11dck", "tst21bdy", "op1bda01"):
            _write_cube_geo(os.path.join(src, name + ".geo"), name)
        return src, path

    def test_sdf_roundtrip_keeps_parts_matrices_and_sdf_fields(self):
        src, path = self._make_source()
        scene = sdf_io.read_sdf(path)
        self.assertEqual(scene.warnings, [])
        by_name = {n.name: n for n in scene.nodes}
        self.assertEqual(set(by_name), {"tst11bdy", "tst11dck", "tst11hp1", "tst21bdy", "op1bda01"})
        self.assertEqual(by_name["tst11dck"].parent, "tst11bdy")
        self.assertEqual(by_name["tst11hp1"].kind, KIND_HARDPOINT)
        self.assertEqual(by_name["tst21bdy"].kind, KIND_LOD)
        self.assertEqual(len(by_name["tst11bdy"].mesh.faces), 6)
        self.assertEqual(scene.meta["name"], "Test Depot")
        self.assertEqual(scene.meta["structuretype"], 1)
        self.assertEqual(scene.meta["ddr"], 500000)
        self.assertEqual(by_name["tst11dck"].props["x"], 0.25)
        self.assertEqual(by_name["tst11dck"].props["time"], 2.0)
        self.assertEqual(by_name["tst11bdy"].props["ddr"], 3)

        out_dir = os.path.join(self.tmp, "out")
        os.makedirs(out_dir)
        out = os.path.join(out_dir, "tstdep.sdf")
        self.assertEqual(sdf_io.write_sdf(scene, out), [])
        for name in ("tst11bdy", "tst11dck", "tst21bdy", "op1bda01"):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, name + ".geo")), name)
        self.assertFalse(os.path.exists(os.path.join(out_dir, "tst11hp1.geo")))

        # Container read back with the toolkit's own section classes.
        with open(out, "rb") as handle:
            data = handle.read()
        header = sdf_classes.SDFHeader()
        pos = header.Read(data, 0)
        self.assertEqual((header.BWDHeader, header.REVHeader, header.Unknown), (b"BWD2", b"REV\0", 8))
        sdfc = sdf_classes.SDFCHeader()
        pos = sdfc.Read(data, pos)
        sgeo = sdf_classes.SGEOHeader()
        pos = sgeo.Read(data, pos)
        self.assertEqual((sdfc.name, sdfc.structuretype, sdfc.ddr), ("Test Depot", 1, 500000))
        self.assertEqual((sdfc.explosioneffect, sdfc.explosionsound), ("xbldx1.xdf", "null"))
        self.assertEqual(sgeo.geocount, 4)
        self.assertEqual(sgeo.sectionlength, 4 * 120 * 6 + 12)
        bands = []
        for _band in range(6):
            names = []
            for _slot in range(sgeo.geocount):
                geo = sdf_classes.GEOData()
                pos = geo.Read(data, pos)
                names.append(geo)
            bands.append(names)
        self.assertEqual([g.name for g in bands[0]], ["tst11bdy", "tst11dck", "tst11hp1", "op1bda01"])
        self.assertEqual(bands[2][0].name, "tst21bdy")
        self.assertTrue(all(g.name == "NULL" for g in bands[1] + bands[3] + bands[5]))
        self.assertEqual(data[pos:], b"EXIT\x08\0\0\0" * 2)
        deck = bands[0][1]
        np.testing.assert_allclose(deck.matrix, [0, 0, -1, 0, 1, 0, 1, 0, 0, 1.0, 2.0, 3.0], atol=TOL)
        self.assertEqual((deck.type, deck.parent.lower(), deck.ddr), (9, "tst11bdy", 0))
        self.assertEqual((deck.x, deck.y, deck.z, deck.time), (0.25, 0.5, 0.75, 2.0))

        again = sdf_io.read_sdf(out)
        self.assertEqual({k: v for k, v in again.meta.items()}, scene.meta)
        self.assertEqual({n.name: n.parent for n in again.nodes}, {n.name: n.parent for n in scene.nodes})
        for node in scene.nodes:
            twin = next(n for n in again.nodes if n.name == node.name)
            np.testing.assert_allclose(twin.matrix, node.matrix, atol=TOL)
            self.assertEqual(twin.props["type"], node.props["type"])
            self.assertEqual(twin.props["ddr"], node.props["ddr"])
            self.assertEqual(twin.mesh is None, node.mesh is None)
            if node.mesh:
                self.assertEqual(len(twin.mesh.faces), len(node.mesh.faces))
                self.assertGreaterEqual(len(twin.mesh.positions), len(node.mesh.positions))

    def test_sdf_rejects_non_sdf(self):
        bad = os.path.join(self.tmp, "bad.sdf")
        with open(bad, "wb") as handle:
            handle.write(b"\0" * 200)
        with self.assertRaises(ValueError):
            sdf_io.read_sdf(bad)


def _non_default(fld, index=1):
    """A value different from the field's default, inside its range."""
    kind = fld.kind
    if kind == "bool":
        return not bool(fld.default)
    if kind == "int":
        lo, hi = fld.lo, fld.hi
        value = int(fld.default or 0) + 3 + index
        if hi is not None and value > hi:
            value = int(hi) - 1
        return value
    if kind == "float":
        return float(fld.default or 0.0) + 1.5 + index
    if kind == "string":
        return "ab%d" % index
    if kind == "enum":
        values = [v for _l, v in fld.enum]
        default = fld.default if isinstance(fld.default, int) else 0
        return next(v for v in values if v != default)
    if kind == "float3":
        return (1.25 + index, -2.5, 3.75)
    if kind == "int3":
        return (7, -8, 9)
    if kind == "intarray":
        return (1, -2, 3, -4, 5)
    if kind == "floatarray":
        return tuple(float(i) * 0.5 + index for i in range(12))
    raise AssertionError(kind)


class AuthoringMappingTests(unittest.TestCase):
    """Attribute values <-> neutral props, for every schema field, with non-default values."""

    def test_schema_uses_blenders_own_text_and_geo_types(self):
        fields = {f.blender: f for f in schema.part_fields()}
        self.assertIn("What kind of GEO", fields["GEOType"].tip)
        self.assertIn("Sets where the center", fields["GeoCenter"].tip)
        self.assertEqual(fields["SphereRadius"].default, 3.0)
        types = dict(schema.geo_types())
        self.assertEqual(len(types), 42)
        self.assertTrue(types[70].startswith("WEAPON_HARDPOINT"))
        self.assertTrue(types[71].startswith("CANNON_HARDPOINT"))
        self.assertEqual(len(fields["GEOType"].enum), 42)
        self.assertIn(40, schema.geo_type_hints())
        self.assertIn("70 - WEAPON_HARDPOINT", schema.geo_type_reference_text())
        self.assertEqual(schema.blender_constant("VALIDATION_PRESET_ITEMS")[0][0], "AUTO")
        # no attribute name collisions
        attrs = [f.attr for f in schema.part_fields()] + [f.attr for f in schema.model_fields()]
        self.assertEqual(len(attrs), len(set(attrs)))

    def test_every_part_field_round_trips_with_non_default_values(self):
        props = {}
        for i, fld in enumerate(schema.part_fields()):
            if fld.derived:
                continue
            value = _non_default(fld, i)
            if fld.keys:
                for key, component in zip(fld.keys, value):
                    props[key] = component
            elif fld.key == "bounds_mode":
                props[fld.key] = schema.BOUNDS_MODES[value]
            elif fld.key.startswith("damage") and fld.key[-1:] in "123":
                props.setdefault("damage", ["", "", ""])[int(fld.key[-1]) - 1] = value
            elif fld.kind in ("float3", "floatarray"):
                props[fld.key] = list(value)
            else:
                props[fld.key] = value
        props["lod"] = 2  # a key with no schema field travels as extra
        values, extra = authoring.part_attr_values(props)
        self.assertEqual(extra, {"lod": 2})
        for fld in schema.part_fields():
            self.assertIn(fld.attr, values, fld.attr)
        back = authoring.part_props_from_attrs(values, extra)
        for key, value in props.items():
            if isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
                self.assertEqual(list(back[key]), list(value), key)
            elif isinstance(value, (list, tuple)):
                np.testing.assert_allclose(back[key], value, err_msg=key)
            elif isinstance(value, float):
                self.assertAlmostEqual(back[key], value, places=6, msg=key)
            elif key == "flags":
                continue
            else:
                self.assertEqual(back[key], value, key)

    def test_object_flags_bits_are_decoded_edited_and_unknown_bits_survive(self):
        raw = 0x1 | 0x10 | 0x2000 | (1 << 16) | 0x40000020  # keep bounds, view render, collision, team 1, 2 unknown bits
        values, _ = authoring.part_attr_values({"flags": raw})
        self.assertTrue(values["bzFlagKeepBounds"])
        self.assertFalse(values["bzFlagDestroyedSeed"])
        self.assertEqual(values["bzCollisionClass"], raw & 0xF000)
        self.assertEqual(values["bzObjFlagTeam"], (raw & 0xF0000) >> 16)
        self.assertEqual(values["bzGEOFlags"], raw)
        # untouched attributes recompose to the same raw value
        self.assertEqual(authoring.part_props_from_attrs(values)["flags"], raw)
        # toggling bits through the decoded attributes keeps every unknown bit
        values["bzFlagKeepBounds"] = False
        values["bzFlagDestroyedSeed"] = True
        values["bzObjFlagTeam"] = 7
        edited = authoring.part_props_from_attrs(values)["flags"]
        self.assertEqual(edited & 0x1, 0)
        self.assertEqual(edited & 0x200, 0x200)
        self.assertEqual((edited & 0xF0000) >> 16, 7)
        self.assertEqual(edited & 0x40000020, 0x40000020)  # unknown bit 30 and bit 5
        # bit 31 uses Maya's signed 32-bit int
        values, _ = authoring.part_attr_values({"flags": 0x80000001})
        self.assertLess(values["bzGEOFlags"], 0)
        self.assertEqual(authoring.part_props_from_attrs(values)["flags"], values["bzGEOFlags"])

    def test_every_model_field_round_trips_with_non_default_values(self):
        meta = {}
        for i, fld in enumerate(schema.model_fields()):
            if fld.key.startswith("lod") and fld.key[3:].isdigit():
                continue
            meta[fld.key] = _non_default(fld, i)
        meta["lod_dists"] = [11.0, 22.0, 33.0, 44.0, 55.0]
        meta["preserved"] = {"plan": ["vdfc"]}
        values, extra = authoring.meta_attr_values(meta)
        self.assertEqual(extra, {"preserved": {"plan": ["vdfc"]}})
        self.assertEqual(values["bzLOD3"], 33.0)
        back = authoring.meta_from_attr_values(values, extra)
        self.assertEqual(back["lod_dists"], [11.0, 22.0, 33.0, 44.0, 55.0])
        for key, value in meta.items():
            if key in ("lod_dists", "preserved"):
                continue
            if isinstance(value, (list, tuple)):
                self.assertEqual(list(back[key]), list(value), key)
            elif isinstance(value, float):
                self.assertAlmostEqual(back[key], value, places=6, msg=key)
            else:
                self.assertEqual(back[key], value, key)
        defaults = authoring.default_meta()
        self.assertEqual(defaults["vehiclesize"], 2)
        self.assertEqual(defaults["lod_dists"][0], 5.0)


class AuthoringFileTests(unittest.TestCase):
    """Non-default authoring values survive write -> read through the real VDF / SDF / GEO files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _scene(self):
        cube = vdf_io.box_mesh((0, 0, 0), (2, 2, 2))
        cube.face_materials = ["hull"] * 6
        props = {
            "type": 60, "flags": 0x40000001 | 0x2000 | (3 << 16),
            "bounds_mode": "PRESERVE", "authored_bounds": True,
            "geocenter": [1.5, 2.5, 3.5], "sphereradius": 9.25, "boxhalfheight": [4.0, 5.0, 6.0],
            "damage": ["tst11d1", "", "tst11d3"],
            "ddr": 777, "x": 1.0, "y": 0.2, "z": 3.0, "time": 4.5,
            "header_unknown": 71, "header_flags": 5,
            "face_unknown": 0, "face_shade": 5, "face_texture": 1, "face_xlu": 2, "face_parent": 3, "face_node": 4,
        }
        spinner = {
            "type": 15, "flags": 0, "is_spinner": True, "spinner_axis": [0.0, 1.0, 0.0],
            "spinner_speed": 2.5, "spinner_target": "tst11bdy",
        }
        raw = {
            "type": 61, "use_raw_matrix": True,
            "raw_matrix": [2.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 4.0, 1.0, 2.0, 3.0],
        }
        scene = Scene(
            nodes=[
                Node("tst11bdy", None, axes.identity4(), KIND_PART, cube, props),
                Node("tst11spn", "tst11bdy", axes.identity4(), KIND_EMPTY, None, spinner),
                Node("tst11raw", "tst11bdy", _identity_translated(9, 9, 9), KIND_PART, cube, raw),
            ],
            meta={
                "name": "Authored", "vehicletype": 3, "vehiclesize": 4, "mass": 999.0, "multiplyer": 2.0,
                "drag": 0.5, "lod_dists": [10.0, 20.0, 30.0, 40.0, 50.0], "vdfc_null": 12,
                "use_scps": True, "scps_data": [1, 2, 3], "structuretype": 5, "ddr": 4242,
                "explosioneffect": "boom.xdf", "explosionsound": "boom",
            },
        )
        return scene

    def test_vdf_part_and_model_fields_survive_a_file_round_trip(self):
        out = os.path.join(self.tmp, "tst.vdf")
        scene = self._scene()
        self.assertEqual([w for w in vdf_io.write_vdf(scene, out) if "concave" not in w], [])
        back = vdf_io.read_vdf(out)
        by_name = {n.name: n for n in back.nodes}
        body = by_name["tst11bdy"].props
        self.assertEqual(body["type"], 60)
        self.assertEqual(body["flags"], 0x40000001 | 0x2000 | (3 << 16))
        self.assertEqual(body["bounds_mode"], "PRESERVE")
        np.testing.assert_allclose(body["geocenter"], [1.5, 2.5, 3.5], atol=TOL)  # stored, not recomputed
        self.assertAlmostEqual(body["sphereradius"], 9.25, places=5)
        np.testing.assert_allclose(body["boxhalfheight"], [4.0, 5.0, 6.0], atol=TOL)
        self.assertEqual(body["damage"], ["tst11d1", "", "tst11d3"])
        self.assertEqual((body["header_unknown"], body["header_flags"]), (71, 5))
        self.assertEqual(
            (body["face_shade"], body["face_texture"], body["face_xlu"], body["face_parent"], body["face_node"]),
            (5, 1, 2, 3, 4),
        )
        spin = by_name["tst11spn"].props
        self.assertTrue(spin["is_spinner"])
        np.testing.assert_allclose(spin["spinner_axis"], [0.0, 1.0, 0.0], atol=TOL)
        self.assertAlmostEqual(spin["spinner_speed"], 2.5, places=5)
        self.assertEqual(spin["spinner_target"], "tst11bdy")
        raw = by_name["tst11raw"]
        np.testing.assert_allclose(raw.props["raw_matrix"], [2, 0, 0, 0, 3, 0, 0, 0, 4, 1, 2, 3], atol=TOL)
        # the raw matrix is what was written, not the node's own transform
        np.testing.assert_allclose(np.array(raw.matrix)[:3, :3].diagonal(), [2.0, 3.0, 4.0], atol=TOL)
        self.assertEqual(
            {k: back.meta[k] for k in ("name", "vehicletype", "vehiclesize", "vdfc_null", "use_scps")},
            {"name": "Authored", "vehicletype": 3, "vehiclesize": 4, "vdfc_null": 12, "use_scps": True},
        )
        self.assertEqual(back.meta["scps_data"], [1, 2, 3])
        self.assertAlmostEqual(back.meta["mass"], 999.0, places=3)
        self.assertAlmostEqual(back.meta["drag"], 0.5, places=5)
        np.testing.assert_allclose(back.meta["lod_dists"], [10, 20, 30, 40, 50])
        # Spinner helpers are written right after their target slot.
        order = [n.name for n in back.nodes if n.name.startswith("tst11")]
        self.assertEqual(order.index("tst11spn"), order.index("tst11bdy") + 1)
        # damage names are real VGEO band records (lod slot 0, states 1 and 3)
        with open(out, "rb") as handle:
            parsed = vdf_file.parse_vdf(handle.read())
        names = [r[:8].split(b"\0")[0].decode() for r in parsed.records]
        self.assertEqual(names[1 * parsed.geocount], "tst11d1")
        self.assertEqual(names[3 * parsed.geocount], "tst11d3")
        self.assertEqual(names[2 * parsed.geocount], "")  # state 2 stays zeros, as Blender writes it

    def test_bounds_modes_follow_blenders_export_rules(self):
        cube = vdf_io.box_mesh((0, 0, 0), (2, 2, 2))
        cube.face_materials = ["h"] * 6
        stored = {"type": 60, "geocenter": [9.0, 9.0, 9.0], "sphereradius": 99.0, "boxhalfheight": [8.0, 8.0, 8.0]}

        def radius(extra):
            scene = Scene(nodes=[Node("tst11bdy", None, axes.identity4(), KIND_PART, cube, dict(stored, **extra))])
            out = os.path.join(self.tmp, "b.vdf")
            vdf_io.write_vdf(scene, out)
            return vdf_io.read_vdf(out).nodes[0].props["sphereradius"]

        self.assertAlmostEqual(radius({"bounds_mode": "PRESERVE", "authored_bounds": True}), 99.0, places=4)
        self.assertAlmostEqual(radius({"bounds_mode": "AUTO", "generate_collision": False}), 99.0, places=4)
        derived = radius({"bounds_mode": "RECALC"})
        self.assertAlmostEqual(derived, 2.0, places=4)  # GenerateGEOCollisions: the largest absolute coordinate
        self.assertAlmostEqual(radius({"bounds_mode": "AUTO", "generate_collision": True}), derived, places=4)
        self.assertAlmostEqual(radius({"bounds_mode": "PRESERVE", "authored_bounds": False}), derived, places=4)

    def test_sdf_target_fields_and_header_survive(self):
        scene = self._scene()
        out = os.path.join(self.tmp, "tst.sdf")
        sdf_io.write_sdf(scene, out)
        back = sdf_io.read_sdf(out)
        by_name = {n.name: n for n in back.nodes}
        body = by_name["tst11bdy"].props
        self.assertEqual((body["ddr"], body["x"], body["time"]), (777, 1.0, 4.5))
        self.assertAlmostEqual(body["y"], 0.2, places=6)
        self.assertEqual(
            (back.meta["structuretype"], back.meta["ddr"], back.meta["explosioneffect"], back.meta["explosionsound"]),
            (5, 4242, "boom.xdf", "boom"),
        )

    def test_geo_header_and_face_defaults_survive_standalone_geo(self):
        node = self._scene().nodes[0]
        path = os.path.join(self.tmp, "solo.geo")
        vdf_io.write_geo(node, path)
        back = vdf_io.read_geo(path).nodes[0]
        self.assertEqual(back.name, "solo")
        self.assertEqual(len(back.mesh.faces), 6)
        self.assertEqual((back.props["header_unknown"], back.props["header_flags"]), (71, 5))
        self.assertEqual(
            (back.props["face_shade"], back.props["face_texture"], back.props["face_xlu"],
             back.props["face_parent"], back.props["face_node"]),
            (5, 1, 2, 3, 4),
        )
        with self.assertRaises(ValueError):
            vdf_io.write_geo(Node("empty"), os.path.join(self.tmp, "none.geo"))

    def test_preserved_chunks_vloc_and_damage_bands_round_trip(self):
        for fixture in ("combination.vdf", "damage_rep.vdf"):
            src = os.path.join(FIXTURES, fixture)
            scene = vdf_io.read_vdf(src)
            out = os.path.join(self.tmp, fixture)
            vdf_io.write_vdf(scene, out)
            back = vdf_io.read_vdf(out)
            for key in ("chunks", "vlocs", "bands"):
                self.assertEqual(back.meta["preserved"][key], scene.meta["preserved"][key], (fixture, key))
        combo = vdf_io.read_vdf(os.path.join(FIXTURES, "combination.vdf"))
        self.assertEqual(len(combo.meta["preserved"]["vlocs"]), 4)
        self.assertEqual(len(combo.meta["preserved"]["chunks"]), 1)  # the SCHK chunk
        damage = vdf_io.read_vdf(os.path.join(FIXTURES, "damage_rep.vdf"))
        self.assertEqual(
            {n.name: n.props["damage"] for n in damage.nodes if n.props}["fxv11"], ["fxvd1", "fxvd2", "fxvd3"]
        )

    def test_collision_helpers_become_colp_with_the_files_axis_slots(self):
        cube = vdf_io.box_mesh((0, 0, 0), (1, 1, 1))
        cube.face_materials = ["h"] * 6
        # BZ extents: x -2..2, y (up) 0..1, z (forward) -3..4  -> Maya z is mirrored
        lo_i = [min(a, b) for a, b in zip(axes.bz_point_to_maya((-1, 0.25, -1)), axes.bz_point_to_maya((1, 0.75, 2)))]
        hi_i = [max(a, b) for a, b in zip(axes.bz_point_to_maya((-1, 0.25, -1)), axes.bz_point_to_maya((1, 0.75, 2)))]
        lo_o = [min(a, b) for a, b in zip(axes.bz_point_to_maya((-2, 0, -3)), axes.bz_point_to_maya((2, 1, 4)))]
        hi_o = [max(a, b) for a, b in zip(axes.bz_point_to_maya((-2, 0, -3)), axes.bz_point_to_maya((2, 1, 4)))]
        scene = Scene(nodes=[
            Node("tst11bdy", None, axes.identity4(), KIND_PART, cube, {"type": 60}),
            Node("inner_col", None, axes.identity4(), KIND_PART, vdf_io.box_mesh(lo_i, hi_i)),
            Node("outer_col", None, _identity_translated(0, 0, 0), KIND_PART, vdf_io.box_mesh(lo_o, hi_o)),
        ])
        out = os.path.join(self.tmp, "col.vdf")
        self.assertEqual([w for w in vdf_io.write_vdf(scene, out) if "inner_col" in w or "outer_col" in w], [])
        with open(out, "rb") as handle:
            colp = vdf_file.parse_vdf(handle.read()).colp_data
        # [Y out max, Y in max, Y in min, Y out min, X ..., Z ...]: file "Y" = BZ z, file "Z" = BZ y
        np.testing.assert_allclose(
            colp, [4, 2, -1, -3, 2, 1, -1, -2, 1.0, 0.75, 0.25, 0.0], atol=TOL
        )
        back = vdf_io.read_vdf(out)
        helpers = {n.name: n for n in back.nodes}
        pts = np.array(helpers["outer_col"].mesh.positions)
        np.testing.assert_allclose(pts.min(axis=0), lo_o, atol=TOL)
        np.testing.assert_allclose(pts.max(axis=0), hi_o, atol=TOL)
        self.assertEqual(len(helpers["inner_col"].mesh.faces), 6)


class ValidationTests(unittest.TestCase):
    def _broken(self):
        cube = vdf_io.box_mesh((0, 0, 0), (1, 1, 1))
        cube.face_materials = ["averyloongtexture"] * 6

        def node(name, parent=None, mesh=cube, props=None, matrix=None):
            return Node(name, parent, matrix or axes.identity4(), KIND_PART if mesh else KIND_EMPTY, mesh, props or {})

        scaled = np.identity(4)
        scaled[0][0] = 2.0
        return Scene(nodes=[
            node("abc11bdy", props={"type": 60}),
            node("abc11gc9", "abc11bdy", cube, {"type": 71}),  # cannon hardpoint, suffix gc9
            node("abc21cok", "abc11bdy", cube, {"type": 69}),  # LOD2 not parented to its LOD1 twin
            node("toolongname1", None, cube),
            node("x", None, cube),
            node("abc11sca", "abc11bdy", cube, {"type": 60}, scaled.tolist()),
            node("abc11pov", None, None, {"type": 40}),
            node("abc11pv2", None, None, {"type": 40}),
        ])

    def test_broken_scene_reports_the_same_issues_as_blenders_validator(self):
        issues = validate.validate_scene(self._broken(), export_mode="VDF")
        found = {(i["severity"], i["scope"], i["target"]) for i in issues}
        expected = {
            ("ERROR", "Scene", "POV"),  # two type-40 parts
            ("WARNING", "Eyepoint", "abc11pov, abc11pv2"),
            ("WARNING", "LOD", "abc21cok"),  # no LOD1 twin
            ("WARNING", "Object", "abc21cok"),  # parent in another LOD set
            ("WARNING", "Object", "toolongname1"),  # name too long
            ("WARNING", "Object", "x"),  # name too short
            ("WARNING", "GEO Type", "abc11gc9"),  # hardpoint suffix
            ("WARNING", "Transform", "abc11sca"),  # non-applied scale
            ("WARNING", "Material", "averyloongtexture"),  # > 8 character texture
            ("WARNING", "Scene", "inner_col"),
            ("WARNING", "Scene", "outer_col"),
            ("INFO", "Orientation", "vehicle front"),
        }
        self.assertTrue(expected <= found, expected - found)
        messages = " ".join(i["message"] for i in issues)
        self.assertIn("Multiple exportable Type 40 POV GEOs detected", messages)
        self.assertIn("Type 71 cannon hardpoint GEOs should use suffix gc1-gc5", messages)
        self.assertIn("exceeds 8 characters", messages)
        self.assertEqual(validate.counts(issues, "VDF")["ERROR"], 1)
        # an SDF check scope: type-40 / spinner helpers are VDF-only
        self.assertFalse(any(i["scope"] in ("Animation", "Armature") for i in issues))

    def test_clean_vehicle_has_no_errors_or_warnings(self):
        cube = vdf_io.box_mesh((0, 0, 0), (1, 1, 1))
        cube.face_materials = ["hull"] * 6
        scene = Scene(nodes=[
            Node("abc11bdy", None, axes.identity4(), KIND_PART, cube, {"type": 60}),
            Node("abc11pov", "abc11bdy", axes.identity4(), KIND_HARDPOINT, None, {"type": 40}),
            Node("abc11gc1", "abc11bdy", axes.identity4(), KIND_HARDPOINT, None, {"type": 71}),
            Node("inner_col", None, axes.identity4(), KIND_PART, vdf_io.box_mesh((0, 0, 0), (1, 1, 1))),
            Node("outer_col", None, axes.identity4(), KIND_PART, vdf_io.box_mesh((0, 0, 0), (1, 1, 1))),
        ])
        issues = validate.validate_scene(scene, export_mode="VDF")
        counts = validate.counts(issues, "VDF")
        self.assertEqual((counts["ERROR"], counts["WARNING"]), (0, 0), [i["message"] for i in issues])

    def test_geometry_checks_use_blender_axes(self):
        # A BRIDGE root with an upward deck: the Maya +Y face must count as "up" (Blender +Z).
        cube = vdf_io.box_mesh((-1, -1, -1), (1, 0, 1))
        cube.face_materials = ["deck"] * 6
        scene = Scene(nodes=[
            Node("brg11bdy", None, axes.identity4(), KIND_PART, cube, {"type": 8}),
            Node("brg11dck", "brg11bdy", axes.identity4(), KIND_PART, cube, {"type": 9}),
        ])
        issues = validate.validate_scene(scene, export_mode="VDF")
        self.assertFalse(any(i["scope"] == "Deck" and i["severity"] == "WARNING" for i in issues), issues)

    def test_fix_legacy_name_matches_blenders_rules(self):
        fixed = validate.fix_legacy_name("My Tank", [])
        self.assertEqual(len(fixed), 7)
        self.assertEqual((fixed[3], fixed[4]), ("3", "1"))  # LOD defaults to 3 like Blender
        self.assertEqual(validate.fix_legacy_name("abc2yy", [])[3], "2")
        taken = validate.fix_legacy_name("zzz", [])
        self.assertNotEqual(validate.fix_legacy_name("zzz", [taken]), taken)
        specs = validate.quick_vehicle_specs()
        self.assertEqual([s[0] for s in specs][:2], ["bod", "pov"])
        self.assertEqual({s[1] for s in specs}, {60, 40, 71, 72, 74, 73, 66, 67})



class OgreRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _source_mesh(self):
        serializer = kenshi_compat.KenshiObjectSerializer()
        mesh = serializer.create_mesh("src.mesh")
        submeshes = []
        specs = [("Hull", "bz98/hull", 0.0), ("Glass", "bz98/glass", 5.0)]
        for index, (name, material, shift) in enumerate(specs):
            sub = kenshi_compat.SubMeshData()
            sub.index = index
            sub.submesh_name = name
            sub.material = material
            # Blender-space inputs; the exporter converts to Ogre space.
            positions = np.asarray(
                [[shift, 0, 0], [shift + 1, 0, 0], [shift + 1, 1, 0], [shift, 1, 1]], dtype=np.float32
            )
            loops = np.asarray([0, 1, 2, 0, 2, 3], dtype=np.int32)
            normals = np.asarray([[0, 0, 1]] * 6, dtype=np.float32)
            uvs = np.asarray(
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32
            )
            out = sub.set_vertex(
                nd_vert_indices=loops,
                nd_loop_indices=np.arange(6, dtype=np.int32),
                nd_positions=positions,
                nd_normals=normals,
                nd_tangents=np.empty(3, dtype=np.float32),
                nd_bitangent_signs=np.empty(1, dtype=np.float32),
                nd_bitangents=np.empty(3, dtype=np.float32),
                nd_texcoords=uvs,
                nd_colors=np.empty(4, dtype=np.float32),
                nd_alphas=np.empty(4, dtype=np.float32),
                tangent_dimensions=3,
                optimize=True,
            )
            sub.set_bone_assignments([], out)
            submeshes.append(sub)
        mesh.set_submeshes(submeshes)
        path = os.path.join(self.tmp, "src.mesh")
        serializer.save_mesh(mesh, path, kenshi_compat.MeshVersion.V_1_10)
        return path

    @staticmethod
    def _load(path):
        importer = import_compat.KenshiObjectSerializer()
        importer.add_resource_location(os.path.dirname(path))
        return importer.load_mesh(os.path.basename(path))

    @staticmethod
    def _corners(submesh):
        geometry = submesh._wire_geometry
        pos = import_compat._decode_semantic(geometry, {"POSITION"})[:, :3]
        uv = import_compat._decode_semantic(geometry, {"TEXCOORD"})[:, :2]
        idx = np.asarray(submesh.faces)
        return pos[idx], uv[idx]

    def test_mesh_roundtrip(self):
        src_path = self._source_mesh()
        scene = ogre_io.read_ogre_mesh(src_path)
        self.assertEqual([n.name for n in scene.nodes], ["Hull", "Glass"])
        hull = scene.nodes[0].mesh
        self.assertEqual(len(hull.faces), 2)
        self.assertEqual(set(hull.face_materials), {"bz98/hull"})
        # Blender (x, y, z) -> Ogre (x, z, -y) is what the toolkit exporter wrote; Maya == Ogre.
        np.testing.assert_allclose(hull.positions[3], (0.0, 1.0, -1.0), atol=TOL)
        np.testing.assert_allclose(hull.normals[0], (0.0, 1.0, 0.0), atol=TOL)
        # The file stores V = 1 - v_blender; Maya (like Blender) has V up, so it flips back.
        np.testing.assert_allclose(hull.uv_sets[0][2], (1.0, 1.0), atol=TOL)
        np.testing.assert_allclose(hull.uv_sets[0][1], (1.0, 0.0), atol=TOL)

        out_path = os.path.join(self.tmp, "out.mesh")
        ogre_io.write_ogre_mesh(scene, out_path)

        before, after = self._load(src_path), self._load(out_path)
        src_subs, dst_subs = before.get_submeshes(), after.get_submeshes()
        self.assertEqual(len(dst_subs), len(src_subs))
        for a, b in zip(src_subs, dst_subs):
            self.assertEqual(b.material, a.material)
            self.assertEqual(b.submesh_name, a.submesh_name)
            self.assertEqual(b.geometry.vertex_count, a.geometry.vertex_count)
            self.assertEqual(len(b.faces), len(a.faces))
            pa, ua = self._corners(a)
            pb, ub = self._corners(b)
            np.testing.assert_allclose(pb, pa, atol=TOL)
            np.testing.assert_allclose(ub, ua, atol=TOL)

    def test_normals_stay_aligned_with_winding_after_roundtrip(self):
        src_path = self._source_mesh()
        out_path = os.path.join(self.tmp, "orient.mesh")
        ogre_io.write_ogre_mesh(ogre_io.read_ogre_mesh(src_path), out_path)
        mirrored = os.path.join(self.tmp, "orient_mirror.mesh")
        scene = ogre_io.read_ogre_mesh(src_path)
        flip = axes.identity4()
        flip[0][0] = -1.0
        for node in scene.nodes:
            node.matrix = flip
        ogre_io.write_ogre_mesh(scene, mirrored)
        for path in (src_path, out_path, mirrored):
            for sub in self._load(path).get_submeshes():
                geometry = sub._wire_geometry
                pos = import_compat._decode_semantic(geometry, {"POSITION"})[:, :3]
                nrm = import_compat._decode_semantic(geometry, {"NORMAL"})[:, :3]
                for tri in np.asarray(sub.faces).reshape(-1, 3):
                    a, b, c = pos[tri]
                    winding = np.cross(b - a, c - a)
                    for vertex in tri:
                        self.assertGreater(float(np.dot(winding, nrm[vertex])), 0.0, path)

    def test_write_bakes_hierarchy_and_keeps_two_uv_sets_and_materials(self):
        quad = Mesh(
            positions=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            faces=[(0, 1, 2), (0, 2, 3)],
            face_materials=["mat_a", "mat_b"],
            normals=[(0, 0, 1)] * 6,
            uv_sets=[
                [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)],
                [(0.5, 0.5)] * 6,
            ],
        )
        scene = Scene(nodes=[
            Node("root", None, _identity_translated(10, 0, 0), KIND_EMPTY, None),
            Node("piece", "root", _identity_translated(0, 5, 0), KIND_PART, quad),
        ])
        out_path = os.path.join(self.tmp, "baked.mesh")
        ogre_io.write_ogre_mesh(scene, out_path)
        loaded = self._load(out_path)
        subs = loaded.get_submeshes()
        self.assertEqual([s.material for s in subs], ["mat_a", "mat_b"])  # split per material
        self.assertEqual([s.submesh_name for s in subs], ["piece_mat_a", "piece_mat_b"])
        positions = import_compat._decode_semantic(subs[0]._wire_geometry, {"POSITION"})
        np.testing.assert_allclose(positions.min(axis=0), (10.0, 5.0, 0.0), atol=TOL)
        uv_elements = import_compat._elements_by_semantic(subs[0]._wire_geometry, {"TEXCOORD"})
        self.assertEqual(sorted(e.index for e in uv_elements), [0, 1])

        again = ogre_io.read_ogre_mesh(out_path)
        self.assertEqual(len(again.nodes), 2)
        self.assertEqual(len(again.nodes[0].mesh.uv_sets), 2)

    def test_mirrored_parent_flips_winding(self):
        tri = Mesh(positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], faces=[(0, 1, 2)], face_materials=["m"])
        mirror = axes.identity4()
        mirror[0][0] = -1.0
        scene = Scene(nodes=[Node("t", None, mirror, KIND_PART, tri)])
        out_path = os.path.join(self.tmp, "mirror.mesh")
        ogre_io.write_ogre_mesh(scene, out_path)
        sub = self._load(out_path).get_submeshes()[0]
        pos = import_compat._decode_semantic(sub._wire_geometry, {"POSITION"})[:, :3][np.asarray(sub.faces)]
        normal = np.cross(pos[1] - pos[0], pos[2] - pos[0])
        self.assertGreater(float(normal[2]), 0.0)  # still facing +Z after the mirror


# --- tier 2: Redux export pipeline ---------------------------------------------------------

def _load_blender_ogre_export():
    """Blender's own OgreExport module (it imports bpy only for the code we do not call)."""
    import importlib.util
    import types

    stubs = {}
    for name in ("bpy", "mathutils"):
        if name not in sys.modules:
            stubs[name] = types.ModuleType(name)
            sys.modules[name] = stubs[name]
    if "mathutils" in stubs:
        for cls in ("Vector", "Matrix", "Quaternion"):
            setattr(stubs["mathutils"], cls, type(cls, (), {}))
    try:
        path = os.path.join(_REPO_ROOT, "bz98tools", "ogretools", "OgreExport.py")
        spec = importlib.util.spec_from_file_location("bz98_ogre_export_for_tests", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name in stubs:
            sys.modules.pop(name, None)


def _reference_mesh_bytes(submeshes, tangent_dimensions=None):
    """Blender's pure static export, fed by hand-built Blender-axes arrays (no module under test)."""
    from bz98tools.ogrefast.pure import kenshi_compat, pose_compat

    built = []
    for index, spec in enumerate(submeshes):
        sub = pose_compat.SubMeshData()
        sub.index = index
        sub.submesh_name = spec["name"]
        sub.material = spec["material"]
        loops = len(spec["loops"])
        out = sub.set_vertex(
            nd_vert_indices=np.asarray(spec["loops"], dtype=np.int32),
            nd_loop_indices=np.arange(loops, dtype=np.int32),
            nd_positions=np.asarray(spec["positions"], dtype=np.float32),
            nd_normals=np.asarray(spec["normals"], dtype=np.float32),
            nd_tangents=np.asarray(spec.get("tangents", np.empty(3)), dtype=np.float32),
            nd_bitangent_signs=np.asarray(spec.get("signs", np.empty(1)), dtype=np.float32),
            nd_bitangents=np.asarray(spec.get("bitangents", np.empty(3)), dtype=np.float32),
            nd_texcoords=np.asarray(spec["uvs"], dtype=np.float32),
            nd_colors=np.empty(4, dtype=np.float32),
            nd_alphas=np.empty(4, dtype=np.float32),
            tangent_dimensions=spec.get("tangent_dimensions", 3),
            optimize=True,
        )
        sub.set_bone_assignments([], out)
        built.append(sub)
    serializer = kenshi_compat.KenshiObjectSerializer()
    mesh = serializer.create_mesh("ref.mesh")
    mesh.set_submeshes(built)
    path = os.path.join(tempfile.mkdtemp(prefix="bzmaya_ref_"), "ref.mesh")
    serializer.save_mesh(mesh, path, kenshi_compat.MeshVersion.V_1_10)
    with open(path, "rb") as handle:
        return handle.read()


def _decode_mesh(path):
    """(per-submesh dict of declaration, vertex count, index list, decoded semantics)."""
    importer = import_compat.KenshiObjectSerializer()
    importer.add_resource_location(os.path.dirname(path))
    out = []
    for sub in importer.load_mesh(os.path.basename(path)).get_submeshes():
        geometry = sub._wire_geometry
        decl = [(e.semantic, e.index, e.offset, e.source, e.component_type) for e in geometry.declaration]
        values = {}
        for element in geometry.declaration:
            key = (element.semantic, element.index)
            values[key] = import_compat._decode_element(geometry, element)
        out.append({
            "name": sub.submesh_name, "material": sub.material, "decl": decl,
            "count": geometry.vertex_count, "faces": list(sub.faces), "values": values,
            "strides": [(b.bind_index, b.vertex_size) for b in geometry.buffers],
        })
    return out


def _box_with_two_uv_sets():
    mesh = vdf_io.box_mesh((0, 0, 0), (1, 2, 3))
    loops = mesh.loop_count()
    mesh.face_materials = ["mat_a", "mat_a", "mat_a", "mat_b", "mat_b", "mat_b"]
    mesh.normals = vdf_io.with_normals(mesh)
    mesh.uv_sets = [
        [(0.1 * (i % 4), 0.2 * (i % 3)) for i in range(loops)],
        [(0.5, 0.25 + 0.01 * i) for i in range(loops)],
    ]
    return mesh


class ExportOptionsTests(unittest.TestCase):
    def test_options_come_from_blenders_operators(self):
        from bz98maya.core import export_options as eo

        vdf = {o.name: o for o in eo.option_fields("VDF")}
        self.assertEqual(vdf["ogre_suffix"].default, "_port")
        self.assertEqual(vdf["ogre_normal_mode"].default, "CORRECT")
        self.assertEqual([i[0] for i in vdf["ogre_normal_mode"].items], ["CORRECT", "NONE", "FLIP"])
        self.assertEqual([i[0] for i in vdf["face_plane_mode"].items], ["CURRENT", "PRESERVE", "RECOMPUTE", "DX_FIX"])
        self.assertTrue(vdf["ogre_headlights"].vdf_only)
        self.assertIn("Porter --headlights", vdf["ogre_headlights"].tip)
        self.assertFalse(any(o.vdf_only for o in eo.option_fields("SDF")))
        self.assertIn("ExportSDFOnly", {o.name for o in eo.option_fields("SDF")})
        mesh = eo.default_options("MESH")
        self.assertEqual(
            {k: mesh[k] for k in ("export_tangents", "export_binormals", "tangent_parity", "apply_transform",
                                  "export_materials", "overwrite_material", "copy_textures", "batch_export")},
            dict(export_tangents=True, export_binormals=True, tangent_parity=True, apply_transform=True,
                 export_materials=True, overwrite_material=False, copy_textures=False, batch_export=False),
        )
        self.assertNotIn("xml_converter", mesh)  # no OgreXMLConverter in the pure path

    def test_modes_and_porter_options_match_the_operators(self):
        from bz98maya.core import export_options as eo

        options = eo.default_options("VDF")
        self.assertEqual(eo.export_mode("VDF", options), eo.MODE_LEGACY)
        options["auto_port_ogre"] = True
        self.assertEqual(eo.export_mode("VDF", options), eo.MODE_LEGACY_REDUX)
        self.assertEqual(eo.export_mode("MESH", {}), eo.MODE_REDUX)
        porter = eo.autoport_options(dict(options, ogre_name=" tank ", ogre_headlights=True, ogre_person_mode="YES"))
        self.assertEqual((porter["name"], porter["suffix"], porter["headlights"], porter["person_mode"]),
                         ("tank", "_port", True, "YES"))
        self.assertEqual(porter["dest_dir"], None)
        self.assertEqual(len(porter["scope_transform"]), 12)
        sdf = eo.autoport_options(eo.default_options("SDF"))
        self.assertNotIn("headlights", sdf)  # VDF-only extras are not passed for SDF / GEO

    def test_presets_apply_save_and_delete_like_blender(self):
        from bz98maya.core import export_options as eo
        from bz98maya.core.export_session import ExportSession

        directory = tempfile.mkdtemp(prefix="bzmaya_presets_")
        self.addCleanup(shutil.rmtree, directory, True)
        keys = [k for k, _l, _v in eo.builtin_presets("VDF")]
        self.assertTrue(keys and all(k for k in keys))
        session = ExportSession("VDF", directory)
        self.assertIn(eo.MODE_LEGACY, session.mode)
        port_key = next(k for k, _l, v in eo.builtin_presets("VDF") if v.get("auto_port_ogre"))
        session.apply_builtin(port_key)
        self.assertEqual(session.mode, eo.MODE_LEGACY_REDUX)
        session.set_option("ogre_suffix", "_mine")
        session.set_option("ogre_bounds_mult", (2.0, 2.0, 2.0))
        path = session.save_preset("My Tank!")
        self.assertTrue(path.endswith("My_Tank.json"))
        self.assertEqual(session.custom_presets(), ["My Tank!"])
        fresh = ExportSession("VDF", directory)
        fresh.apply_custom("My Tank!")
        self.assertEqual((fresh.options["ogre_suffix"], fresh.options["ogre_bounds_mult"], fresh.options["auto_port_ogre"]),
                         ("_mine", [2.0, 2.0, 2.0], True))
        self.assertTrue(fresh.delete_preset("My Tank!"))
        self.assertEqual(fresh.custom_presets(), [])
        with self.assertRaises(ValueError):
            fresh.save_preset("  ")
        self.assertFalse(fresh.delete_preset("nope"))

    def test_session_switches_between_the_three_modes(self):
        from bz98maya.core import export_options as eo
        from bz98maya.core.export_session import ExportSession

        calls = []
        session = ExportSession("SDF", runner=lambda kind, path, options: calls.append((kind, path, dict(options))))
        session.set_mode(eo.MODE_LEGACY_REDUX)
        self.assertEqual((session.kind, session.options["auto_port_ogre"]), ("SDF", True))
        session.set_option("ogre_name", "keep")
        session.set_mode(eo.MODE_REDUX)
        self.assertEqual((session.kind, session.extension()), ("MESH", ".mesh"))
        session.set_mode(eo.MODE_LEGACY)
        self.assertEqual((session.kind, session.options["auto_port_ogre"], session.options["ogre_name"]), ("SDF", False, "keep"))
        session.export("x.sdf")
        self.assertEqual(calls[0][:2], ("SDF", "x.sdf"))
        with self.assertRaises(KeyError):
            session.set_option("not_an_option", 1)


class MaterialFileTests(unittest.TestCase):
    MATERIALS = {
        "hull_trim": {"ambient": [1.0, 0.5, 0.25], "diffuse": [0.8, 0.8, 0.8], "specular": [0.8, 0.8, 0.8],
                      "emissive": [0.0, 0.0, 0.0], "texture": "hull_trim_d.png", "texture_path": "x/hull_trim_d.png"},
        "cockpit_glass": {"ambient": [0.1234567, 0.2, 0.3], "diffuse": [0.1234567, 0.2, 0.3],
                          "specular": [0.0, 0.0, 0.0], "emissive": [0.0, 0.0, 0.0]},
        "wing2": {"ambient": [1, 1, 1], "diffuse": [1, 1, 1], "specular": [0, 0, 0], "emissive": [0, 0, 0],
                  "texture": "metal.tga"},
        "bz98/hull": {"ambient": [0.5, 0.5, 0.5], "diffuse": [0.5, 0.5, 0.5], "specular": [0.5, 0.5, 0.5],
                      "emissive": [0, 0, 0], "texture": "Plain_Diffuse.dds"},
    }

    def test_material_file_is_identical_to_blenders_writer(self):
        from bz98maya.core import material

        tmp = tempfile.mkdtemp(prefix="bzmaya_mat_")
        self.addCleanup(shutil.rmtree, tmp, True)
        blender_dir, maya_dir = os.path.join(tmp, "b"), os.path.join(tmp, "m")
        os.makedirs(blender_dir)
        os.makedirs(maya_dir)
        blender = _load_blender_ogre_export()
        blender.xSaveMaterialData(os.path.join(blender_dir, "a.mesh"), {"materials": self.MATERIALS}, True, False)
        # the pure writer is Blender's text; the file writer leaves out derived textures that are not there
        with open(os.path.join(blender_dir, "a.material"), encoding="utf-8") as a:
            self.assertEqual(material.material_text(self.MATERIALS), a.read())
        text = material.material_text(self.MATERIALS)
        self.assertIn("material cockpit_glass : BZBaseCockpit", text)
        self.assertIn("material wing2 : BZBaseCockpit", text)  # name ends in 2
        self.assertIn("set_texture_alias DiffuseMap hull_trim_d.dds", text)  # _d suffix stripped, not doubled
        self.assertIn("set_texture_alias NormalMap Plain_n.dds", text)
        self.assertIn('set $diffuse "0.123 0.2 0.3"', text)

    def test_existing_material_is_kept_unless_overwrite_and_textures_are_copied(self):
        from bz98maya.core import material

        tmp = tempfile.mkdtemp(prefix="bzmaya_mat_")
        self.addCleanup(shutil.rmtree, tmp, True)
        texture = os.path.join(tmp, "tex.png")
        with open(texture, "wb") as handle:
            handle.write(b"png")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir)
        mesh = os.path.join(out_dir, "m.mesh")
        mats = {"a": {"texture": "tex.png", "texture_path": texture}}
        with open(os.path.join(out_dir, "m.material"), "w") as handle:
            handle.write("keep me")
        material.write_material_file(mesh, mats, overwrite=False, copy_textures=True)
        with open(os.path.join(out_dir, "m.material")) as handle:
            self.assertEqual(handle.read(), "keep me")
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "tex.png")))
        material.write_material_file(mesh, mats, overwrite=True)
        with open(os.path.join(out_dir, "m.material")) as handle:
            self.assertIn("material a : BZBase", handle.read())
        self.assertIsNone(material.write_material_file(mesh, {}, True))

    def test_material_file_round_trips_through_the_reader(self):
        from bz98maya.core import material

        tmp = tempfile.mkdtemp(prefix="bzmaya_mat_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "a.material")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(material.material_text(self.MATERIALS))
        parsed = material.parse_material_file(path)
        self.assertEqual(set(parsed), set(self.MATERIALS))
        self.assertEqual(parsed["hull_trim"]["textures"]["DiffuseMap"], "hull_trim_d.dds")
        self.assertEqual(parsed["hull_trim"]["template"], "BZBase")
        np.testing.assert_allclose(parsed["hull_trim"]["diffuse"], [0.8, 0.8, 0.8])
        plain = os.path.join(tmp, "plain.material")
        with open(plain, "w") as handle:
            handle.write("material simple\n{\n technique\n {\n pass\n {\n texture_unit\n {\n texture wall.png\n }\n }\n }\n}\n")
        self.assertEqual(material.parse_material_file(plain)["simple"]["textures"]["DiffuseMap"], "wall.png")
        open(os.path.join(tmp, "WALL.DDS"), "wb").close()
        self.assertTrue(material.find_texture_file("wall.png", tmp).lower().endswith("wall.dds"))


class ReduxMeshGoldenTests(unittest.TestCase):
    """The Maya-free Redux writer against Blender's pipeline fed by hand-built reference arrays."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_mesh_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, scene, options=None):
        from bz98maya.core import redux

        path = os.path.join(self.tmp, "out.mesh")
        redux.write_bzr_mesh(scene, path, dict({"export_materials": False}, **(options or {})))
        with open(path, "rb") as handle:
            return handle.read(), path

    def test_tangent_format_mapping_is_blenders(self):
        import itertools

        from bz98maya.core import redux
        from bz98tools.ogrefast import backend

        for combo in itertools.product((True, False), repeat=4):
            self.assertEqual(redux.map_tangent_format(*combo), backend._map_tangent_format(*combo), combo)

    def test_plane_with_tangents_is_byte_identical_to_the_blender_path(self):
        # Maya XZ plane, normal +Y, U along +X, V along +Z.
        pts = [(0, 0, 0), (2, 0, 0), (2, 0, 1), (0, 0, 1)]
        mesh = Mesh(
            positions=pts, faces=[(0, 1, 2, 3)], face_materials=["plane_mat"],
            normals=[(0, 1, 0)] * 4, uv_sets=[[(0, 0), (2, 0), (2, 1), (0, 1)]],
        )
        scene = Scene(nodes=[Node("plane", None, axes.identity4(), KIND_PART, mesh)])
        blender_pts = [(x, -z, y) for x, y, z in pts]
        loops = [0, 1, 2, 0, 2, 3]
        # tangent = +U = Blender +X; bitangent = +V = Blender -Y; cross(n, t) = +Y -> sign -1.
        # Blender's ALL format multiplies the (signed) bitangent by the sign again.
        reference = _reference_mesh_bytes([dict(
            name="plane", material="plane_mat", positions=blender_pts, loops=loops,
            normals=[(0, 0, 1)] * 6, uvs=[mesh.uv_sets[0][i] for i in loops],
            tangents=[(1, 0, 0)] * 6, signs=[-1.0] * 6, bitangents=[(0, 1, 0)] * 6, tangent_dimensions=4,
        )])
        data, path = self._write(scene)
        self.assertEqual(data, reference)
        decoded = _decode_mesh(path)[0]
        # BZR's vertex organisation (from the serializer) lists the binormal before the tangent
        self.assertEqual([d[0] for d in decoded["decl"]], ["POSITION", "NORMAL", "TEXCOORD", "BINORMAL", "TANGENT"])
        np.testing.assert_allclose(decoded["values"][("TANGENT", 0)], [[1, 0, 0, -1]] * decoded["count"], atol=1e-6)

    def test_tangent_options_change_the_layout_like_blender(self):
        pts = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]
        mesh = Mesh(positions=pts, faces=[(0, 1, 2, 3)], face_materials=["m"], normals=[(0, 1, 0)] * 4,
                    uv_sets=[[(0, 0), (1, 0), (1, 1), (0, 1)]])
        scene = Scene(nodes=[Node("plane", None, axes.identity4(), KIND_PART, mesh)])
        layouts = {}
        for label, options in {
            "none": {"export_tangents": False},
            "t4": {"export_binormals": False},
            "t3": {"tangent_parity": False},
            "all": {},
            "zero": {"zero_tangents_binormals": True},
        }.items():
            data, path = self._write(scene, options)
            decoded = _decode_mesh(path)[0]
            layouts[label] = ([(d[0], d[4]) for d in decoded["decl"]], decoded)
        self.assertEqual([s for s, _t in layouts["none"][0]], ["POSITION", "NORMAL", "TEXCOORD"])
        self.assertIn(("TANGENT", "FLOAT4"), layouts["t4"][0])
        self.assertNotIn("BINORMAL", [s for s, _t in layouts["t4"][0]])
        self.assertIn(("TANGENT", "FLOAT3"), layouts["t3"][0])
        self.assertIn("BINORMAL", [s for s, _t in layouts["t3"][0]])
        self.assertIn(("TANGENT", "FLOAT4"), layouts["all"][0])
        self.assertIn("BINORMAL", [s for s, _t in layouts["all"][0]])
        np.testing.assert_array_equal(layouts["zero"][1]["values"][("TANGENT", 0)], 0.0)

    def test_two_uv_sets_materials_and_parent_transform_match_the_blender_path(self):
        mesh = _box_with_two_uv_sets()
        scene = Scene(nodes=[
            Node("root", None, _identity_translated(5, 0, 0), KIND_EMPTY, None),
            Node("box", "root", _identity_translated(0, 1, 0), KIND_PART, mesh),
        ])
        # independent reference: world = translate(5, 1, 0); fan triangles per face; split per material
        T = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)
        world_pts = [(x + 5, y + 1, z) for x, y, z in mesh.positions]
        blender_pts = [tuple(T @ np.array(p)) for p in world_pts]
        specs = []
        loop = 0
        by_material = {}
        for face_index, face in enumerate(mesh.faces):
            for k in (1, 2):
                tri = [0, k, k + 1]
                by_material.setdefault(mesh.face_materials[face_index], []).append(
                    [(face[c], loop + c) for c in tri]
                )
            loop += 4
        for name in ("mat_a", "mat_b"):
            loops, normals, uv0, uv1 = [], [], [], []
            for tri in by_material[name]:
                for vertex, loop_index in tri:
                    loops.append(vertex)
                    normals.append(tuple(T @ np.array(mesh.normals[loop_index])))
                    uv0.append(mesh.uv_sets[0][loop_index])
                    uv1.append(mesh.uv_sets[1][loop_index])
            specs.append(dict(name="box_" + name, material=name, positions=blender_pts, loops=loops,
                              normals=normals, uvs=[uv0, uv1]))
        reference = _reference_mesh_bytes(specs)
        data, path = self._write(scene, {"export_tangents": False})
        self.assertEqual(data, reference)
        decoded = _decode_mesh(path)
        self.assertEqual([d["material"] for d in decoded], ["mat_a", "mat_b"])
        self.assertEqual([d["name"] for d in decoded], ["box_mat_a", "box_mat_b"])
        self.assertEqual(sorted(k for k in decoded[0]["values"] if k[0] == "TEXCOORD"), [("TEXCOORD", 0), ("TEXCOORD", 1)])

    def test_apply_transform_off_keeps_object_space_and_batch_writes_one_file_per_object(self):
        from bz98maya.core import redux

        mesh = vdf_io.box_mesh((0, 0, 0), (1, 1, 1))
        mesh.face_materials = ["m"] * 6
        scene = Scene(nodes=[
            Node("a", None, _identity_translated(10, 0, 0), KIND_PART, mesh),
            Node("b", None, _identity_translated(0, 7, 0), KIND_PART, mesh),
        ])
        data, path = self._write(scene, {"apply_transform": False, "export_tangents": False})
        position = _decode_mesh(path)[0]["values"][("POSITION", 0)]
        self.assertEqual(float(position[:, 0].max()), 1.0)  # not moved by the +10 translation
        out = os.path.join(self.tmp, "batch", "ignored.mesh")
        os.makedirs(os.path.dirname(out))
        redux.write_bzr_mesh(scene, out, {"batch_export": True, "export_materials": True})
        self.assertEqual(sorted(os.listdir(os.path.dirname(out))), ["a.material", "a.mesh", "b.material", "b.mesh"])
        moved = _decode_mesh(os.path.join(os.path.dirname(out), "a.mesh"))[0]["values"][("POSITION", 0)]
        self.assertEqual(float(moved[:, 0].min()), 10.0)

    def test_materials_file_follows_export_materials_and_overwrite(self):
        from bz98maya.core import redux

        mesh = vdf_io.box_mesh((0, 0, 0), (1, 1, 1))
        mesh.face_materials = ["skin"] * 6
        scene = Scene(
            nodes=[Node("a", None, axes.identity4(), KIND_PART, mesh)],
            materials={"skin": {"ambient": [0.25] * 3, "diffuse": [0.25] * 3, "specular": [0.25] * 3,
                                "emissive": [0] * 3, "texture": "skin_d.png"}},
        )
        path = os.path.join(self.tmp, "m.mesh")
        redux.write_bzr_mesh(scene, path, {"export_materials": False})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "m.material")))
        redux.write_bzr_mesh(scene, path, {})
        with open(os.path.join(self.tmp, "m.material"), encoding="utf-8") as handle:
            self.assertIn('set $diffuse "0.25 0.25 0.25"', handle.read())
        scene.materials["skin"]["diffuse"] = [0.5] * 3
        redux.write_bzr_mesh(scene, path, {})  # not overwritten by default
        with open(os.path.join(self.tmp, "m.material"), encoding="utf-8") as handle:
            self.assertIn('"0.25 0.25 0.25"', handle.read())
        redux.write_bzr_mesh(scene, path, {"overwrite_material": True})
        with open(os.path.join(self.tmp, "m.material"), encoding="utf-8") as handle:
            self.assertIn('"0.5 0.5 0.5"', handle.read())

    def test_stock_meshes_match_the_blender_import_export_path(self):
        stock = os.environ.get("BZ98_STOCK_DIR")
        folder = os.path.join(stock, "mesh") if stock else ""
        files = sorted(f for f in os.listdir(folder) if f.endswith(".mesh")) if os.path.isdir(folder) else []
        if not files:
            self.skipTest("BZ98_STOCK_DIR/mesh is not set")
        for name in files:
            with self.subTest(mesh=name):
                source = os.path.join(folder, name)
                # Blender: import accessors -> set_vertex -> serializer
                importer = import_compat.KenshiObjectSerializer()
                importer.add_resource_location(folder)
                specs = []
                for sub in importer.load_mesh(name).get_submeshes():
                    loops = list(sub.faces)
                    uvs = np.asarray(sub.get_texcoords(), dtype=np.float32)
                    specs.append(dict(
                        name=sub.submesh_name, material=sub.material,
                        positions=np.asarray(sub.get_positions(), dtype=np.float32).reshape(-1, 3),
                        loops=loops, normals=np.asarray(sub.get_normals(), dtype=np.float32).reshape(-1, 3),
                        uvs=uvs.reshape(len(uvs), len(loops), 2) if uvs.ndim == 2 and uvs.size else np.empty(2),
                    ))
                reference = _reference_mesh_bytes(specs)
                # Maya path (Maya-free half): neutral scene -> redux writer
                scene = ogre_io.read_ogre_mesh(source)
                data, path = self._write(scene, {"export_tangents": False})
                ref_path = os.path.join(self.tmp, "reference.mesh")
                with open(ref_path, "wb") as handle:
                    handle.write(reference)
                got, want = _decode_mesh(path), _decode_mesh(ref_path)
                self.assertEqual(len(got), len(want))
                for a, b in zip(got, want):
                    self.assertEqual((a["name"], a["material"], a["decl"], a["count"], a["strides"]),
                                     (b["name"], b["material"], b["decl"], b["count"], b["strides"]))
                    self.assertEqual(a["faces"], b["faces"])
                    for key in b["values"]:
                        np.testing.assert_allclose(a["values"][key], b["values"][key], atol=1e-5, err_msg=str(key))
                # and against the original stock file: same geometry, whatever its layout
                original = _decode_mesh(source)
                for a, o in zip(got, original):
                    np.testing.assert_allclose(
                        a["values"][("POSITION", 0)][a["faces"]], o["values"][("POSITION", 0)][o["faces"]], atol=1e-5
                    )


class AutoPortTests(unittest.TestCase):
    def _vehicle(self, folder, textured=False):
        cube = vdf_io.box_mesh((-1, 0, -1), (1, 1, 1))
        cube.face_materials = ["avtrim"] * 6
        scene = Scene(nodes=[
            Node("tst11bod", None, axes.identity4(), KIND_PART, cube, {"type": 60}),
            Node("tst11pov", None, axes.identity4(), KIND_HARDPOINT, None, {"type": 40}),
            Node("tst11gc1", "tst11bod", axes.identity4(), KIND_HARDPOINT, None, {"type": 71}),
        ])
        path = os.path.join(folder, "tst.vdf")
        vdf_io.write_vdf(scene, path)
        return path

    def test_legacy_plus_redux_runs_the_toolkits_porter_on_the_exported_files(self):
        from bz98maya.core import export_options as eo, redux

        tmp = tempfile.mkdtemp(prefix="bzmaya_port_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = self._vehicle(tmp)
        options = dict(eo.default_options("VDF"), auto_port_ogre=True, ogre_dest_dir=os.path.join(tmp, "redux"))
        self.assertEqual(redux.auto_port(path, options), {"FINISHED"})
        produced = sorted(os.listdir(os.path.join(tmp, "redux")))
        self.assertIn("tst.mesh", produced)
        self.assertIn("tst.skeleton", produced)
        self.assertTrue(any(f.endswith(".material") for f in produced), produced)
        from bz98tools.bzrmodelporter.ogremesh_serializer import MeshSerializer

        with open(os.path.join(tmp, "redux", "tst.mesh"), "rb") as handle:
            ported = MeshSerializer(handle).read()  # the porter's mesh is skinned, so read it directly
        self.assertGreaterEqual(len(ported.submesh_list), 1)
        self.assertEqual(ported.skeleton_name, "tst.skeleton")
        # the legacy files stay where they were written
        self.assertTrue(os.path.isfile(os.path.join(tmp, "tst11bod.geo")))
        # --onlyonce: a second run is skipped, as in Blender
        self.assertEqual(redux.auto_port(path, dict(options, ogre_only_once=True)), {"CANCELLED"})
        # --nowrite: nothing new is written
        dry = os.path.join(tmp, "dry")
        redux.auto_port(path, dict(options, ogre_dest_dir=dry, ogre_nowrite=True))
        self.assertEqual(os.listdir(dry) if os.path.isdir(dry) else [], [])
        self.assertEqual(redux.auto_port(os.path.join(tmp, "x.txt"), options), {"CANCELLED"})


class MapTextureTests(unittest.TestCase):
    def _indexed_map(self, folder, name, width=4, height=2):
        # BZ .map: <row_bytes, pixel_format, height, unknown> then palette indices (fmt 0 = indexed)
        with open(os.path.join(folder, name + ".map"), "wb") as handle:
            handle.write(struct.pack("<4H", width, 0, height, 0))
            handle.write(bytes(range(width * height)))

    def test_map_files_are_found_by_blenders_rules_and_converted_to_png(self):
        from bz98maya.core import mapload

        tmp = tempfile.mkdtemp(prefix="bzmaya_map_")
        self.addCleanup(shutil.rmtree, tmp, True)
        base, extra = os.path.join(tmp, "base"), os.path.join(tmp, "extra", "deep")
        os.makedirs(base)
        os.makedirs(extra)
        self._indexed_map(extra, "AVHULL")
        self.assertIsNone(mapload.resolve_map_file("avhull", base))
        found = mapload.resolve_map_file("avhull", base, os.path.join(tmp, "extra"))  # case-insensitive, subfolders
        self.assertTrue(found.lower().endswith("avhull.map"))
        png = mapload.texture_for_map_name("avhull", base, os.path.join(tmp, "extra"))
        self.assertTrue(png.lower().endswith(".png"))
        with open(png, "rb") as handle:
            data = handle.read()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (4, 2))
        self.assertIsNone(mapload.texture_for_map_name("missing", base))

    def test_vdf_import_attaches_map_textures_unless_disabled(self):
        tmp = tempfile.mkdtemp(prefix="bzmaya_map_")
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = os.path.join(tmp, "src")
        os.makedirs(folder)
        shutil.copy(os.path.join(FIXTURES, "eyepoint.vdf"), folder)
        _write_cube_geo(os.path.join(folder, "fxh11.geo"), "fxh11")  # faces use tex_a / tex_b
        self._indexed_map(folder, "tex_a")
        path = os.path.join(folder, "eyepoint.vdf")
        scene = vdf_io.read_vdf(path)
        self.assertEqual({k for k, v in scene.materials.items() if v.get("texture_path")}, {"tex_a"})
        self.assertTrue(scene.materials["tex_a"]["texture_path"].endswith("tex_a.png"))
        self.assertEqual(scene.materials["tex_a"]["texture"], "tex_a.map")
        off = vdf_io.read_vdf(path, {"ImportMapTextures": False})
        self.assertFalse(any(v.get("texture_path") for v in off.materials.values()))
        other = os.path.join(tmp, "textures")
        os.makedirs(other)
        self._indexed_map(other, "tex_b")
        with_dir = vdf_io.read_vdf(path, {"MapTextureDirectory": other})
        self.assertEqual(set(with_dir.materials), {"tex_a", "tex_b"})


class FacePlaneModeTests(unittest.TestCase):
    def test_blenders_face_plane_modes(self):
        # A quad at Maya z = 2 (BZ z = -2) facing +Z.
        pts = [(0, 0, 2), (2, 0, 2), (2, 2, 2), (0, 2, 2)]
        bz = [axes.maya_point_to_bz(p) for p in pts]
        normal, d = vdf_io.face_plane("CURRENT", pts, bz)
        # Blender: face centre in Blender axes (x, -z, y) and d = 1.0
        np.testing.assert_allclose(normal, (1.0, -2.0, 1.0))
        self.assertEqual(d, 1.0)
        self.assertEqual(vdf_io.face_plane("PRESERVE", pts, bz), vdf_io.face_plane("CURRENT", pts, bz))
        for mode in ("RECOMPUTE", "DX_FIX"):
            normal, d = vdf_io.face_plane(mode, pts, bz)
            np.testing.assert_allclose(normal, (0, 0, 1), atol=1e-9)  # cross of the first three GEO-space points
            self.assertAlmostEqual(d, -2.0)  # Blender writes +n . p0
        normal, d = vdf_io.face_plane("STOCK", pts, bz)
        self.assertAlmostEqual(d, 2.0)  # stock files: -n . p0
        mesh = Mesh(positions=pts, faces=[(0, 1, 2, 3)], face_materials=["t"])
        geo = vdf_io.mesh_to_geo("pl", mesh, None, "CURRENT")
        self.assertEqual(geo.face_list[0].plane.distance, 1.0)
        out = tempfile.mkdtemp(prefix="bzmaya_geo_")
        self.addCleanup(shutil.rmtree, out, True)
        scene = Scene(nodes=[Node("tst11bdy", None, axes.identity4(), KIND_PART, vdf_io.box_mesh((0, 0, 0), (1, 1, 1)),
                                  {"type": 60})])
        vdf_io.write_vdf(scene, os.path.join(out, "a.vdf"), write_geos=False)
        self.assertFalse(os.path.exists(os.path.join(out, "tst11bdy.geo")))  # "Skip GEO File Export"
        vdf_io.write_vdf(scene, os.path.join(out, "b.vdf"), True, "RECOMPUTE")
        self.assertTrue(os.path.isfile(os.path.join(out, "tst11bdy.geo")))


class ToolkitDiscoveryTests(unittest.TestCase):
    def test_finds_repo_toolkit_without_importing_bpy(self):
        from bz98maya.core import toolkit

        found = toolkit.find_toolkit_dir()
        self.assertEqual(os.path.basename(found), "bz98tools")
        self.assertNotIn("bpy", sys.modules)

    def test_env_override_is_honoured(self):
        from bz98maya.core import toolkit

        old = os.environ.get(toolkit.ENV_VAR)
        os.environ[toolkit.ENV_VAR] = os.path.join(_REPO_ROOT)
        try:
            self.assertEqual(
                toolkit.find_toolkit_dir(), os.path.join(_REPO_ROOT, "bz98tools")
            )
        finally:
            if old is None:
                del os.environ[toolkit.ENV_VAR]
            else:
                os.environ[toolkit.ENV_VAR] = old


def _vehicle_scene_with_anim():
    cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
    cube.face_materials = ["tex_a"] * 6
    body = Node("abc11bdy", None, axes.identity4(), KIND_PART, cube, {"type": 60})
    turret = Node("abc11ty1", "abc11bdy", _identity_translated(0.0, 1.5, 0.0), KIND_PART, cube, {"type": 69})
    s = math.sqrt(0.5)
    turret.anim = {
        "rot": [(0, (1.0, 0.0, 0.0, 0.0)), (10, (s, 0.0, s, 0.0)), (20, (1.0, 0.0, 0.0, 0.0))],
        "pos": [(0, (0.0, 1.5, 0.0)), (10, (0.5, 1.5, 0.0)), (20, (0.0, 1.5, 0.0))],
    }
    elements = [{"index": 0, "start": 0, "length": 20, "loop": 1, "speed": 15.0, "mask": []}]
    return Scene(nodes=[body, turret], meta={"animations": elements})


class AnimationCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_anim_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _check_keys(self, back):
        node = {n.name: n for n in back.nodes}["abc11ty1"]
        self.assertIsNotNone(node.anim)
        self.assertEqual([f for f, _q in node.anim["rot"]], [0, 10, 20])
        self.assertEqual([f for f, _p in node.anim["pos"]], [0, 10, 20])
        np.testing.assert_allclose(node.anim["pos"][1][1], (0.5, 1.5, 0.0), atol=TOL)
        s = math.sqrt(0.5)
        np.testing.assert_allclose(np.abs(node.anim["rot"][1][1]), (s, 0.0, s, 0.0), atol=1e-4)
        self.assertEqual(back.meta["animations"][0]["length"], 20)

    def test_vdf_anim_roundtrip_and_toolkit_reads_block(self):
        path = os.path.join(self.tmp, "av.vdf")
        vdf_io.write_vdf(_vehicle_scene_with_anim(), path)
        self._check_keys(vdf_io.read_vdf(path))
        with open(path, "rb") as handle:
            parsed = vdf_file.parse_vdf(handle.read())
        self.assertTrue(parsed.anim_present)
        self.assertEqual(len(parsed.anim_elements), 1)
        self.assertEqual(len(parsed.anim_rotations), 3)
        self.assertEqual(len(parsed.anim_positions), 3)

    def test_sdf_anim_roundtrip(self):
        path = os.path.join(self.tmp, "as.sdf")
        sdf_io.write_sdf(_vehicle_scene_with_anim(), path)
        self._check_keys(sdf_io.read_sdf(path))

    def test_export_animations_off_writes_no_block(self):
        path = os.path.join(self.tmp, "no.vdf")
        vdf_io.write_vdf(_vehicle_scene_with_anim(), path, export_animations=False)
        with open(path, "rb") as handle:
            self.assertFalse(vdf_file.parse_vdf(handle.read()).anim_present)

    def test_quaternion_storage_is_an_involution(self):
        from bz98maya.core import anim

        q = (0.5, 0.5, -0.5, 0.5)
        self.assertTrue(anim.quats_close(anim.stored_to_maya_quat(anim.maya_to_stored_quat(q)), q))
        np.testing.assert_allclose(anim.quat_to_matrix(anim.matrix_to_quat(anim.quat_to_matrix(q))),
                                   anim.quat_to_matrix(q), atol=1e-9)

    def test_preset_duplicate_move_and_reference(self):
        from bz98maya.core import anim

        slots = anim.apply_preset([], "DEPLOY_PAIR")
        self.assertEqual([e["index"] for e in slots], [0, 1])
        self.assertEqual(len(anim.apply_preset([], "PERSON_CORE")), 12)
        self.assertEqual([e["index"] for e in anim.duplicate_element(slots, 0)], [0, 0, 1])
        moved, at = anim.move_element(slots, 0, "DOWN")
        self.assertEqual((at, [e["index"] for e in moved]), (1, [1, 0]))
        self.assertEqual(anim.move_element(slots, 0, "UP")[1], 0)
        self.assertTrue(anim.reference_text().strip())
        with self.assertRaises(KeyError):
            anim.apply_preset([], "NOPE")

    def test_mirror_keys_matrix_flips_x_translation(self):
        from bz98maya.core import anim

        keys = {"pos": [(0, (1.0, 0.0, 0.0)), (10, (1.0, 2.0, 0.0))]}
        rest = anim.compose((1.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        target_rest = anim.compose((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        out = anim.mirror_keys_matrix(keys, [0, 10], "X", ("pos",), 0, rest, target_rest)
        np.testing.assert_allclose(out["pos"][0][1], (-1.0, 0.0, 0.0), atol=1e-9)
        np.testing.assert_allclose(out["pos"][1][1], (-1.0, 2.0, 0.0), atol=1e-9)

    def test_replace_name_token_is_blenders(self):
        from bz98maya.core import anim

        self.assertEqual(anim.replace_name_token("arm_L", "L", "R"), "arm_R")

    def test_validation_sees_animation(self):
        scene = _vehicle_scene_with_anim()
        issues = validate.validate_scene(scene, export_mode="VDF")
        self.assertFalse(any(i["scope"] == "Animation" and i["severity"] != "INFO" for i in issues), issues)
        scene.nodes[1].anim["rot"][-1] = (20, (0.0, 0.0, 1.0, 0.0))  # does not return to the first pose
        issues = validate.validate_scene(scene, export_mode="VDF")
        self.assertTrue(any(i["scope"] == "Animation" and "Looping ANIM slot 0" in i["message"] for i in issues), issues)
        scene.meta["animations"] = []
        issues = validate.validate_scene(scene, export_mode="VDF")
        self.assertTrue(any("no ANIM sequence entries" in i["message"] for i in issues), issues)

    def test_validation_flags_skinned_meshes(self):
        scene = _vehicle_scene_with_anim()
        scene.nodes[0].mesh.weights = [[(0, 1.0)]] * len(scene.nodes[0].mesh.positions)
        issues = validate.validate_scene(scene, export_mode="VDF")
        self.assertTrue(any("Skinned mesh deformation" in i["message"] for i in issues), issues)


def _two_bone_skeleton():
    from bz98maya.core.model import Bone, Clip, Skeleton, Track

    skeleton = Skeleton(name="rig", bones=[
        Bone("root", 0, None, (0.0, 1.0, 0.0)),
        Bone("tip", 1, "root", (0.0, 2.0, 0.0)),
    ])
    skeleton.clips = [Clip("walk", 1.0, [
        Track("tip", [0.0, 0.5, 1.0], [(0, 0, 0), (0, 0.5, 0), (0, 0, 0)],
              [(1, 0, 0, 0), (0.7071068, 0, 0.7071068, 0), (1, 0, 0, 0)], [(1, 1, 1)] * 3),
    ])]
    return skeleton


class SkeletonCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_skel_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_skeleton_roundtrip_matches_toolkit_reader(self):
        from bz98maya.core import skeleton_io

        path = os.path.join(self.tmp, "rig.skeleton")
        skeleton_io.write_skeleton(_two_bone_skeleton(), path)
        back = skeleton_io.read_skeleton(path)
        self.assertEqual([(b.name, b.id, b.parent) for b in back.bones],
                         [("root", 0, None), ("tip", 1, "root")])
        np.testing.assert_allclose(back.bones[1].position, (0.0, 2.0, 0.0), atol=TOL)
        self.assertEqual([c.name for c in back.clips], ["walk"])
        track = back.clips[0].tracks[0]
        self.assertEqual(track.bone, "tip")
        np.testing.assert_allclose(track.times, [0.0, 0.5, 1.0], atol=TOL)
        np.testing.assert_allclose(track.rotations[1], (0.7071068, 0, 0.7071068, 0), atol=1e-5)

    def test_bone_ids_are_renumbered_contiguously(self):
        from bz98maya.core import skeleton_io

        skeleton = _two_bone_skeleton()
        skeleton.bones[0].id, skeleton.bones[1].id = 7, 3
        self.assertEqual(skeleton_io.renumbered(skeleton), {3: 0, 7: 1})  # by id order

    def test_influence_cut_keeps_three_heaviest_and_renormalises(self):
        from bz98maya.core import redux

        weights = [[(0, 0.4), (1, 0.3), (2, 0.2), (3, 0.1)], [(0, 0.0), (1, 0.5)]]
        ids = {0: 0, 1: 1, 2: 2, 3: 3}
        out = redux.bone_assignments(weights, ids, True)
        first = [(b, w) for v, b, w in out if v == 0]
        self.assertEqual([b for b, _w in first], [0, 1, 2])
        self.assertAlmostEqual(sum(w for _b, w in first), 1.0, places=6)
        self.assertAlmostEqual(first[0][1], 0.4 / 0.9, places=6)
        self.assertEqual([(b, w) for v, b, w in out if v == 1], [(1, 1.0)])
        raw = redux.bone_assignments(weights, ids, False)
        self.assertAlmostEqual(sum(w for v, _b, w in raw if v == 0), 0.9, places=6)

    def test_skinned_mesh_export_reads_back_with_toolkit(self):
        from bz98maya.core import redux

        cube = vdf_io.box_mesh((-1, 0, -1), (1, 2, 1))
        cube.face_materials = ["tex_a"] * 6
        cube.weights = [[(0, 0.5), (1, 0.5)] for _ in cube.positions]
        scene = Scene(nodes=[Node("rig", None, axes.identity4(), KIND_PART, cube, {})])
        scene.skeleton = _two_bone_skeleton()
        path = os.path.join(self.tmp, "rig.mesh")
        redux.write_bzr_mesh(scene, path, {"export_skeleton": True, "export_animation": True,
                                           "export_materials": False})
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "rig.skeleton")))
        back = ogre_io.read_ogre_mesh(path, import_materials=False)
        self.assertIsNotNone(back.skeleton)
        self.assertEqual([c.name for c in back.skeleton.clips], ["walk"])
        mesh = back.nodes[0].mesh
        self.assertEqual(len(mesh.weights), len(mesh.positions))
        self.assertAlmostEqual(sum(w for _b, w in mesh.weights[0]), 1.0, places=5)
        path2 = os.path.join(self.tmp, "noanim.mesh")
        redux.write_bzr_mesh(scene, path2, {"export_skeleton": True, "export_animation": False,
                                            "export_materials": False})
        self.assertEqual(ogre_io.read_ogre_mesh(path2, import_materials=False).skeleton.clips, [])




class StockSweepRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_sweep_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _scene(self):
        cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
        cube.face_materials = ["tex_a"] * 6
        body = Node("abc11bdy", None, axes.identity4(), KIND_PART, cube, {"type": 60})
        return Scene(nodes=[body], meta={"name": "x"})

    def test_per_face_geo_words_survive_a_round_trip(self):
        scene = self._scene()
        scene.nodes[0].mesh.face_data = {
            "unknown": [0, 1, 2, 3, 4, 5], "shade": [4, 4, 3, 3, 4, 4], "texture": [0, 1, 0, 1, 0, 1],
            "xlu": [0, 0, 1, 1, 0, 0], "parent": [-1, 0, 1, 2, 3, 4], "node": [1, 0, 1, 0, 1, 0],
        }
        path = os.path.join(self.tmp, "pf.vdf")
        vdf_io.write_vdf(scene, path)
        back = {n.name: n for n in vdf_io.read_vdf(path).nodes}["abc11bdy"].mesh.face_data
        for key, values in scene.nodes[0].mesh.face_data.items():
            self.assertEqual(back[key], values, key)

    def test_header_name_is_written_as_authored_even_when_empty(self):
        scene = self._scene()
        scene.meta["name"] = ""
        path = os.path.join(self.tmp, "nm.vdf")
        vdf_io.write_vdf(scene, path)
        self.assertEqual(vdf_io.read_vdf(path).meta["name"], "")

    def test_spinner_helper_chains_are_all_written(self):
        scene = self._scene()
        previous = "abc11bdy"
        for suffix in ("sp1", "sp2", "sp3"):
            cube = vdf_io.box_mesh((-0.1, -0.1, -0.1), (0.1, 0.1, 0.1))
            cube.face_materials = ["tex_a"] * 6
            node = Node("abc11" + suffix, previous, axes.identity4(), KIND_PART, cube,
                        {"type": 15, "is_spinner": True, "spinner_axis": [1.0, 0.0, 0.0],
                         "spinner_speed": 1.0, "spinner_target": previous})
            scene.nodes.append(node)
            previous = node.name
        path = os.path.join(self.tmp, "sp.vdf")
        vdf_io.write_vdf(scene, path)
        names = {n.name for n in vdf_io.read_vdf(path).nodes}
        self.assertTrue({"abc11sp1", "abc11sp2", "abc11sp3"} <= names)

    def test_lod_part_without_a_lod1_twin_is_dropped_like_blender(self):
        scene = self._scene()
        cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
        cube.face_materials = ["tex_a"] * 6
        scene.nodes.append(Node("abc21zzz", "abc11bdy", axes.identity4(), KIND_PART, cube, {"type": 60}))
        warnings = vdf_io.write_vdf(scene, os.path.join(self.tmp, "lod.vdf"))
        self.assertTrue(any("no LOD1 part with the same name" in w for w in warnings))




def _read_geo_file(path):
    with open(path, "rb") as handle:
        return GeoSerializer(handle).read()


class ReviewFixTests(unittest.TestCase):
    """Blender-parity fixes from the fresh-eyes review; each would have failed before."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_review_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _cube_scene(self, material="tex_a", materials=None):
        cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
        cube.face_materials = [material] * 6
        body = Node("abc11bdy", None, axes.identity4(), KIND_PART, cube, {"type": 60})
        return Scene(nodes=[body], meta={"name": "x"}, materials=materials or {})

    def _geo_faces(self, scene, **kwargs):
        path = os.path.join(self.tmp, "t.vdf")
        vdf_io.write_vdf(scene, path, **kwargs)
        return _read_geo_file(os.path.join(self.tmp, "abc11bdy.geo")).face_list

    # 1. batch Redux export keeps the skeleton and the parent transform
    def test_batch_redux_export_bakes_parents_and_writes_the_skeleton(self):
        from bz98maya.core import redux

        parent = Node("grp", None, _identity_translated(10.0, 0.0, 0.0), KIND_EMPTY, None, {})
        cube = vdf_io.box_mesh((-1, 0, -1), (1, 2, 1))
        cube.face_materials = ["tex_a"] * 6
        cube.weights = [[(0, 0.5), (1, 0.5)] for _ in cube.positions]
        child = Node("rig", "grp", axes.identity4(), KIND_PART, cube, {})
        scene = Scene(nodes=[parent, child])
        scene.skeleton = _two_bone_skeleton()
        redux.write_bzr_mesh(scene, os.path.join(self.tmp, "batch.mesh"),
                             {"batch_export": True, "export_skeleton": True, "export_materials": False})
        out = os.path.join(self.tmp, "rig.mesh")
        self.assertTrue(os.path.exists(out))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "rig.skeleton")))
        back = ogre_io.read_ogre_mesh(out, import_materials=False)
        xs = [p[0] for p in back.nodes[0].mesh.positions]
        self.assertAlmostEqual(min(xs), 9.0, places=4)  # the parent's +10 is baked in
        self.assertTrue(back.nodes[0].mesh.weights and back.nodes[0].mesh.weights[0])

    # 2. a preserved damage band whose part is gone is skipped, not moved to another part
    def test_damage_band_of_a_missing_part_is_dropped_with_a_warning(self):
        import base64

        scene = self._cube_scene()
        data = base64.b64encode(bytes(100)).decode("ascii")
        scene.meta["preserved"] = {"plan": [], "chunks": [], "vlocs": [],
                                   "bands": [{"part": "zzz11gone", "slot": 0, "band": 4, "data": data}]}
        warnings = vdf_io.write_vdf(scene, os.path.join(self.tmp, "d.vdf"))
        self.assertTrue(any("damage band" in w and "zzz11gone" in w for w in warnings), warnings)

    # 3. face colour = material diffuse * 255; imported colours are kept per face
    def test_face_colour_follows_blenders_material_rule(self):
        faces = self._geo_faces(self._cube_scene(materials={"tex_a": {"diffuse": [0.5, 0.25, 1.0]}}))
        self.assertEqual({(f.color.r, f.color.g, f.color.b) for f in faces}, {(127, 63, 255)})
        faces = self._geo_faces(self._cube_scene(material=""))
        self.assertEqual({(f.color.r, f.color.g, f.color.b) for f in faces}, {(255, 255, 255)})  # no material: white

    def test_imported_face_colours_are_preserved_per_face_by_default(self):
        scene = self._cube_scene()
        scene.nodes[0].mesh.face_data = {"color": [[10, 20, 30]] * 3 + [[40, 50, 60]] * 3}
        faces = self._geo_faces(scene)
        self.assertEqual([(f.color.r, f.color.g, f.color.b) for f in faces][::3], [(10, 20, 30), (40, 50, 60)])
        path = os.path.join(self.tmp, "t.vdf")
        kept = vdf_io.read_vdf(path).nodes[0].mesh
        self.assertEqual(kept.face_data["color"][0], [10, 20, 30])
        loose = vdf_io.read_vdf(path, {"PreserveFaceColors": False})
        self.assertNotIn("color", loose.nodes[0].mesh.face_data)
        self.assertEqual(loose.materials["tex_a"]["diffuse"], [10 / 255.0, 20 / 255.0, 30 / 255.0])

    # 4. face plane modes (export_geo.py:271-286)
    def test_face_plane_modes_current_and_preserve(self):
        scene = self._cube_scene()
        scene.nodes[0].mesh.face_data = {"plane": [[0.1, 0.2, 0.3, 4.5]] * 6}
        current = self._geo_faces(scene, face_plane_mode="CURRENT")
        self.assertEqual({f.plane.distance for f in current}, {1.0})  # face centre, d = 1.0
        kept = self._geo_faces(scene, face_plane_mode="PRESERVE")
        self.assertEqual({round(f.plane.distance, 4) for f in kept}, {4.5})
        self.assertAlmostEqual(kept[0].plane.surface_normal.y, 0.2, places=5)
        bare = self._geo_faces(self._cube_scene(), face_plane_mode="PRESERVE")  # nothing imported: falls back
        self.assertEqual({f.plane.distance for f in bare}, {1.0})

    # 5. texture names (export_geo.py:35-38, 205-222)
    def test_explicit_texture_names_are_written_as_typed_derived_ones_are_derived(self):
        scene = self._cube_scene(material="AVDEF4.MAP", materials={"AVDEF4.MAP": {"map_texture": "AVDEF4.MAP"}})
        self.assertEqual({f.texture_name for f in self._geo_faces(scene)}, {"AVDEF4.MAP"})
        scene = self._cube_scene(material="My Tex.png")
        self.assertEqual({f.texture_name for f in self._geo_faces(scene)}, {"my_tex"})

    # 6. automatic sphere radius = largest absolute coordinate (export_vdf.py:62-79)
    def test_auto_sphere_radius_is_the_largest_absolute_coordinate(self):
        cube = vdf_io.box_mesh((0, 0, 0), (2, 2, 2))
        self.assertAlmostEqual(vdf_io._bounds_from_mesh(cube)[1], 2.0, places=6)

    # 7. silent discards now warn
    def test_skinned_mesh_without_skeleton_and_dropped_influences_warn(self):
        from bz98maya.core import redux

        cube = vdf_io.box_mesh((-1, 0, -1), (1, 2, 1))
        cube.face_materials = ["tex_a"] * 6
        cube.weights = [[(0, 0.5), (99, 0.5)] for _ in cube.positions]
        scene = Scene(nodes=[Node("rig", None, axes.identity4(), KIND_PART, cube, {})])
        warnings = redux.write_bzr_mesh(scene, os.path.join(self.tmp, "a.mesh"), {"export_materials": False})
        self.assertTrue(any("without its skin weights" in w for w in warnings), warnings)
        scene.skeleton = _two_bone_skeleton()
        warnings = redux.write_bzr_mesh(scene, os.path.join(self.tmp, "b.mesh"),
                                        {"export_skeleton": True, "export_materials": False})
        self.assertTrue(any("outside the exported skeleton" in w for w in warnings), warnings)

    def test_trailing_bytes_on_import_and_dropped_scale_keys_warn(self):
        path = os.path.join(self.tmp, "t.vdf")
        vdf_io.write_vdf(self._cube_scene(), path)
        with open(path, "ab") as handle:
            handle.write(b"abc")
        self.assertTrue(any("trailing byte" in w for w in vdf_io.read_vdf(path).warnings))
        scene = _vehicle_scene_with_anim()
        scene.nodes[1].anim["scale"] = [(0, (1.0, 1.0, 1.0)), (20, (2.0, 2.0, 2.0))]
        warnings = vdf_io.write_vdf(scene, os.path.join(self.tmp, "s.vdf"))
        self.assertTrue(any("scale keys are not written" in w for w in warnings), warnings)
        sdf = os.path.join(self.tmp, "t.sdf")
        sdf_io.write_sdf(_vehicle_scene_with_anim(), sdf)
        with open(sdf, "ab") as handle:
            handle.write(b"ZZZZ" + struct.pack("<i", 12) + b"abcd")
        self.assertTrue(any("unrecognised chunk" in w for w in sdf_io.read_sdf(sdf).warnings))




STOCK_ABBARR_MATERIAL = """import * from "BZBase.material"

material abbarr : BZBase
{
	set_texture_alias DiffuseMap abbarr_D.dds
	set_texture_alias NormalMap flat_N.dds
	set_texture_alias SpecularMap abbarr_S.dds
	set_texture_alias EmissiveMap abbarr_E.dds

	set $diffuse "1 1 1"
	set $ambient "1 1 1"
	set $specular ".7 .7 .7"
	set $shininess "127"
}


"""

STOCK_SVTANK_MATERIAL = """import * from "BZBase.material"

material svtank00 : BZBase
{
	set_texture_alias DiffuseMap svtank_d.dds
	set_texture_alias NormalMap svtank_N.dds
	set_texture_alias SpecularMap svtank_S.dds
	set_texture_alias EmissiveMap svtank_e.dds

	set $diffuse "1 1 1"
	set $ambient "1 1 1"
	set $specular ".7 .7 .7"
	set $shininess "127"
}


material svtank01 : BZBaseCockpit
{
	set_texture_alias DiffuseMap soviet_units_cockpit_d.dds
	set_texture_alias NormalMap soviet_units_cockpit_n.dds
	set_texture_alias SpecularMap soviet_units_cockpit_S.dds
	set_texture_alias EmissiveMap soviet_units_cockpit_e.dds

	set $diffuse "1 1 1"
	set $ambient "1 1 1"
	set $specular ".7 .7 .7"
	set $shininess "127"
}
"""


def _material_blocks(text):
    """name -> (parent, [(alias, file)], [(param, value)]): what two .material texts must agree on."""
    import re

    out = {}
    for match in re.finditer(r"material\s+(\S+)\s*:\s*(\S+)\s*\{(.*?)\}", text, re.S):
        body = match.group(3)
        out[match.group(1)] = (
            match.group(2),
            re.findall(r"set_texture_alias\s+(\w+)\s+(\S+)", body),
            re.findall(r'set\s+\$(\w+)\s+"([^"]*)"', body),
        )
    return out


class SourceMaterialTests(unittest.TestCase):
    """A material read from a .material comes back as it was (parent, aliases, exact file names, values)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_srcmat_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _sourced(self, text, name="stock.material"):
        from bz98maya.core import material

        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        parsed = material.parse_material_file(path)
        return {n: {"source": {"raw": i["raw"], "preamble": i["preamble"]}, "diffuse": [1, 1, 1]} for n, i in parsed.items()}

    def _export(self, materials, folder_name="out"):
        from bz98maya.core import material

        folder = os.path.join(self.tmp, folder_name)
        os.makedirs(folder, exist_ok=True)
        warnings = []
        path = material.write_material_file(os.path.join(folder, "m.mesh"), materials, True, False, warnings)
        with open(path, encoding="utf-8") as handle:
            return handle.read(), warnings

    def test_stock_abbarr_and_svtank_materials_round_trip_exactly(self):
        for text in (STOCK_ABBARR_MATERIAL, STOCK_SVTANK_MATERIAL):
            out, warnings = self._export(self._sourced(text))
            self.assertEqual(_material_blocks(out), _material_blocks(text))
            self.assertEqual(warnings, [])
        out, _ = self._export(self._sourced(STOCK_SVTANK_MATERIAL))
        self.assertIn("material svtank01 : BZBaseCockpit", out)
        self.assertIn("set_texture_alias NormalMap flat_N.dds", self._export(self._sourced(STOCK_ABBARR_MATERIAL))[0])

    def test_real_stock_materials_round_trip_when_the_game_is_installed(self):
        import glob

        root = r"C:\\Program Files (x86)\\GOG Galaxy\\Games\\Battlezone 98 Redux"
        files = glob.glob(os.path.join(root, "**", "abbarr.material"), recursive=True)[:1] + \
            glob.glob(os.path.join(root, "**", "svtank.material"), recursive=True)[:1]
        if not files:
            self.skipTest("no stock .material files found")
        for path in files:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            out, _ = self._export(self._sourced(text, os.path.basename(path)))
            self.assertEqual(_material_blocks(out), _material_blocks(text), path)

    def test_only_what_the_user_changed_is_rewritten(self):
        materials = self._sourced(STOCK_ABBARR_MATERIAL)
        materials["abbarr"]["source"]["diffuse_override"] = [0.5, 0.25, 0.125]
        materials["abbarr"]["source"]["texture_override"] = "Paint_D.dds"
        out, warnings = self._export(materials)
        got, want = _material_blocks(out)["abbarr"], _material_blocks(STOCK_ABBARR_MATERIAL)["abbarr"]
        self.assertEqual(got[0], want[0])
        self.assertEqual([a for a in got[1] if a[0] != "DiffuseMap"], [a for a in want[1] if a[0] != "DiffuseMap"])
        self.assertEqual(dict(got[1])["DiffuseMap"], "Paint_D.dds")
        self.assertEqual(dict(got[2])["diffuse"], "0.5 0.25 0.125")
        self.assertEqual({k: v for k, v in got[2] if k != "diffuse"}, {k: v for k, v in want[2] if k != "diffuse"})
        self.assertTrue(any("Paint_D.dds" in w for w in warnings), warnings)  # not next to the .material

    def test_a_material_without_a_source_never_names_a_missing_texture(self):
        from bz98maya.core import material

        out, warnings = self._export({"newmat": {"diffuse": [0.5, 0.5, 0.5]}}, "fresh")
        self.assertNotIn("set_texture_alias", out)
        self.assertTrue(any("not found next to the .material" in w for w in warnings), warnings)
        folder = os.path.join(self.tmp, "fresh")
        open(os.path.join(folder, "NewMat_D.dds"), "wb").close()
        out, _ = self._export({"newmat": {"diffuse": [0.5, 0.5, 0.5]}}, "fresh")
        self.assertIn("set_texture_alias DiffuseMap NewMat_D.dds", out)  # the real file's case
        self.assertNotIn("NormalMap", out)
        self.assertIn("newmat_d.dds", material.material_text({"newmat": {}}))  # Blender's own text is unchanged

    def test_materials_missing_from_the_source_file_are_not_redefined(self):
        from bz98maya.core import ogre_io, redux
        from bz98maya.core.model import KIND_PART, Node, Scene

        cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
        cube.face_materials = ["abbarr"] * 3 + ["svheadlight00"] * 3
        scene = Scene(nodes=[Node("m", None, axes.identity4(), KIND_PART, cube, {})])
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        redux.write_bzr_mesh(scene, os.path.join(src, "m.mesh"), {"export_materials": False})
        with open(os.path.join(src, "m.material"), "w", encoding="utf-8") as handle:
            handle.write(STOCK_ABBARR_MATERIAL)
        back = ogre_io.read_ogre_mesh(os.path.join(src, "m.mesh"))
        self.assertTrue(back.materials["svheadlight00"]["not_in_source"])
        dst = os.path.join(self.tmp, "dst")
        os.makedirs(dst)
        warnings = redux.write_bzr_mesh(back, os.path.join(dst, "m.mesh"), {})
        with open(os.path.join(dst, "m.material"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(set(_material_blocks(text)), {"abbarr"})
        self.assertEqual(_material_blocks(text), _material_blocks(STOCK_ABBARR_MATERIAL))
        self.assertTrue(any("svheadlight00" in w and "not defined" in w for w in warnings), warnings)



if __name__ == "__main__":
    unittest.main()
