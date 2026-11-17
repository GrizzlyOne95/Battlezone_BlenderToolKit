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

from bz98maya.core import axes, ogre_io, vdf_io  # noqa: E402
from bz98maya.core.model import (  # noqa: E402
    KIND_EMPTY,
    KIND_HARDPOINT,
    KIND_LOD,
    KIND_PART,
    Mesh,
    Node,
    Scene,
)

from bz98tools import vdf_file  # noqa: E402
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
        self.assertEqual([n.name for n in scene.nodes], ["fxh11", "fxp11"])
        hull, pov = scene.nodes
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
        self.assertEqual([n.name.lower() for n in again.nodes], ["fxh11", "fxp11"])
        self.assertEqual(again.nodes[1].parent.lower(), "fxh11")
        self.assertEqual(again.nodes[1].kind, KIND_HARDPOINT)
        for before, after in zip(scene.nodes, again.nodes):
            np.testing.assert_allclose(after.matrix, before.matrix, atol=TOL)
        new_mesh = again.nodes[0].mesh
        self.assertEqual(len(new_mesh.positions), 8)  # welded on (position, normal)
        self.assertEqual(len(new_mesh.faces), 6)
        self.assertEqual(new_mesh.face_materials, hull.mesh.face_materials)
        for old, new in zip(_expanded(hull.mesh), _expanded(new_mesh)):
            np.testing.assert_allclose(new[0], old[0], atol=TOL)
            np.testing.assert_allclose(new[1], old[1], atol=TOL)
            np.testing.assert_allclose(new[2], old[2], atol=TOL)

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
        self.assertAlmostEqual(body.sphereradius, math.sqrt(3.0), places=5)
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
        self.assertEqual(set(by_name), {"tst11bdy", "tst21bdy", "tst11tur", "tst11hp1", "tst11pov"})
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
        self.assertEqual(len(back.meta["colp"]), 12)

    def test_combination_fixture_keeps_part_classes(self):
        _folder, path = self._copy_fixture("combination.vdf")
        scene = vdf_io.read_vdf(path)
        kinds = {n.name: n.kind for n in scene.nodes}
        self.assertEqual(kinds["cxp11"], KIND_HARDPOINT)  # eyepoint
        self.assertEqual(kinds["cxa11"], KIND_EMPTY)  # no GEO beside the fixture
        self.assertEqual({n.props["type"] for n in scene.nodes}, {60, 40, 66, 9})


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


if __name__ == "__main__":
    unittest.main()
