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
import struct
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

from bz98maya import animation_tools, attributes, legacy_anim, maya_io, menu, panel  # noqa: E402
from bz98maya.core import axes, ogre_io, schema, sdf_io, validate, vdf_io  # noqa: E402
from bz98tools import sdf_classes, vdf_classes, vdf_file  # noqa: E402

from test_maya_core import (  # noqa: E402
    _decode_mesh, _sdf_record, _two_bone_skeleton, _vehicle_scene_with_anim, _write_cube_geo, _write_sdf,
)
from skel_compare import compare_skeletons  # noqa: E402

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
        maya_io.BLOCK_ON_ERRORS = False  # most tests export scenes that Blender's validator would reject
        self.addCleanup(setattr, maya_io, "BLOCK_ON_ERRORS", True)
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

    @staticmethod
    def animated_names(scene):
        """Names of keyed parts and their descendants: Maya shows the first key's pose, not the record matrix."""
        by_name = {n.name: n for n in scene.nodes}

        def under(node):
            while node is not None:
                if node.anim:
                    return True
                node = by_name.get(node.parent)
            return False

        return {n.name for n in scene.nodes if under(n)}

    def check_imported(self, scene, root, texture_dir_has_images=False):
        """Maya-side state of an import matches the neutral scene it came from."""
        animated = self.animated_names(scene)
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
                if node.name not in animated:  # Maya shows the first key's pose, not the record matrix
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
            if node.name not in animated:
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
            if not node.anim:  # a keyed part is written at its first key's pose
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
                self.assertEqual(m1, m0, name)  # an explicit texture name is written as typed (export_geo.py:210-222)

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
            if key == "preserved":  # the plan keeps its other chunks
                for sub in ("chunks", "vlocs", "bands"):
                    self.assertEqual(out.meta[key][sub], value[sub], sub)
                continue
            if key == "use_translation2" and not any((n.anim or {}).get("scale") for n in src.nodes):
                continue  # a scale-track flag with no scale keys is not written (stock files carry unused ones)
            if key == "animations":  # slot table: same slots in the same order
                self.assertEqual([(e["index"], e["start"], e["length"], e["loop"]) for e in out.meta[key]],
                                 [(e["index"], e["start"], e["length"], e["loop"]) for e in value])
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
        self.assertAlmostEqual(cmds.getAttr(cube + ".bzSphereRadius"), 3.0, places=4)  # largest absolute coordinate (export_vdf.py:62-79)

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
        cmds.deleteAttr(part + ".bzFaceData")  # per-face words from the file win over the part defaults, as in Blender
        cmds.select(part, replace=True)
        out = os.path.join(folder, "out.geo")
        self.inject(menu.export_geo, out)
        back = vdf_io.read_geo(out).nodes[0]
        self.assertEqual(len(back.mesh.faces), 6)
        self.assertEqual((back.props["header_unknown"], back.props["face_xlu"]), (71, 2))
        cmds.file(new=True, force=True)
        with self.assertRaises(RuntimeError):  # nothing in the scene to export
            self.inject(menu.export_geo, os.path.join(folder, "none.geo"))


