# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Real-Maya tests for the Maya plug-in (headless, run with mayapy).

    "C:\\Program Files\\Autodesk\\Maya2027\\bin\\mayapy.exe" tests/maya_integration_test.py

Skips cleanly (exit 0) when ``maya`` cannot be imported, and is not named
``test_*.py``, so ``tests/run_tests.py`` and CI under plain Python never run it.

Stock-file cases need real Battlezone assets, which are not committed: point
``BZ98_STOCK_DIR`` at a folder laid out as ``vdf/<name>/<name>.vdf`` (+ its .geo files),
``sdf/<name>/<name>.sdf`` (+ .geo) and ``mesh/*.mesh``. Without it only the
fixture-based cases run. The toolkit itself is found relative to this file.
"""

import glob
import os
import shutil
import sys
import tempfile
import unittest

try:
    import maya.standalone  # noqa: F401
except ImportError:
    print("maya is not importable; skipping the Maya integration tests")
    sys.exit(0)

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "maya"))

import numpy as np  # noqa: E402

maya.standalone.initialize(name="python")

import maya.api.OpenMaya as om  # noqa: E402
import maya.cmds as cmds  # noqa: E402

import _bootstrap  # noqa: F401,E402

from bz98maya import attributes, maya_io, menu, panel  # noqa: E402
from bz98maya.core import axes, ogre_io, schema, sdf_io, validate, vdf_io  # noqa: E402
from bz98tools import sdf_classes, vdf_classes, vdf_file  # noqa: E402

from test_maya_core import _sdf_record, _write_cube_geo, _write_sdf  # noqa: E402

PLUGIN = os.path.join(_REPO_ROOT, "maya", "plug-ins", "bz98maya_plugin.py")
STOCK = os.environ.get("BZ98_STOCK_DIR")
TOL = 1e-4
menu.RAISE_ERRORS = True


def _stock(kind):
    if not STOCK:
        return []
    if kind == "mesh":
        return sorted(glob.glob(os.path.join(STOCK, "mesh", "*.mesh")))
    return sorted(glob.glob(os.path.join(STOCK, kind, "*", "*." + kind)))


# --- helpers ---------------------------------------------------------------

def world_matrices(scene):
    """name -> world 4x4 for every node of a neutral scene."""
    by_name = {n.name: n for n in scene.nodes}
    out = {}

    def world(node):
        if node.name not in out:
            m = node.matrix
            if node.parent in by_name:
                m = axes.matmul(world(by_name[node.parent]), m)
            out[node.name] = m
        return out[node.name]

    for node in scene.nodes:
        world(node)
    return out


def loops(mesh):
    """Per face-vertex (position, uv0, material) rows, independent of vertex sharing."""
    rows = []
    loop = 0
    for index, face in enumerate(mesh.faces):
        for vi in face:
            rows.append((
                mesh.positions[vi],
                mesh.uv_sets[0][loop] if mesh.uv_sets else None,
                mesh.face_materials[index],
            ))
            loop += 1
    return rows


def maya_parts(root):
    """bzName -> long transform name, for everything under the import group."""
    out = {}
    for transform in cmds.listRelatives(root, allDescendents=True, type="transform", fullPath=True) or []:
        if cmds.attributeQuery("bzPartName", node=transform, exists=True):
            out[cmds.getAttr(transform + ".bzPartName")] = transform
    return out


def mesh_shape(transform):
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, noIntermediate=True) or []
    meshes = [s for s in shapes if cmds.nodeType(s) == "mesh"]
    return meshes[0] if meshes else None


def shading_groups(shape):
    return set(cmds.listConnections(shape, type="shadingEngine") or [])


class MayaCase(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.tmp = tempfile.mkdtemp(prefix="bzmaya_it_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def inject(self, handler, path):
        """Run a menu handler with the file dialog replaced by ``path``."""
        original = menu._ask
        menu._ask = lambda *args, **kwargs: path
        try:
            handler()
        finally:
            menu._ask = original

    # --- shared checks -----------------------------------------------------

    def check_imported(self, scene, root, texture_dir_has_images=False):
        """Maya-side state of an import matches the neutral scene it came from."""
        parts = maya_parts(root)
        self.assertEqual(set(parts), {n.name for n in scene.nodes})
        worlds = world_matrices(scene)
        for node in scene.nodes:
            transform = parts[node.name]
            parent = cmds.listRelatives(transform, parent=True, fullPath=True)[0]
            if node.parent:
                self.assertEqual(parent, parts[node.parent], node.name)
            else:
                self.assertEqual(parent, cmds.ls(root, long=True)[0], node.name)
            shape = mesh_shape(transform)
            if node.mesh is not None and node.mesh.faces:
                self.assertIsNotNone(shape, node.name)
                self.assertEqual(cmds.polyEvaluate(shape, face=True), len(node.mesh.faces), node.name)
                self.assertEqual(cmds.polyEvaluate(shape, vertex=True), len(node.mesh.positions), node.name)
                # world bounding box follows the file's positions after the axis mapping
                pts = np.array([axes.transform_point(worlds[node.name], p) for p in node.mesh.positions])
                # (exactWorldBoundingBox is not exact for rotated parents, so measure the
                # world-space vertices themselves.)
                world_pts = np.array(
                    [(p.x, p.y, p.z) for p in om.MFnMesh(maya_io._dag_path(shape)).getPoints(om.MSpace.kWorld)]
                )
                np.testing.assert_allclose(world_pts.min(axis=0), pts.min(axis=0), atol=1e-3, err_msg=node.name)
                np.testing.assert_allclose(world_pts.max(axis=0), pts.max(axis=0), atol=1e-3, err_msg=node.name)
                if node.mesh.uv_sets:
                    names = cmds.polyUVSet(shape, query=True, allUVSets=True)
                    self.assertGreaterEqual(len(names), len(node.mesh.uv_sets))
                    cmds.polyUVSet(shape, currentUVSet=True, uvSet=names[0])
                    self.assertGreater(cmds.polyEvaluate(shape, uvcoord=True), 0)
                wanted = {m for m in node.mesh.face_materials if m}
                found = set()
                for sg in shading_groups(shape):
                    name = maya_io._material_name_of(sg)
                    if name:
                        found.add(name)
                self.assertEqual(found, wanted, node.name)
            else:
                self.assertIsNone(shape, node.name)
            if node.props:
                self.check_props(attributes.read_part(transform), node.props, node.name)
            if node.kind == "hardpoint" and node.mesh is None:
                shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
                self.assertTrue(any(cmds.nodeType(s) == "locator" for s in shapes), node.name)
            # local transform: world matrix in Maya equals the composed neutral one
            flat = cmds.xform(transform, query=True, worldSpace=True, matrix=True)
            np.testing.assert_allclose(
                axes.row_vector_flat_to_matrix(flat)[:3], np.array(worlds[node.name])[:3], atol=1e-3,
                err_msg=node.name,
            )

    def check_props(self, got, wanted, name):
        """Attribute values read back from Maya equal the neutral props that were imported."""
        for key, value in wanted.items():
            label = "%s %s" % (name, key)
            if key == "damage":
                self.assertEqual(list(got.get(key, ["", "", ""])), list(value), label)
            elif isinstance(value, (list, tuple)):
                np.testing.assert_allclose(got[key], value, atol=1e-5, err_msg=label)
            elif isinstance(value, float):
                self.assertAlmostEqual(got[key], value, places=4, msg=label)
            else:
                self.assertEqual(got[key], value, label)

    def check_same_scene(self, src, out, legacy):
        """Re-read export equals the source: names, hierarchy, transforms, geometry."""
        key = (lambda s: s.lower()) if legacy else (lambda s: s)
        src_nodes = {key(n.name): n for n in src.nodes}
        out_nodes = {key(n.name): n for n in out.nodes}
        self.assertEqual(set(out_nodes), set(src_nodes))
        for name, node in src_nodes.items():
            twin = out_nodes[name]
            self.assertEqual(key(twin.parent) if twin.parent else None, key(node.parent) if node.parent else None, name)
            np.testing.assert_allclose(twin.matrix, node.matrix, atol=TOL, err_msg=name)
            if legacy and node.props:
                self.assertEqual(twin.props["type"], node.props["type"], name)
                self.assertEqual(twin.props["flags"], node.props["flags"], name)
                self.assertEqual(twin.props["lod"], node.props["lod"], name)
                self.assertEqual(twin.props.get("slot"), node.props.get("slot"), name)
            self.assertEqual(twin.mesh is None, node.mesh is None, name)
            if node.mesh is None:
                continue
            self.assertEqual(len(twin.mesh.faces), len(node.mesh.faces), name)
            if node.mesh.normals is not None and twin.mesh.normals is not None:
                # Maya stores unit normals; the GEO source may not be normalised.
                def unit(values):
                    arr = np.array(values, dtype=float)
                    return arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-12)

                np.testing.assert_allclose(
                    unit(twin.mesh.normals), unit(node.mesh.normals), atol=2e-3, err_msg=name + " normals"
                )
            before, after = loops(node.mesh), loops(twin.mesh)
            self.assertEqual(len(before), len(after), name)
            for (p0, uv0, m0), (p1, uv1, m1) in zip(before, after):
                np.testing.assert_allclose(p1, p0, atol=TOL, err_msg=name)
                if uv0 is not None:
                    np.testing.assert_allclose(uv1, uv0, atol=TOL, err_msg=name)
                expected = m0.lower()[:8] if legacy else m0
                self.assertEqual(m1.lower() if legacy else m1, expected, name)

    # --- legacy round trip (VDF / SDF) ---------------------------------------

    def roundtrip_legacy(self, source_path, fmt):
        reader = vdf_io.read_vdf if fmt == "vdf" else sdf_io.read_sdf
        src = reader(source_path)
        importer = menu.import_vdf if fmt == "vdf" else menu.import_sdf
        exporter = menu.export_vdf if fmt == "vdf" else menu.export_sdf
        self.inject(importer, source_path)
        root = cmds.ls(selection=True, long=True)[0]
        self.assertTrue(cmds.attributeQuery("bzLOD1", node=root, exists=True))
        self.check_imported(src, root)

        out_dir = os.path.join(self.tmp, "out")
        os.makedirs(out_dir)
        out_path = os.path.join(out_dir, os.path.basename(source_path))
        cmds.select(root, replace=True)
        self.inject(exporter, out_path)
        self.assertTrue(os.path.isfile(out_path))
        out = reader(out_path)
        self.check_same_scene(src, out, legacy=True)
        for node in src.nodes:
            if node.mesh is not None and node.name not in ("inner_col", "outer_col"):
                self.assertTrue(
                    any(f.lower() == node.name.lower() + ".geo" for f in os.listdir(out_dir)), node.name
                )
        # Container fields survive (header / meta values the neutral model keeps).
        for key, value in src.meta.items():
            if key == "preserved":  # the plan loses its ANIM block (animation is a later tier)
                for sub in ("chunks", "vlocs", "bands"):
                    self.assertEqual(out.meta[key][sub], value[sub], sub)
                continue
            if isinstance(value, list):
                np.testing.assert_allclose(out.meta[key], value, atol=TOL, err_msg=key)
            elif isinstance(value, float):
                self.assertAlmostEqual(out.meta[key], value, places=4, msg=key)
            else:
                self.assertEqual(out.meta[key], value, key)
        return src, out, out_path

    def fixture_vdf(self):
        folder = os.path.join(self.tmp, "src_vdf")
        os.makedirs(folder)
        shutil.copy(os.path.join(_HERE, "fixtures", "eyepoint.vdf"), folder)
        _write_cube_geo(os.path.join(folder, "fxh11.geo"), "fxh11")
        return os.path.join(folder, "eyepoint.vdf")

    def fixture_sdf(self):
        folder = os.path.join(self.tmp, "src_sdf")
        os.makedirs(folder)
        parts = {
            "tst11bdy": _sdf_record("tst11bdy", ddr=3),
            "tst11dck": _sdf_record(
                "tst11dck", "tst11bdy", [0, 0, -1, 0, 1, 0, 1, 0, 0, 1.0, 2.0, 3.0], 9, 0, 0.25, 0.5, 0.75, 2.0
            ),
            "tst11hp1": _sdf_record("tst11hp1", "tst11bdy", None, 70),
            "tst21bdy": _sdf_record("tst21bdy", ddr=3),
        }
        path = os.path.join(folder, "tstdep.sdf")
        _write_sdf(path, {0: ["tst11bdy", "tst11dck", "tst11hp1"], 2: ["tst21bdy"]}, parts, 3)
        for name in ("tst11bdy", "tst11dck", "tst21bdy"):
            _write_cube_geo(os.path.join(folder, name + ".geo"), name)
        return path


# --- tests -----------------------------------------------------------------

class PluginTests(MayaCase):
    def test_plugin_loads_and_menu_code_does_not_raise(self):
        if cmds.pluginInfo(PLUGIN, query=True, loaded=True):
            cmds.unloadPlugin(os.path.basename(PLUGIN))
        cmds.loadPlugin(PLUGIN, quiet=True)
        self.assertTrue(cmds.pluginInfo(PLUGIN, query=True, loaded=True))
        # No GUI in standalone: building and deleting the menu must be harmless no-ops.
        self.assertFalse(menu.create_menu())
        menu.delete_menu()
        cmds.evalDeferred(menu.create_menu)  # what initializePlugin queues
        cmds.unloadPlugin(os.path.basename(PLUGIN))
        self.assertFalse(cmds.pluginInfo(PLUGIN, query=True, loaded=True))
        cmds.loadPlugin(PLUGIN, quiet=True)

    def test_menu_dialog_cancel_is_a_noop(self):
        before = len(cmds.ls(dag=True))
        self.inject(menu.import_vdf, None)
        self.inject(menu.export_mesh, None)
        self.assertEqual(len(cmds.ls(dag=True)), before)


class VdfTests(MayaCase):
    def test_fixture_vdf_roundtrip(self):
        src, out, _ = self.roundtrip_legacy(self.fixture_vdf(), "vdf")
        self.assertEqual({n.kind for n in src.nodes}, {"part", "hardpoint"})

    def test_stock_vdf_roundtrips(self):
        files = _stock("vdf")
        if not files:
            self.skipTest("BZ98_STOCK_DIR not set")
        for path in files:
            with self.subTest(path=os.path.basename(path)):
                cmds.file(new=True, force=True)
                shutil.rmtree(os.path.join(self.tmp, "out"), True)
                src, out, _ = self.roundtrip_legacy(path, "vdf")
                self.assertGreater(len(src.nodes), 0)

    def test_stock_vdf_set_has_hardpoints_and_lod_parts(self):
        files = _stock("vdf")
        if not files:
            self.skipTest("BZ98_STOCK_DIR not set")
        scenes = [vdf_io.read_vdf(p) for p in files]
        self.assertGreaterEqual(len(files), 3)
        self.assertTrue(any(n.kind == "hardpoint" for s in scenes for n in s.nodes))
        self.assertTrue(any(n.kind == "lod" for s in scenes for n in s.nodes))


class SdfTests(MayaCase):
    def test_fixture_sdf_roundtrip(self):
        src, out, _ = self.roundtrip_legacy(self.fixture_sdf(), "sdf")
        self.assertEqual(out.meta["name"], "Test Depot")
        by_name = {n.name: n for n in out.nodes}
        # SDF-only per-part fields survive Maya (stored in bzProps)
        self.assertEqual((by_name["tst11dck"].props["x"], by_name["tst11dck"].props["time"]), (0.25, 2.0))
        self.assertEqual(by_name["tst11bdy"].props["ddr"], 3)

    def test_stock_sdf_roundtrips(self):
        files = _stock("sdf")
        if not files:
            self.skipTest("BZ98_STOCK_DIR not set")
        self.assertGreaterEqual(len(files), 2)
        for path in files:
            with self.subTest(path=os.path.basename(path)):
                cmds.file(new=True, force=True)
                shutil.rmtree(os.path.join(self.tmp, "out"), True)
                self.roundtrip_legacy(path, "sdf")


class OgreTests(MayaCase):
    def roundtrip_mesh(self, source_path):
        src = ogre_io.read_ogre_mesh(source_path)
        self.inject(menu.import_mesh, source_path)
        root = cmds.ls(selection=True, long=True)[0]
        self.check_imported(src, root)

        out_path = os.path.join(self.tmp, "out_" + os.path.basename(source_path))
        cmds.select(root, replace=True)
        self.inject(menu.export_mesh, out_path)
        out = ogre_io.read_ogre_mesh(out_path)
        self.assertEqual(len(out.nodes), len(src.nodes))
        self.check_same_scene(src, out, legacy=False)
        # Re-read the exported file with the toolkit's own importer as well.
        from bz98tools.ogrefast.pure import import_compat

        importer = import_compat.KenshiObjectSerializer()
        importer.add_resource_location(os.path.dirname(out_path))
        subs = importer.load_mesh(os.path.basename(out_path)).get_submeshes()
        self.assertEqual([s.material for s in subs], [n.mesh.face_materials[0] for n in out.nodes])
        return src, out

    def synthetic_mesh(self):
        node_mesh = vdf_io.Mesh(
            positions=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            faces=[(0, 1, 2), (0, 2, 3)],
            face_materials=["mat/a", "mat/a"],
            normals=[(0, 0, 1)] * 6,
            uv_sets=[[(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)], [(0.5, 0.5)] * 6],
        )
        second = vdf_io.Mesh(
            positions=[(0, 0, 0), (1, 0, 0), (0, 0, 1)],
            faces=[(0, 2, 1)],
            face_materials=["mat/b"],
            normals=[(0, 1, 0)] * 3,
            uv_sets=[[(0, 0), (0, 1), (1, 0)]],
        )
        from bz98maya.core.model import Node, Scene

        scene = Scene(nodes=[Node("quad", None, axes.identity4(), "part", node_mesh), Node("tri", None, axes.identity4(), "part", second)])
        path = os.path.join(self.tmp, "synthetic.mesh")
        ogre_io.write_ogre_mesh(scene, path)
        return path

    def test_synthetic_mesh_roundtrip_with_two_uv_sets_and_slash_materials(self):
        path = self.synthetic_mesh()
        src, out = self.roundtrip_mesh(path)
        self.assertEqual(len(src.nodes), 2)
        self.assertEqual({len(n.mesh.uv_sets) for n in out.nodes}, {1, 2})

    def test_stock_meshes_roundtrip(self):
        files = _stock("mesh")
        if not files:
            self.skipTest("BZ98_STOCK_DIR not set")
        self.assertGreaterEqual(len(files), 3)
        for path in files:
            with self.subTest(path=os.path.basename(path)):
                cmds.file(new=True, force=True)
                self.roundtrip_mesh(path)

    def test_concave_ngon_is_triangulated_correctly(self):
        # L-shaped hexagon in the XZ plane, counter-clockwise seen from +Y: area 3.
        pts = [(0, 0, 0), (2, 0, 0), (2, 0, -1), (1, 0, -1), (1, 0, -2), (0, 0, -2)]
        pts = [(x, y, -z) for x, y, z in pts]
        transform = cmds.polyCreateFacet(point=pts, name="ell")[0]
        shape = mesh_shape(cmds.ls(transform, long=True)[0])
        self.assertEqual(cmds.polyEvaluate(shape, face=True), 1)
        cmds.select(transform, replace=True)
        out_path = os.path.join(self.tmp, "ell.mesh")
        self.inject(menu.export_mesh, out_path)
        scene = ogre_io.read_ogre_mesh(out_path)
        mesh = scene.nodes[0].mesh
        self.assertGreaterEqual(len(mesh.faces), 4)  # a hexagon is 4 triangles
        total = np.zeros(3)
        for face in mesh.faces:
            a, b, c = (np.array(mesh.positions[i]) for i in face)
            total += np.cross(b - a, c - a) / 2.0
        # A fan from vertex 0 over this concave polygon would give a different (or cancelling) sum.
        self.assertAlmostEqual(abs(total[1]), 3.0, places=4)
        np.testing.assert_allclose([total[0], total[2]], 0.0, atol=1e-4)
        for face in mesh.faces:
            a, b, c = (np.array(mesh.positions[i]) for i in face)
            self.assertGreater(float(np.cross(b - a, c - a)[1]) * total[1], 0.0)
        # The temporary duplicate is gone.
        self.assertEqual(len(cmds.ls(type="mesh")), 1)

    def test_unusable_first_uv_set_is_reported(self):
        cube = cmds.ls(cmds.polyCube(name="uvcube")[0], long=True)[0]
        shape = mesh_shape(cube)
        cmds.polyUVSet(cube, create=True, uvSet="second")
        cmds.polyUVSet(cube, copy=True, uvSet="map1", newUVSet="second")
        cmds.polyUVSet(cube, currentUVSet=True, uvSet="map1")
        cmds.polyMapDel(cube + ".f[*]")  # map1 is now empty, 'second' still has the UVs
        cmds.polyUVSet(cube, create=True, uvSet="third")  # an empty extra set
        warnings = []
        mesh = maya_io._extract_mesh(shape, warnings, "uvcube")
        self.assertEqual(len(mesh.uv_sets), 1)
        self.assertTrue(any("'map1'" in w and "skipped" in w for w in warnings), warnings)
        self.assertTrue(any("'third'" in w and "skipped" in w for w in warnings), warnings)
        self.assertTrue(any("'second' is written as UV0" in w for w in warnings), warnings)


class SceneBehaviourTests(MayaCase):
    def test_skipped_group_is_baked_into_exported_children(self):
        cmds.group(empty=True, name="organiser")
        cmds.setAttr("organiser.translate", 4, 1, -2)
        cmds.setAttr("organiser.rotate", 0, 90, 0)
        cube = cmds.polyCube(name="tst11bdy")[0]
        cmds.parent(cube, "organiser")
        cmds.setAttr(cube + ".translate", 0, 2, 3)
        child = cmds.polyCube(name="tst11tur")[0]
        cmds.parent(child, cube)
        cmds.setAttr(child + ".translate", 1, 0, 0)
        expected = {
            name: axes.row_vector_flat_to_matrix(cmds.xform(name, query=True, worldSpace=True, matrix=True))
            for name in ("tst11bdy", "tst11tur")
        }
        cmds.select(clear=True)  # nothing selected = whole scene
        vdf_path = os.path.join(self.tmp, "bake.vdf")
        self.inject(menu.export_vdf, vdf_path)
        scene = vdf_io.read_vdf(vdf_path)
        nodes = {n.name: n for n in scene.nodes if n.name not in ("inner_col", "outer_col")}
        self.assertEqual(set(nodes), {"tst11bdy", "tst11tur"})
        self.assertIsNone(nodes["tst11bdy"].parent)
        self.assertEqual(nodes["tst11tur"].parent, "tst11bdy")
        np.testing.assert_allclose(nodes["tst11bdy"].matrix, expected["tst11bdy"], atol=1e-3)
        np.testing.assert_allclose(
            axes.matmul(nodes["tst11bdy"].matrix, nodes["tst11tur"].matrix), expected["tst11tur"], atol=1e-3
        )

    def test_locator_without_lod_digit_is_skipped_with_a_warning(self):
        cmds.polyCube(name="tst11bdy")
        cmds.spaceLocator(name="locator1")
        cmds.select(clear=True)
        out = os.path.join(self.tmp, "w.vdf")
        warnings = vdf_io.write_vdf(maya_io.collect_scene(), out)
        self.assertTrue(any("locator1" in w and "skipped" in w for w in warnings), warnings)
        self.assertEqual([n.name for n in vdf_io.read_vdf(out).nodes][:1], ["tst11bdy"])

    def test_import_flushes_undo_without_error(self):
        path = self.fixture_vdf()
        self.inject(menu.import_vdf, path)
        cmds.undo() if cmds.undoInfo(query=True, undoQueueEmpty=True) is False else None
        self.assertTrue(cmds.objExists("eyepoint"))

    def test_hardpoint_locator_attrs_and_user_tagged_locator_export(self):
        path = self.fixture_vdf()
        self.inject(menu.import_vdf, path)
        self.assertEqual(cmds.getAttr("fxp11.bzGEOType"), 40)
        cmds.spaceLocator(name="usr11hp1")
        attributes.ensure_part("usr11hp1")
        cmds.setAttr("usr11hp1.bzGEOType", 71)
        cmds.select(clear=True)
        out = os.path.join(self.tmp, "u.vdf")
        self.inject(menu.export_vdf, out)
        by_name = {n.name: n for n in vdf_io.read_vdf(out).nodes}
        self.assertEqual(by_name["usr11hp1"].props["type"], 71)
        self.assertEqual(by_name["usr11hp1"].kind, "hardpoint")


class AuthoringAttributeTests(MayaCase):
    def tagged_locator(self, name="tst11bdy"):
        transform = cmds.spaceLocator(name=name)[0]
        attributes.ensure_part(transform)
        return transform

    def test_every_field_becomes_a_typed_attribute_with_blenders_defaults(self):
        node = self.tagged_locator()
        for fld in schema.part_fields():
            self.assertTrue(attributes.exists(node, fld.attr), fld.attr)
        self.assertEqual(cmds.getAttr(node + ".bzGEOType"), 60)  # Blender's default
        self.assertEqual(cmds.getAttr(node + ".bzSphereRadius"), 3.0)
        self.assertEqual(cmds.getAttr(node + ".bzGEOFaceShadeTypeDefault"), 4)
        self.assertEqual(cmds.getAttr(node + ".bzSpinnerAxis")[0], (1.0, 0.0, 0.0))
        self.assertEqual(len(cmds.getAttr(node + ".bzRawVDFMatrix")), 12)
        self.assertEqual(cmds.attributeQuery("bzGEOType", node=node, attributeType=True), "enum")
        self.assertEqual(cmds.attributeQuery("bzBoundsMode", node=node, attributeType=True), "enum")
        self.assertEqual(cmds.attributeQuery("bzGeoCenter", node=node, attributeType=True), "double3")
        # the enum lists Blender's full GEO type list
        listed = cmds.attributeQuery("bzGEOType", node=node, listEnum=True)[0]
        self.assertIn("70 - WEAPON_HARDPOINT", listed)
        self.assertIn("81 - PARKING_LOT", listed)
        self.assertEqual(listed.count(":") + 1, len(schema.geo_types()))

    def test_every_part_field_round_trips_through_maya_attributes(self):
        node = self.tagged_locator()
        props = {}
        for i, fld in enumerate(schema.part_fields()):
            if fld.derived:
                continue
            if fld.kind == "bool":
                value = not bool(fld.default)
            elif fld.kind == "int":
                value = min(int(fld.default or 0) + 3 + i, int(fld.hi) - 1 if fld.hi is not None else 10 ** 6)
            elif fld.kind == "float":
                value = float(fld.default or 0.0) + 1.5 + i
            elif fld.kind == "string":
                value = "ab%d" % i
            elif fld.kind == "enum":
                value = next(v for _l, v in fld.enum if v != fld.default)
            elif fld.kind == "float3":
                value = (1.25 + i, -2.5, 3.75)
            else:
                value = tuple(float(k) * 0.5 + i for k in range(12))
            if fld.keys:
                for key, comp in zip(fld.keys, value):
                    props[key] = comp
            elif fld.key == "bounds_mode":
                props[fld.key] = schema.BOUNDS_MODES[value]
            elif fld.key.startswith("damage") and fld.key[-1:] in "123":
                props.setdefault("damage", ["", "", ""])[int(fld.key[-1]) - 1] = value
            elif fld.kind in ("float3", "floatarray"):
                props[fld.key] = list(value)
            else:
                props[fld.key] = value
        props["lod"] = 2
        attributes.write_part(node, props)
        back = attributes.read_part(node)
        self.check_props(back, dict(props), "all fields")
        self.assertEqual(back["lod"], 2)  # extra JSON

    def test_flags_type_and_unknown_enum_values_survive_maya(self):
        node = self.tagged_locator()
        attributes.write_part(node, {"type": 99, "flags": 0x40000001 | 0x2000 | (3 << 16) | 0x7000000})
        self.assertEqual(cmds.getAttr(node + ".bzGEOType"), 99)  # outside Blender's list: kept, not remapped
        self.assertEqual(attributes.read_part(node)["type"], 99)
        self.assertTrue(cmds.getAttr(node + ".bzFlagKeepBounds"))
        self.assertEqual(cmds.getAttr(node + ".bzObjFlagTeam"), 3)
        self.assertEqual(cmds.getAttr(node + ".bzCollisionClass"), 0x2000)
        cmds.setAttr(node + ".bzFlagDestroyedSeed", True)
        cmds.setAttr(node + ".bzFlagKeepBounds", False)
        flags = attributes.read_part(node)["flags"]
        self.assertEqual(flags & 0x201, 0x200)
        self.assertEqual(flags & 0x7000000, 0x7000000)  # unknown bits stay
        self.assertEqual(panel.flags_unknown_hex(node), "0x47000000")
        # a reserved collision nibble value is kept and the attribute shows it
        attributes.write_part(node, {"type": 5, "flags": 0x5000 | 0x50000})
        self.assertEqual(attributes.read_part(node)["flags"], 0x5000 | 0x50000)
        self.assertEqual(cmds.getAttr(node + ".bzCollisionClass"), 0x5000)

    def test_attributes_drive_the_exported_files(self):
        cube = cmds.ls(cmds.polyCube(name="tst11bdy")[0], long=True)[0]
        pov = cmds.ls(cmds.spaceLocator(name="tst11pov")[0], long=True)[0]
        cmds.setAttr(cube + ".translate", 0, 1, 2)
        attributes.ensure_part(cube)
        attributes.ensure_part(pov)
        cmds.setAttr(cube + ".bzGEOType", 65)
        cmds.setAttr(cube + ".bzBoundsMode", 1)  # PRESERVE
        cmds.setAttr(cube + ".bzHasAuthoredBounds", True)
        cmds.setAttr(cube + ".bzGeoCenter", 1.5, 2.5, 3.5, type="double3")
        cmds.setAttr(cube + ".bzSphereRadius", 12.5)
        cmds.setAttr(cube + ".bzBoxHalfHeight", 4, 5, 6, type="double3")
        cmds.setAttr(cube + ".bzDamageGeo1", "tst11d1", type="string")
        cmds.setAttr(cube + ".bzFlagKeepBounds", True)
        cmds.setAttr(cube + ".bzObjFlagTeam", 2)
        cmds.setAttr(cube + ".bzSDFDDR", 333)
        cmds.setAttr(cube + ".bzSDFTarget", 1.0, 2.0, 3.0, type="double3")
        cmds.setAttr(cube + ".bzSDFTime", 6.5)
        cmds.setAttr(cube + ".bzGEOHeaderUnknown2", 9)
        cmds.setAttr(cube + ".bzGEOFaceShadeTypeDefault", 5)
        cmds.setAttr(pov + ".bzGEOType", 40)
        root = panel.create_model_node("vehicle_header")
        cmds.setAttr(root + ".bzName", "Attr Tank", type="string")
        cmds.setAttr(root + ".bzVehicleSize", 3)
        cmds.setAttr(root + ".bzMass", 4321.0)
        cmds.setAttr(root + ".bzLOD2", 77.0)
        cmds.setAttr(root + ".bzDeathExplosion", "x.xdf", type="string")
        cmds.select(clear=True)

        vdf_path = os.path.join(self.tmp, "attr.vdf")
        self.inject(menu.export_vdf, vdf_path)
        back = vdf_io.read_vdf(vdf_path)
        by_name = {n.name: n for n in back.nodes}
        body = by_name["tst11bdy"].props
        self.assertEqual(body["type"], 65)
        self.assertEqual(body["flags"], 0x1 | (2 << 16))
        np.testing.assert_allclose(body["geocenter"], [1.5, 2.5, 3.5], atol=1e-5)
        self.assertAlmostEqual(body["sphereradius"], 12.5, places=4)
        np.testing.assert_allclose(body["boxhalfheight"], [4, 5, 6], atol=1e-5)
        self.assertEqual(body["damage"][0], "tst11d1")
        self.assertEqual((body["header_flags"], body["face_shade"]), (9, 5))
        self.assertEqual(by_name["tst11pov"].props["type"], 40)
        self.assertEqual((back.meta["name"], back.meta["vehiclesize"]), ("Attr Tank", 3))
        self.assertAlmostEqual(back.meta["mass"], 4321.0, places=2)
        self.assertAlmostEqual(back.meta["lod_dists"][1], 77.0, places=3)

        sdf_path = os.path.join(self.tmp, "attr.sdf")
        self.inject(menu.export_sdf, sdf_path)
        sdf = sdf_io.read_sdf(sdf_path)
        part = {n.name: n for n in sdf.nodes}["tst11bdy"].props
        self.assertEqual((part["ddr"], part["x"], part["y"], part["z"], part["time"]), (333, 1.0, 2.0, 3.0, 6.5))
        self.assertEqual(sdf.meta["explosioneffect"], "x.xdf")

    def test_model_header_attributes_are_created_on_import_and_read_on_export(self):
        path = self.fixture_sdf()
        root = maya_io.import_sdf(path)
        self.assertEqual(cmds.getAttr(root + ".bzName"), "Test Depot")
        self.assertEqual(cmds.getAttr(root + ".bzStructureType"), 1)
        self.assertEqual(cmds.getAttr(root + ".bzDefensive"), 500000)
        self.assertEqual(cmds.getAttr(root + ".bzLOD1"), 400.0)
        cmds.setAttr(root + ".bzStructureType", 4)
        out = os.path.join(self.tmp, "hdr.sdf")
        cmds.select(root, replace=True)
        self.inject(menu.export_sdf, out)
        self.assertEqual(sdf_io.read_sdf(out).meta["structuretype"], 4)


class ValidationAndPanelTests(MayaCase):
    def broken_scene(self):
        for name in ("abc11bdy", "abc21cok", "x", "toolongname1"):
            transform = cmds.polyCube(name=name)[0]
            attributes.ensure_part(transform)
        cmds.setAttr("abc11bdy.bzGEOType", 60)
        cmds.parent("abc21cok", "abc11bdy")
        cmds.setAttr("abc21cok.bzGEOType", 69)
        gc = cmds.spaceLocator(name="abc11gc9")[0]
        attributes.ensure_part(gc)
        cmds.setAttr(gc + ".bzGEOType", 71)
        pov = cmds.spaceLocator(name="abc11pov")[0]
        attributes.ensure_part(pov)
        cmds.setAttr(pov + ".bzGEOType", 40)
        cmds.setAttr("abc11bdy.scale", 2, 1, 1)
        cmds.select(clear=True)

    def test_validation_reports_the_blender_issues_for_a_broken_scene(self):
        self.broken_scene()
        issues = maya_io.validate_scene("VDF")
        found = {(i["severity"], i["scope"], i["target"]) for i in issues}
        for expected in (
            ("WARNING", "LOD", "abc21cok"),
            ("WARNING", "Object", "x"),
            ("WARNING", "Object", "toolongname1"),
            ("WARNING", "Transform", "abc11bdy"),
            ("WARNING", "Scene", "inner_col"),
            ("WARNING", "Scene", "outer_col"),
        ):
            self.assertIn(expected, found)
        messages = [i["message"] for i in issues]
        self.assertTrue(any("exceeds 8 characters" in m for m in messages))
        self.assertTrue(any("shorter than 5 characters" in m for m in messages))
        # the same scene through the core validator (the code Blender runs) gives the same issues
        scene = maya_io.collect_scene()
        again = validate.validate_scene(scene, export_mode="VDF")
        self.assertEqual(
            sorted((i["severity"], i["scope"], i["target"], i["message"]) for i in again),
            sorted((i["severity"], i["scope"], i["target"], i["message"]) for i in issues),
        )
        self.assertEqual(maya_io.LAST_ISSUES, issues)
        self.assertEqual(len(menu.validate_scene()), len(issues))

    def test_export_validates_first_but_never_blocks(self):
        self.broken_scene()
        out = os.path.join(self.tmp, "warned.vdf")
        self.inject(menu.export_vdf, out)
        self.assertTrue(os.path.isfile(out))
        found = validate.counts(maya_io.LAST_ISSUES, "VDF")
        self.assertGreater(found["WARNING"], 0)

    def test_select_target_and_fix_legacy_name(self):
        self.broken_scene()
        issues = maya_io.validate_scene("VDF")
        issue = next(i for i in issues if i["target"] == "x" and i["action"] == "fix_name")
        self.assertEqual(panel.select_issue_target(issue), cmds.ls("|x", long=True)[0])
        self.assertEqual(cmds.ls(selection=True), ["x"])
        new_name = panel.fix_issue_name(issue)
        self.assertEqual(len(new_name), 5)
        self.assertEqual((new_name[3], new_name[4]), ("3", "1"))
        self.assertTrue(cmds.objExists(new_name))
        self.assertEqual(cmds.getAttr(new_name + ".bzPartName"), new_name)
        self.assertFalse(
            any(i["target"] == new_name and i["scope"] == "Object" for i in maya_io.validate_scene("VDF"))
        )

    def test_panel_controller_reads_and_writes_the_selected_part(self):
        cube = cmds.ls(cmds.polyCube(name="tst11bdy")[0], long=True)[0]
        cmds.select(cube, replace=True)
        self.assertEqual(panel.selected_part(), cube)
        state = panel.part_state(cube)
        self.assertEqual(state["bzGEOType"], 60)  # untagged: Blender's defaults
        self.assertFalse(attributes.is_part(cube))
        panel.set_part_attr(cube, "bzGEOType", 70)  # editing tags the transform
        self.assertTrue(attributes.is_part(cube))
        self.assertEqual(panel.part_state(cube)["bzGEOType"], 70)
        self.assertIn("WEAPON_HARDPOINT", panel.role_text(70))
        self.assertIn("WARNING", panel.hint_text(70))
        self.assertIn("LOD1", panel.lod_text("tst11bdy"))
        panel.set_part_attr(cube, "bzFlagViewRender", True)
        self.assertEqual(panel.effective_flags(cube), 0x10)
        self.assertIsNone(panel.model_node(cube))
        model = panel.create_model_node()
        self.assertEqual(panel.model_node(cube), model)
        panel.set_model_attr(model, "bzMass", 5.5)
        self.assertEqual(panel.model_state(model)["bzMass"], 5.5)
        self.assertIn("Unit scale", panel.eyepoint_scale_warning(cube))
        cmds.setAttr(cube + ".scale", 2, 2, 2)
        self.assertIn("multiplies POV basis rows by 25", panel.eyepoint_scale_warning(cube))
        self.assertIn("Redux", panel.info_text("DUAL_COMPONENT_INFO_LINES"))
        self.assertIn("70 - WEAPON_HARDPOINT", schema.geo_type_reference_text())

    def test_panel_ui_builds_when_the_ui_is_available(self):
        with self.assertRaises(RuntimeError):
            panel.show_panel()  # needs a main window; says so instead of failing obscurely
        try:
            window = cmds.window("bzTestWindow")
            cmds.columnLayout()
        except RuntimeError as exc:
            self.skipTest("UI layouts are unavailable in standalone: %s" % exc)
        form = panel.build_ui()
        cube = cmds.ls(cmds.polyCube(name="tst11bdy")[0], long=True)[0]
        cmds.select(cube, replace=True)
        form.refresh()
        self.assertEqual(form.node, cube)
        self.assertTrue(form.part_controls and form.model_controls)
        # Standalone Maya creates the controls but they hold no state (queries return False),
        # so feed the change handlers the value a user would have typed.
        ctrl, fld = form.part_controls["bzSphereRadius"]
        form._control_value = lambda c, f: 7.5 if f.attr == "bzSphereRadius" else 71
        form.on_part_change(fld, None)
        self.assertEqual(cmds.getAttr(cube + ".bzSphereRadius"), 7.5)
        ctrl, fld = form.part_controls["bzGEOType"]
        form.on_part_change(fld, None)
        self.assertEqual(cmds.getAttr(cube + ".bzGEOType"), 71)
        del form._control_value
        form.validate()
        self.assertGreater(len(form.issues), 0)
        cmds.deleteUI(window)


class ToolTests(MayaCase):
    def test_create_vehicle_geo_set(self):
        created = maya_io.create_vehicle_geo_set("tst")
        names = [c.split("|")[-1] for c in created]
        self.assertEqual(
            names, ["tst11bod", "tst11pov", "tst11gc1", "tst11gr1", "tst11gs1", "tst11gm1", "tst11rot", "tst11nac"]
        )
        types = {n: cmds.getAttr(n + ".bzGEOType") for n in names}
        self.assertEqual(types["tst11pov"], 40)
        self.assertEqual(types["tst11gc1"], 71)
        pov = cmds.listRelatives("tst11pov", shapes=True)[0]
        self.assertEqual(cmds.nodeType(pov), "locator")  # invisible classes are locators
        self.assertIsNotNone(mesh_shape(cmds.ls("tst11bod", long=True)[0]))
        self.assertEqual(cmds.listRelatives("tst11gc1", parent=True)[0], "tst11bod")
        self.assertTrue(cmds.getAttr("tst11bod.bzGenerateCollision"))
        # a second set picks a free prefix
        again = maya_io.create_vehicle_geo_set("tst")
        self.assertNotEqual(again[0].split("|")[-1], "tst11bod")
        # the set validates and exports
        cmds.select(created[0], replace=True)
        issues = maya_io.validate_scene("VDF")
        self.assertFalse([i for i in issues if i["severity"] == "ERROR"], issues)
        out = os.path.join(self.tmp, "set.vdf")
        self.inject(menu.export_vdf, out)
        back = {n.name: n for n in vdf_io.read_vdf(out).nodes}
        self.assertEqual(back["tst11gr1"].props["type"], 72)
        self.assertEqual(back["tst11gr1"].parent, "tst11bod")

    def test_select_mesh_and_non_mesh_parts(self):
        maya_io.create_vehicle_geo_set("tst")
        meshes = menu.select_mesh_parts()
        self.assertEqual({m.split("|")[-1] for m in meshes}, {"tst11bod", "tst11rot", "tst11nac"})
        others = menu.select_non_mesh_parts()
        self.assertEqual(len(others), 5)

    def test_generate_collision_settings(self):
        cube = cmds.ls(cmds.polyCube(name="tst11bdy", width=2, height=4, depth=6)[0], long=True)[0]
        maya_io.generate_collision(cube)
        np.testing.assert_allclose(cmds.getAttr(cube + ".bzGeoCenter")[0], (0, 0, 0), atol=1e-5)
        # BZ half extents: x 1, y (up) 2, z (forward) 3
        np.testing.assert_allclose(cmds.getAttr(cube + ".bzBoxHalfHeight")[0], (1, 2, 3), atol=1e-5)
        self.assertAlmostEqual(cmds.getAttr(cube + ".bzSphereRadius"), (1 + 4 + 9) ** 0.5, places=4)

    def test_standalone_geo_import_and_export(self):
        folder = os.path.join(self.tmp, "geo")
        os.makedirs(folder)
        geo_path = os.path.join(folder, "solo.geo")
        from test_maya_core import _write_cube_geo

        _write_cube_geo(geo_path, "solo")
        self.inject(menu.import_geo, geo_path)
        root = cmds.ls(selection=True, long=True)[0]
        part = list(maya_parts(root).values())[0]
        self.assertEqual(cmds.polyEvaluate(mesh_shape(part), face=True), 6)
        self.assertEqual(cmds.getAttr(part + ".bzGEOHeaderUnknown"), 69)
        cmds.setAttr(part + ".bzGEOHeaderUnknown", 71)
        cmds.setAttr(part + ".bzGEOFaceXluscentTypeDefault", 2)
        cmds.select(part, replace=True)
        out = os.path.join(folder, "out.geo")
        self.inject(menu.export_geo, out)
        back = vdf_io.read_geo(out).nodes[0]
        self.assertEqual(len(back.mesh.faces), 6)
        self.assertEqual((back.props["header_unknown"], back.props["face_xlu"]), (71, 2))
        cmds.file(new=True, force=True)
        with self.assertRaises(RuntimeError):  # nothing in the scene to export
            self.inject(menu.export_geo, os.path.join(folder, "none.geo"))


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
    os._exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