class ReduxExportTests(MayaCase):
    """Blender's export modes, options, materials and textures through the Maya layer."""

    def vehicle(self, prefix="tst"):
        created = maya_io.create_vehicle_geo_set(prefix)
        cmds.select(clear=True)
        return created

    def test_validation_blocks_on_errors_and_only_warns_otherwise(self):
        maya_io.BLOCK_ON_ERRORS = True
        cube = cmds.polyCube(name="tst11bod")[0]
        attributes.ensure_part(cube)
        cmds.select(clear=True)
        out = os.path.join(self.tmp, "blocked.vdf")
        with self.assertRaises(RuntimeError) as caught:  # a vehicle without a POV is an error
            self.inject(menu.export_vdf, out)
        self.assertIn("VDF export blocked by 1 validation errors", str(caught.exception))
        self.assertFalse(os.path.exists(out))
        # add the POV: only warnings remain (no inner_col / outer_col), and those never block
        pov = cmds.spaceLocator(name="tst11pov")[0]
        attributes.ensure_part(pov)
        cmds.setAttr(pov + ".bzGEOType", 40)
        cmds.select(clear=True)
        self.inject(menu.export_vdf, out)
        self.assertTrue(os.path.isfile(out))
        self.assertGreater(validate.counts(maya_io.LAST_ISSUES, "VDF")["WARNING"], 0)
        # GEO: no mesh to export is the validator's error
        cmds.file(new=True, force=True)
        cmds.polyCube(name="tst11bod")
        cmds.select(clear=True)
        geo = os.path.join(self.tmp, "ok.geo")
        self.inject(menu.export_geo, geo)
        self.assertTrue(os.path.isfile(geo))

    def test_the_three_export_modes(self):
        self.vehicle()
        legacy = os.path.join(self.tmp, "legacy", "tst.vdf")
        both = os.path.join(self.tmp, "both", "tst.vdf")
        for path in (legacy, both):
            os.makedirs(os.path.dirname(path))
        # Legacy only
        result = maya_io.export_legacy("VDF", legacy, {})
        self.assertIsNone(result["redux"])
        self.assertEqual(sorted(f for f in os.listdir(os.path.dirname(legacy)) if f.endswith(".mesh")), [])
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(legacy), "tst11bod.geo")))
        # Legacy + Redux: the toolkit's auto-port runs on the exported files
        result = maya_io.export_legacy("VDF", both, {"auto_port_ogre": True, "ogre_suffix": "_bz"})
        self.assertEqual(result["redux"], {"FINISHED"})
        produced = sorted(os.listdir(os.path.dirname(both)))
        self.assertIn("tst.vdf", produced)
        self.assertIn("tst.mesh", produced)
        self.assertIn("tst.skeleton", produced)
        self.assertTrue(any(f.endswith("_bz.material") for f in produced), produced)
        # Redux only: no legacy files at all
        redux_dir = os.path.join(self.tmp, "redux")
        os.makedirs(redux_dir)
        maya_io.export_redux_mesh(os.path.join(redux_dir, "tank.mesh"), {})
        self.assertEqual(sorted(os.listdir(redux_dir)), ["tank.material", "tank.mesh"])
        # the dialog's session drives the same three paths
        from bz98maya import export_dialog
        from bz98maya.core import export_options as eo

        session = export_dialog.new_session("VDF", preset_dir=os.path.join(self.tmp, "presets"))
        session.set_mode(eo.MODE_REDUX)
        session.export(os.path.join(redux_dir, "via_session.mesh"))
        self.assertTrue(os.path.isfile(os.path.join(redux_dir, "via_session.mesh")))
        session.set_mode(eo.MODE_LEGACY)
        session.set_option("ExportVDFOnly", True)
        sub = os.path.join(self.tmp, "vdf_only")
        os.makedirs(sub)
        session.export(os.path.join(sub, "tst.vdf"))
        self.assertEqual(os.listdir(sub), ["tst.vdf"])  # "Skip GEO File Export"

    def test_face_plane_modes_reach_the_geo_files(self):
        from bz98tools.bzrmodelporter.bzgeo_serializer import GeoSerializer

        cube = cmds.polyCube(name="tst11bod")[0]
        pov = cmds.spaceLocator(name="tst11pov")[0]
        attributes.ensure_part(pov)
        cmds.setAttr(pov + ".bzGEOType", 40)
        cmds.select(clear=True)
        distances = {}
        for mode in ("CURRENT", "RECOMPUTE"):
            folder = os.path.join(self.tmp, mode)
            os.makedirs(folder)
            maya_io.export_legacy("VDF", os.path.join(folder, "t.vdf"), {"face_plane_mode": mode})
            with open(os.path.join(folder, "tst11bod.geo"), "rb") as handle:
                geo = GeoSerializer(handle).read()
            distances[mode] = {round(f.plane.distance, 4) for f in geo.face_list}
        self.assertEqual(distances["CURRENT"], {1.0})  # Blender's historical default: centre + d = 1
        self.assertNotEqual(distances["RECOMPUTE"], {1.0})

    def test_mesh_options_tangents_batch_and_materials(self):
        ship = cmds.polyCube(name="ship")[0]
        wing = cmds.polyPlane(name="wing", width=2, height=1, subdivisionsX=1, subdivisionsY=1)[0]
        cmds.setAttr(wing + ".translateY", 3)
        # a lambert with a colour and a file texture (the texture makes Blender's colour grey)
        shader = cmds.shadingNode("lambert", asShader=True, name="hull_mat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="hull_SG")
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
        cmds.setAttr(shader + ".color", 0.2, 0.4, 0.6, type="double3")
        cmds.sets(ship, edit=True, forceElement=sg)
        textured = cmds.shadingNode("lambert", asShader=True, name="skin_mat")
        sg2 = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="skin_SG")
        cmds.connectAttr(textured + ".outColor", sg2 + ".surfaceShader", force=True)
        file_node = cmds.shadingNode("file", asTexture=True, name="skin_tex")
        texture = os.path.join(self.tmp, "skin_d.png")
        with open(texture, "wb") as handle:
            handle.write(b"png")
        cmds.setAttr(file_node + ".fileTextureName", texture, type="string")
        cmds.connectAttr(file_node + ".outColor", textured + ".color", force=True)
        cmds.sets(wing, edit=True, forceElement=sg2)
        cmds.select(clear=True)

        out_dir = os.path.join(self.tmp, "m")
        os.makedirs(out_dir)
        mesh_path = os.path.join(out_dir, "model.mesh")
        from bz98maya.core import material as material_core

        for mat_name, tex in (("hull", None), ("skin_d", "skin_d.png")):  # the derived maps exist, so Blender's text is written
            for derived in material_core.derive_map_names(mat_name, tex):
                open(os.path.join(out_dir, derived), "wb").close()
        maya_io.export_redux_mesh(mesh_path, {"copy_textures": True})
        mats = maya_io.collect_materials(maya_io.collect_scene())
        np.testing.assert_allclose(mats["hull"]["diffuse"], [0.2, 0.4, 0.6], atol=1e-6)
        self.assertEqual(mats["skin_d"]["texture"], "skin_d.png")
        # the text equals what Blender's xSaveMaterialData writes for the same material data
        from test_maya_core import _load_blender_ogre_export

        golden_dir = os.path.join(self.tmp, "golden")
        os.makedirs(golden_dir)
        _load_blender_ogre_export().xSaveMaterialData(os.path.join(golden_dir, "model.mesh"), {"materials": mats}, True, False)
        with open(os.path.join(golden_dir, "model.material"), encoding="utf-8") as a, open(
            os.path.join(out_dir, "model.material"), encoding="utf-8"
        ) as b:
            self.assertEqual(b.read(), a.read())
        text = open(os.path.join(out_dir, "model.material"), encoding="utf-8").read()
        self.assertIn("material hull : BZBase", text)
        self.assertIn("set_texture_alias DiffuseMap skin_d.dds", text)
        self.assertIn('set $diffuse "0.2 0.4 0.6"', text)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "skin_d.png")))  # Copy Textures

        decoded = _decode_mesh(mesh_path)
        self.assertEqual(sorted(d["material"] for d in decoded), ["hull", "skin_d"])
        for d in decoded:  # default options: BZR layout with tangents and binormals, V flipped
            self.assertIn(("TANGENT", 0), d["values"])
            self.assertIn(("BINORMAL", 0), d["values"])
            tangent = d["values"][("TANGENT", 0)]
            normal = d["values"][("NORMAL", 0)]
            np.testing.assert_allclose(np.abs(np.sum(tangent[:, :3] * normal, axis=1)), 0.0, atol=1e-4)
            np.testing.assert_allclose(np.linalg.norm(tangent[:, :3], axis=1), 1.0, atol=1e-4)
            self.assertTrue(set(np.round(tangent[:, 3])) <= {-1.0, 1.0})
        maya_io.export_redux_mesh(mesh_path, {"export_tangents": False, "export_materials": False})
        self.assertEqual([d[0] for d in _decode_mesh(mesh_path)[0]["decl"]], ["POSITION", "NORMAL", "TEXCOORD"])
        # batch: one .mesh per object, named after it
        cmds.select([ship, wing], replace=True)
        batch_dir = os.path.join(self.tmp, "batch")
        os.makedirs(batch_dir)
        maya_io.export_redux_mesh(os.path.join(batch_dir, "ignored.mesh"), {"batch_export": True})
        self.assertEqual(sorted(f for f in os.listdir(batch_dir) if f.endswith(".mesh")), ["ship.mesh", "wing.mesh"])

    def test_mesh_import_picks_up_the_sibling_material_and_textures(self):
        from bz98maya.core import material

        folder = os.path.join(self.tmp, "imp")
        os.makedirs(folder)
        scene_mesh = vdf_io.box_mesh((0, 0, 0), (1, 1, 1))
        scene_mesh.face_materials = ["hull_trim", "hull_trim", "hull_trim", "plain", "plain", "plain"]
        scene_mesh.normals = vdf_io.with_normals(scene_mesh)
        scene_mesh.uv_sets = [[(0, 0)] * scene_mesh.loop_count()]
        from bz98maya.core.model import Node, Scene

        mesh_path = os.path.join(folder, "thing.mesh")
        ogre_io.write_ogre_mesh(Scene(nodes=[Node("thing", None, axes.identity4(), "part", scene_mesh)]), mesh_path,
                                {"export_materials": False})
        with open(os.path.join(folder, "thing.material"), "w", encoding="utf-8") as handle:
            handle.write(material.material_text({
                "hull_trim": {"diffuse": [0.3, 0.6, 0.9], "texture": "hull_trim_d.dds"},
                "plain": {"diffuse": [0.3, 0.6, 0.9]},
            }))
        texture = os.path.join(folder, "HULL_TRIM_D.png")
        with open(texture, "wb") as handle:
            handle.write(b"png")
        root = maya_io.import_ogre_mesh(mesh_path)
        groups = {}  # one submesh (node) per material
        for part in maya_parts(root).values():
            for g in shading_groups(mesh_shape(part)) - {"initialShadingGroup"}:
                groups[maya_io._material_name_of(g)] = g
        self.assertEqual(set(groups), {"hull_trim", "plain"})
        trim = cmds.listConnections(groups["hull_trim"] + ".surfaceShader")[0]
        files = cmds.listConnections(trim + ".color", type="file")  # the texture found beside the mesh
        self.assertEqual(os.path.normcase(cmds.getAttr(files[0] + ".fileTextureName")), os.path.normcase(texture))
        plain = cmds.listConnections(groups["plain"] + ".surfaceShader")[0]  # no texture: the .material diffuse
        np.testing.assert_allclose(cmds.getAttr(plain + ".color")[0], (0.3, 0.6, 0.9), atol=1e-5)
        self.assertFalse(cmds.listConnections(plain + ".color", type="file"))

    def test_legacy_import_loads_stock_map_textures_from_the_folder_the_user_names(self):
        folder = os.path.join(self.tmp, "src")
        os.makedirs(folder)
        shutil.copy(os.path.join(_HERE, "fixtures", "eyepoint.vdf"), folder)
        _write_cube_geo(os.path.join(folder, "fxh11.geo"), "fxh11")
        textures = os.path.join(self.tmp, "textures")
        os.makedirs(textures)
        with open(os.path.join(textures, "tex_a.map"), "wb") as handle:
            handle.write(struct.pack("<4H", 4, 0, 2, 0) + bytes(range(8)))
        path = os.path.join(folder, "eyepoint.vdf")
        root = maya_io.import_vdf(path, {"MapTextureDirectory": textures})
        part = maya_parts(root)["fxh11"]
        sg = [g for g in shading_groups(mesh_shape(part)) if maya_io._material_name_of(g) == "tex_a"][0]
        shader = cmds.listConnections(sg + ".surfaceShader")[0]
        files = cmds.listConnections(shader + ".color", type="file")
        converted = cmds.getAttr(files[0] + ".fileTextureName")
        self.assertTrue(converted.lower().endswith("tex_a.png") and os.path.isfile(converted))
        cmds.file(new=True, force=True)
        root = maya_io.import_vdf(path, {"ImportMapTextures": False})
        sg = [g for g in shading_groups(mesh_shape(maya_parts(root)["fxh11"])) if maya_io._material_name_of(g) == "tex_a"][0]
        self.assertFalse(cmds.listConnections(cmds.listConnections(sg + ".surfaceShader")[0] + ".color", type="file"))

    def test_stock_meshes_export_like_the_blender_path(self):
        files = _stock("mesh")
        if not files:
            self.skipTest("BZ98_STOCK_DIR not set")
        from bz98tools.ogrefast.pure import import_compat
        from test_maya_core import _reference_mesh_bytes

        for source in files:
            with self.subTest(mesh=os.path.basename(source)):
                cmds.file(new=True, force=True)
                folder = os.path.dirname(source)
                importer = import_compat.KenshiObjectSerializer()
                importer.add_resource_location(folder)
                specs = []
                for sub in importer.load_mesh(os.path.basename(source)).get_submeshes():
                    loops = list(sub.faces)
                    uvs = np.asarray(sub.get_texcoords(), dtype=np.float32)
                    specs.append(dict(
                        name=sub.submesh_name, material=sub.material,
                        positions=np.asarray(sub.get_positions(), dtype=np.float32).reshape(-1, 3), loops=loops,
                        normals=np.asarray(sub.get_normals(), dtype=np.float32).reshape(-1, 3),
                        uvs=uvs.reshape(len(uvs), len(loops), 2) if uvs.ndim == 2 and uvs.size else np.empty(2),
                    ))
                reference = os.path.join(self.tmp, "ref_" + os.path.basename(source))
                with open(reference, "wb") as handle:
                    handle.write(_reference_mesh_bytes(specs))
                maya_io.import_ogre_mesh(source)
                cmds.select(clear=True)
                out = os.path.join(self.tmp, "maya_" + os.path.basename(source))
                maya_io.export_redux_mesh(out, {"export_tangents": False, "export_materials": False})
                got, want = _decode_mesh(out), _decode_mesh(reference)
                self.assertEqual(len(got), len(want))
                for a, b in zip(got, want):
                    self.assertEqual((a["material"], a["decl"], a["strides"]), (b["material"], b["decl"], b["strides"]))
                    self.assertEqual((a["count"], len(a["faces"])), (b["count"], len(b["faces"])))
                    for key in b["values"]:
                        np.testing.assert_allclose(a["values"][key][a["faces"]], b["values"][key][b["faces"]], atol=1e-4,
                                                   err_msg=str(key))

    def test_export_dialog_builds_and_presets_persist_in_standalone(self):
        from bz98maya import export_dialog

        with self.assertRaises(RuntimeError):
            export_dialog.show_dialog()  # needs the Maya UI
        preset_dir = os.path.join(self.tmp, "presets")
        session = export_dialog.new_session("SDF", preset_dir)
        session.set_option("ogre_suffix", "_sdf")
        session.save_preset("sdf one")
        self.assertEqual(export_dialog.new_session("SDF", preset_dir).custom_presets(), ["sdf one"])
        try:
            cmds.window("bzExportTestWindow")
            cmds.columnLayout()
        except RuntimeError as exc:
            self.skipTest("UI layouts are unavailable in standalone: %s" % exc)
        for kind in ("VDF", "SDF", "GEO", "MESH"):
            session = export_dialog.new_session(kind, preset_dir)
            session.options["auto_port_ogre"] = kind != "MESH"
            export_dialog._build_form(session, lambda *_a: None)  # builds every control without error


class StockPortTests(MayaCase):
    def port_summary(self, legacy_path, dest):
        from bz98maya.core import redux
        from bz98tools.bzrmodelporter.ogremesh_serializer import MeshSerializer

        os.makedirs(dest, exist_ok=True)
        result = redux.auto_port(legacy_path, {"ogre_dest_dir": dest, "ogre_skip_unchanged": False})
        stem = os.path.splitext(os.path.basename(legacy_path))[0]
        mesh_path = os.path.join(dest, stem + ".mesh")
        if result != {"FINISHED"} or not os.path.isfile(mesh_path):
            return None
        with open(mesh_path, "rb") as handle:
            mesh = MeshSerializer(handle).read()
        materials = sorted(os.path.splitext(f)[0] for f in os.listdir(dest) if f.endswith(".material"))
        return {
            "submeshes": len(mesh.submesh_list),
            "vertices": sorted(sm.vertex_data.vertex_count if sm.vertex_data else 0 for sm in mesh.submesh_list),
            "indices": sorted(len(sm.get_index_array()) for sm in mesh.submesh_list),
            "skeleton": mesh.skeleton_name,
            "materials": materials,
        }

    def test_legacy_plus_redux_of_stock_models_matches_porting_the_source(self):
        files = _stock("vdf") + _stock("sdf")
        if not files:
            self.skipTest("BZ98_STOCK_DIR not set")
        compared = 0
        for path in files:
            kind = "vdf" if path.endswith(".vdf") else "sdf"
            with self.subTest(model=os.path.basename(path)):
                cmds.file(new=True, force=True)
                source = self.port_summary(path, os.path.join(self.tmp, "src_" + os.path.basename(path)))
                if source is None:
                    self.skipTest("the porter does not accept this stock model as is")
                (maya_io.import_vdf if kind == "vdf" else maya_io.import_sdf)(path)
                cmds.select(clear=True)
                folder = os.path.join(self.tmp, "maya_" + os.path.basename(path))
                os.makedirs(folder)
                out = os.path.join(folder, os.path.basename(path))
                result = maya_io.export_legacy(
                    kind.upper(), out, {"auto_port_ogre": True, "ogre_skip_unchanged": False}
                )
                self.assertEqual(result["redux"], {"FINISHED"})
                maya = self.port_summary(out, folder)
                self.assertEqual(maya["submeshes"], source["submeshes"])
                # Same triangles. The porter keeps one vertex per GEO (position, normal) index pair, so
                # stock GEOs that repeat equal normals under different indices give it a few more
                # vertices than the welded GEO the Maya export writes; never fewer triangles.
                self.assertEqual(maya["indices"], source["indices"])
                for got, want in zip(maya["vertices"], source["vertices"]):
                    self.assertLessEqual(got, want)
                    self.assertGreaterEqual(got, 0.5 * want)
                self.assertEqual(maya["skeleton"], source["skeleton"])
                self.assertEqual(len(maya["materials"]), len(source["materials"]))
                compared += 1
        self.assertGreater(compared, 0)


class RigAndAnimationTests(MayaCase):
    def _skinned_mesh(self):
        from bz98maya.core import redux
        from bz98maya.core.model import KIND_PART, Node, Scene

        cube = vdf_io.box_mesh((-1, 0, -1), (1, 2, 1))
        cube.face_materials = ["tex_a"] * 6
        cube.weights = [[(0, 0.7), (1, 0.3)] if p[1] < 1 else [(1, 1.0)] for p in cube.positions]
        scene = Scene(nodes=[Node("rig", None, axes.identity4(), KIND_PART, cube, {})])
        scene.skeleton = _two_bone_skeleton()
        path = os.path.join(self.tmp, "rig.mesh")
        redux.write_bzr_mesh(scene, path, {"export_skeleton": True, "export_animation": True,
                                           "export_materials": False})
        return path, scene

    def test_skinned_mesh_import_builds_joints_skin_and_clips_and_exports_back(self):
        path, _source = self._skinned_mesh()
        self.inject(menu.import_mesh, path)
        self.assertEqual(len(cmds.ls(type="joint", long=True)), 2)
        self.assertEqual(len(cmds.ls(type="skinCluster")), 1)
        out_dir = os.path.join(self.tmp, "out")
        os.makedirs(out_dir)
        out = os.path.join(out_dir, "rig.mesh")
        cmds.select(clear=True)
        maya_io.export_redux_mesh(out, {"export_skeleton": True, "export_animation": True,
                                        "export_materials": False})
        back = ogre_io.read_ogre_mesh(out, import_materials=False)
        want = ogre_io.read_ogre_mesh(path, import_materials=False).skeleton
        self.assertEqual(compare_skeletons(want, back.skeleton), [])
        weights = back.nodes[0].mesh.weights
        self.assertEqual(len(weights), len(back.nodes[0].mesh.positions))
        for influences in weights:
            self.assertAlmostEqual(sum(w for _b, w in influences), 1.0, places=4)
        out2 = os.path.join(out_dir, "noclip.mesh")
        maya_io.export_redux_mesh(out2, {"export_skeleton": True, "export_animation": False,
                                         "export_materials": False})
        self.assertEqual(ogre_io.read_ogre_mesh(out2, import_materials=False).skeleton.clips, [])

    def test_four_influences_are_cut_to_three_on_export(self):
        path, _source = self._skinned_mesh()
        self.inject(menu.import_mesh, path)
        cluster = cmds.ls(type="skinCluster")[0]
        shape = cmds.skinCluster(cluster, query=True, geometry=True)[0]
        extra = [cmds.joint(name="extra1"), cmds.joint(name="extra2")]
        cmds.skinCluster(cluster, edit=True, addInfluence=extra, weight=0.0)
        influences = cmds.skinCluster(cluster, query=True, influence=True)
        self.assertEqual(len(influences), 4)
        cmds.setAttr(cluster + ".normalizeWeights", 0)
        cmds.skinPercent(cluster, shape + ".vtx[0]", normalize=False,
                         transformValue=list(zip(influences, (0.4, 0.3, 0.2, 0.1))))
        cmds.select(clear=True)
        scene = maya_io.collect_scene(triangulate=True)
        four = scene.nodes[0].mesh.weights[0]
        self.assertEqual(len([1 for _b, w in four if w > 1e-6]), 4)
        from bz98maya.core import redux

        cut = redux.bone_assignments([four], {b: b for b, _w in four}, True)
        self.assertEqual(len(cut), 3)
        self.assertAlmostEqual(sum(w for _v, _b, w in cut), 1.0, places=5)

    def test_a_rig_built_in_maya_without_tags_exports(self):
        cmds.select(clear=True)
        root = cmds.joint(name="root", position=(0, 0, 0))
        tip = cmds.joint(name="tip", position=(0, 2, 0))
        cmds.select(clear=True)
        cube = cmds.polyCube(name="body", width=1, height=2, depth=1)[0]
        cmds.move(0, 1, 0, cube)
        cmds.makeIdentity(cube, apply=True, translate=True)
        cmds.skinCluster(root, tip, cube, toSelectedBones=True, maximumInfluences=2)
        cmds.setKeyframe(tip, attribute="rotateZ", time=0, value=0)
        cmds.setKeyframe(tip, attribute="rotateZ", time=30, value=45)
        cmds.select(clear=True)
        out = os.path.join(self.tmp, "mine.mesh")
        maya_io.export_redux_mesh(out, {"export_skeleton": True, "export_animation": True, "export_materials": False})
        back = ogre_io.read_ogre_mesh(out, import_materials=False)
        self.assertEqual(sorted(b.name for b in back.skeleton.bones), ["root", "tip"])
        self.assertEqual(sorted(b.id for b in back.skeleton.bones), [0, 1])
        self.assertEqual(len(back.skeleton.clips), 1)  # one default take for keys without a clip table
        self.assertEqual(len(back.nodes[0].mesh.weights), len(back.nodes[0].mesh.positions))

    def test_standalone_skeleton_import_export_roundtrip(self):
        from bz98maya.core import skeleton_io

        src = os.path.join(self.tmp, "solo.skeleton")
        skeleton_io.write_skeleton(_two_bone_skeleton(), src)
        self.inject(menu.import_skeleton, src)
        cmds.select(clear=True)
        out = os.path.join(self.tmp, "solo_out.skeleton")
        self.inject(menu.export_skeleton, out)
        self.assertEqual(compare_skeletons(skeleton_io.read_skeleton(src), skeleton_io.read_skeleton(out)), [])

    def _import_anim_vdf(self):
        path = os.path.join(self.tmp, "av.vdf")
        vdf_io.write_vdf(_vehicle_scene_with_anim(), path)
        self.inject(menu.import_vdf, path)
        return path

    def test_vdf_anim_import_keys_the_part_and_export_writes_them_back(self):
        path = self._import_anim_vdf()
        turret = cmds.ls("abc11ty1", long=True)[0]
        self.assertGreater(cmds.keyframe(turret, query=True, keyframeCount=True), 0)
        self.assertEqual(sorted(set(cmds.keyframe(turret + ".translateX", query=True, timeChange=True))), [0, 10, 20])
        self.assertAlmostEqual(cmds.getAttr(turret + ".translateX", time=10), 0.5, places=4)
        self.assertEqual(cmds.playbackOptions(query=True, maxTime=True), 20)
        keys = legacy_anim.collect_keys(turret)
        self.assertEqual([f for f, _q in keys["rot"]], [0, 10, 20])
        out = os.path.join(self.tmp, "av_out.vdf")
        cmds.select(clear=True)
        self.inject(menu.export_vdf, out)
        back = vdf_io.read_vdf(out)
        node = {n.name: n for n in back.nodes}["abc11ty1"]
        np.testing.assert_allclose(node.anim["pos"][1][1], (0.5, 1.5, 0.0), atol=1e-3)
        want = {n.name: n for n in vdf_io.read_vdf(path).nodes}["abc11ty1"].anim
        for (f0, q0), (f1, q1) in zip(want["rot"], node.anim["rot"]):
            self.assertEqual(f0, f1)
            self.assertAlmostEqual(abs(sum(a * b for a, b in zip(q0, q1))), 1.0, places=4)
        self.assertEqual(back.meta["animations"][0]["length"], 20)
        with open(out, "rb") as handle:
            self.assertTrue(vdf_file.parse_vdf(handle.read()).anim_present)

    def test_sdf_anim_roundtrip_through_maya(self):
        path = os.path.join(self.tmp, "as.sdf")
        sdf_io.write_sdf(_vehicle_scene_with_anim(), path)
        self.inject(menu.import_sdf, path)
        turret = cmds.ls("abc11ty1", long=True)[0]
        self.assertGreater(cmds.keyframe(turret, query=True, keyframeCount=True), 0)
        out = os.path.join(self.tmp, "as_out.sdf")
        cmds.select(clear=True)
        self.inject(menu.export_sdf, out)
        node = {n.name: n for n in sdf_io.read_sdf(out).nodes}["abc11ty1"]
        self.assertEqual([f for f, _p in node.anim["pos"]], [0, 10, 20])

    def test_export_animations_option_off_drops_the_block(self):
        self._import_anim_vdf()
        out = os.path.join(self.tmp, "noanim.vdf")
        cmds.select(clear=True)
        maya_io.export_vdf(out, {"ExportAnimations": False})
        with open(out, "rb") as handle:
            self.assertFalse(vdf_file.parse_vdf(handle.read()).anim_present)

    def test_slot_tools_presets_duplicate_move_remove(self):
        model = panel.create_model_node("bz_anim_model")
        animation_tools.add_preset(model, "DEPLOY_PAIR")
        self.assertEqual([s["index"] for s in animation_tools.slots(model)], [0, 1])
        animation_tools.duplicate_slot(model, 0)
        self.assertEqual([s["index"] for s in animation_tools.slots(model)], [0, 0, 1])
        self.assertEqual(animation_tools.move_slot(model, 0, "DOWN"), 1)
        animation_tools.update_slot(model, 1, length=12, speed=20.0)
        self.assertEqual(animation_tools.slots(model)[1]["length"], 12)
        animation_tools.remove_slot(model, 0)
        self.assertEqual(len(animation_tools.slots(model)), 2)

    def test_mirror_keys_copies_motion_onto_the_counterpart(self):
        self._import_anim_vdf()
        source = cmds.ls("abc11ty1", long=True)[0]
        target = cmds.polyCube(name="abc11ty2")[0]
        cmds.parent(target, cmds.listRelatives(source, parent=True, fullPath=True)[0])
        cmds.setAttr(target + ".translate", 0, 1.5, 0)
        attributes.ensure_part(target)
        written = animation_tools.mirror_keys("ty1", "ty2", "X", ("pos", "rot"), use_selection=False)
        self.assertEqual(written, 1)
        mirrored = legacy_anim.collect_keys(target)
        self.assertEqual([f for f, _p in mirrored["pos"]], [0, 10, 20])
        self.assertAlmostEqual(mirrored["pos"][1][1][1], 1.5, places=3)

    def test_animation_validation_runs_on_the_maya_scene(self):
        self._import_anim_vdf()
        cmds.select(clear=True)
        issues = maya_io.validate_scene("VDF")
        self.assertFalse(any(i["scope"] == "Animation" and i["severity"] == "ERROR" for i in issues))
        animation_tools.set_slots(panel.model_node(None), [])
        issues = maya_io.validate_scene("VDF")
        self.assertTrue(any("no ANIM sequence entries" in i["message"] for i in issues), issues)

    def test_stock_clip_table_and_auto_map(self):
        from bz98maya.core import anim, skeleton_io

        path = os.path.join(self.tmp, "t.skeleton")
        skeleton_io.write_skeleton(_two_bone_skeleton(), path)
        table = anim.stock_clip_table(path)
        self.assertEqual([(n, c) for n, _d, c in table], [("walk", 1)])
        mapped = anim.auto_map(["walk", "idleEject"], ["Walk.001", "idleElect"])
        self.assertEqual(mapped, {"walk": "Walk.001", "idleEject": "idleElect"})


class StockQuirkTests(MayaCase):
    def test_parent_cycle_in_a_file_imports_without_the_loop(self):
        from bz98maya.core.model import KIND_PART, Node, Scene

        cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
        cube.face_materials = ["tex_a"] * 6
        a = Node("abc11bda", "abc11bdb", axes.identity4(), KIND_PART, cube, {"type": 60})
        b = Node("abc11bdb", "abc11bda", axes.identity4(), KIND_PART, cube, {"type": 60})
        root = maya_io.build_scene(Scene(nodes=[a, b]), "cyc")
        self.assertEqual(len(cmds.listRelatives(root, allDescendents=True, type="transform") or []), 2)

    def test_nul_in_a_string_field_and_out_of_range_numbers_are_tolerated(self):
        node = panel.create_model_node("bz_quirk")
        attributes.write_model(node, {"name": "ok", "explosioneffect": "null\x00\x00garbage"})
        self.assertEqual(attributes.read_model(node)["explosioneffect"], "null")

    def test_per_face_geo_words_ride_on_the_part(self):
        from bz98maya.core.model import KIND_PART, Node, Scene

        cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
        cube.face_materials = ["tex_a"] * 6
        cube.face_data = {"unknown": [0] * 6, "shade": [4] * 6, "texture": [1] * 6, "xlu": [0] * 6,
                          "parent": [-1, 0, 1, 2, 3, 4], "node": [0] * 6}
        root = maya_io.build_scene(Scene(nodes=[Node("abc11bdy", None, axes.identity4(), KIND_PART, cube,
                                                      {"type": 60})]), "pf")
        cmds.select(root, replace=True)
        out = os.path.join(self.tmp, "pf.vdf")
        maya_io.export_vdf(out, {})
        back = {n.name: n for n in vdf_io.read_vdf(out).nodes}["abc11bdy"].mesh.face_data
        self.assertEqual(back["parent"], [-1, 0, 1, 2, 3, 4])


class ReviewFixMayaTests(MayaCase):
    def test_batch_redux_export_of_a_parented_skinned_mesh_keeps_parent_and_skeleton(self):
        rig = RigAndAnimationTests("test_standalone_skeleton_import_export_roundtrip")
        rig.tmp = self.tmp
        path, _scene = rig._skinned_mesh()
        self.inject(menu.import_mesh, path)
        body = [t for t in cmds.ls(type="transform", long=True) if t.endswith("|rig")][0]
        holder = cmds.polyCube(name="holder")[0]
        body = cmds.parent(body, holder)[0]
        cmds.setAttr(holder + ".translateX", 5)
        cmds.select(clear=True)
        out_dir = os.path.join(self.tmp, "batch")
        os.makedirs(out_dir)
        maya_io.export_redux_mesh(os.path.join(out_dir, "all.mesh"),
                                  {"batch_export": True, "export_skeleton": True, "export_materials": False})
        self.assertTrue(os.path.exists(os.path.join(out_dir, "rig.skeleton")))
        back = ogre_io.read_ogre_mesh(os.path.join(out_dir, "rig.mesh"), import_materials=False)
        self.assertAlmostEqual(min(p[0] for p in back.nodes[0].mesh.positions), 4.0, places=3)  # parent's +5 baked in
        self.assertTrue(any(back.nodes[0].mesh.weights))

    def test_shader_colour_and_explicit_texture_name_reach_the_geo(self):
        cube = cmds.polyCube(name="tst11bdy")[0]
        shader = cmds.shadingNode("lambert", asShader=True, name="paint_mat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="paint_SG")
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
        cmds.setAttr(shader + ".color", 0.5, 0.25, 1.0, type="double3")
        cmds.addAttr(shader, longName="bzMaterial", dataType="string")
        cmds.setAttr(shader + ".bzMaterial", "AVDEF4.MAP", type="string")
        cmds.sets(cube, edit=True, forceElement=sg)
        cmds.select(clear=True)
        out = os.path.join(self.tmp, "col.vdf")
        self.inject(menu.export_vdf, out)
        geo = vdf_io.read_geo(os.path.join(self.tmp, "tst11bdy.geo"))
        from bz98tools.bzrmodelporter.bzgeo_serializer import GeoSerializer

        with open(os.path.join(self.tmp, "tst11bdy.geo"), "rb") as handle:
            faces = GeoSerializer(handle).read().face_list
        self.assertEqual({(f.color.r, f.color.g, f.color.b) for f in faces}, {(127, 63, 255)})
        self.assertEqual({f.texture_name for f in faces}, {"AVDEF4.MAP"})
        self.assertTrue(geo.nodes)

    def test_skinned_mesh_exported_without_its_joints_warns(self):
        rig = RigAndAnimationTests("test_standalone_skeleton_import_export_roundtrip")
        rig.tmp = self.tmp
        path, _scene = rig._skinned_mesh()
        self.inject(menu.import_mesh, path)
        body = [t for t in cmds.ls(type="transform", long=True) if t.endswith("|rig")][0]
        cmds.select(body, replace=True)  # joints are not selected
        warnings = maya_io.export_redux_mesh(os.path.join(self.tmp, "nojoints.mesh"),
                                             {"export_skeleton": True, "export_materials": False})
        self.assertTrue(any("skin" in w and "rig" in w for w in warnings), warnings)


class SourceMaterialMayaTests(MayaCase):
    def _import_stock(self, text, face_materials):
        from bz98maya.core import redux
        from bz98maya.core.model import KIND_PART, Node, Scene

        cube = vdf_io.box_mesh((-1, -1, -1), (1, 1, 1))
        cube.face_materials = face_materials
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        redux.write_bzr_mesh(Scene(nodes=[Node("m", None, axes.identity4(), KIND_PART, cube, {})]),
                             os.path.join(src, "m.mesh"), {"export_materials": False})
        with open(os.path.join(src, "m.material"), "w", encoding="utf-8") as handle:
            handle.write(text)
        self.inject(menu.import_mesh, os.path.join(src, "m.mesh"))

    def _export(self):
        dst = os.path.join(self.tmp, "dst")
        os.makedirs(dst, exist_ok=True)
        cmds.select(clear=True)
        warnings = maya_io.export_redux_mesh(os.path.join(dst, "m.mesh"), {"export_materials": True, "overwrite_material": True})
        with open(os.path.join(dst, "m.material"), encoding="utf-8") as handle:
            return handle.read(), warnings

    def test_stock_materials_round_trip_through_maya(self):
        from test_maya_core import STOCK_ABBARR_MATERIAL, STOCK_SVTANK_MATERIAL, _material_blocks

        self._import_stock(STOCK_SVTANK_MATERIAL, ["svtank00"] * 2 + ["svtank01"] * 2 + ["svheadlight00"] * 2)
        text, warnings = self._export()
        self.assertEqual(_material_blocks(text), _material_blocks(STOCK_SVTANK_MATERIAL))  # cockpit parent, exact names
        self.assertTrue(any("svheadlight00" in w for w in warnings), warnings)
        cmds.file(new=True, force=True)
        shutil.rmtree(os.path.join(self.tmp, "src"))
        shutil.rmtree(os.path.join(self.tmp, "dst"))
        self._import_stock(STOCK_ABBARR_MATERIAL, ["abbarr"] * 6)
        text, _ = self._export()
        self.assertEqual(_material_blocks(text), _material_blocks(STOCK_ABBARR_MATERIAL))

    def test_a_colour_changed_in_maya_changes_only_the_diffuse(self):
        from test_maya_core import STOCK_ABBARR_MATERIAL, _material_blocks

        self._import_stock(STOCK_ABBARR_MATERIAL, ["abbarr"] * 6)
        shader = cmds.ls("abbarr_mat")[0]
        cmds.setAttr(shader + ".color", 0.5, 0.25, 0.125, type="double3")
        text, _ = self._export()
        got, want = _material_blocks(text)["abbarr"], _material_blocks(STOCK_ABBARR_MATERIAL)["abbarr"]
        self.assertEqual(got[1], want[1])
        self.assertEqual(dict(got[2])["diffuse"], "0.5 0.25 0.125")
        self.assertEqual({k: v for k, v in got[2] if k != "diffuse"}, {k: v for k, v in want[2] if k != "diffuse"})


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
