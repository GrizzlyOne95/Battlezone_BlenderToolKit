# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Tests for the Maya release archive built by ``scripts/package_maya.py``.

    python tests/maya_package_test.py
    "C:\\Program Files\\Autodesk\\Maya2027\\bin\\mayapy.exe" tests/maya_package_test.py

Both build the archive and extract it into a temporary folder outside the
repository. Plain Python checks the layout and that the Maya-free core
imports from the extracted copy alone. Under mayapy a child mayapy (cwd outside
the repository, no BZ98TOOLKIT_PATH, MAYA_MODULE_PATH = the extracted folder,
a scratch MAYA_APP_DIR) loads the plug-in through Maya's module system and runs
a VDF and an Ogre mesh import + export through the menu handlers, asserting
that every ``bz98maya`` / ``bz98tools`` module came from the extracted copy.

Not named ``test_*.py``, so ``tests/run_tests.py`` does not collect it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import package_maya  # noqa: E402

try:
    import maya.standalone  # noqa: F401

    HAVE_MAYA = True
except ImportError:
    HAVE_MAYA = False

CHILD_TIMEOUT = 600


def _child_env(module_dir, app_dir):
    env = dict(os.environ)
    for name in ("BZ98TOOLKIT_PATH", "PYTHONPATH", "PYTHONHOME", "MAYA_PLUG_IN_PATH", "MAYA_SCRIPT_PATH"):
        env.pop(name, None)
    env["MAYA_MODULE_PATH"] = module_dir
    env["MAYA_APP_DIR"] = app_dir
    return env


CORE_CHILD = textwrap.dedent(
    """
    import os, sys
    root = sys.argv[1]
    sys.path.insert(0, root)  # what the .mod's PYTHONPATH line does
    from bz98maya.core import toolkit, schema, vdf_io, sdf_io, ogre_io, validate
    expected = os.path.normcase(os.path.join(root, "bz98tools"))
    assert os.path.normcase(toolkit.find_toolkit_dir()) == expected, toolkit.find_toolkit_dir()
    toolkit.ensure_toolkit(stub_ogrefast=True)
    from bz98tools.bzrmodelporter.bzgeo_serializer import GeoSerializer  # noqa: F401
    from bz98tools.ogrefast.pure.serializer import OgreMeshSerializer  # noqa: F401
    from bz98tools import validation, semantics  # noqa: F401
    assert schema is not None and vdf_io and sdf_io and ogre_io and validate
    bad = [(n, m.__file__) for n, m in sys.modules.items()
           if n.split(".")[0] in ("bz98maya", "bz98tools") and getattr(m, "__file__", None)
           and not os.path.normcase(os.path.abspath(m.__file__)).startswith(os.path.normcase(root) + os.sep)]
    assert not bad, bad
    print("CORE_OK")
    """
)

MAYA_CHILD = textwrap.dedent(
    """
    import os, sys, traceback
    root, work = sys.argv[1], sys.argv[2]
    try:
        import maya.standalone
        maya.standalone.initialize(name="python")
        import maya.cmds as cmds

        def under(path, base):
            return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(base) + os.sep)

        assert "bz98maya" in (cmds.moduleInfo(listModules=True) or []), cmds.moduleInfo(listModules=True)
        module_path = cmds.moduleInfo(path=True, moduleName="bz98maya")
        assert os.path.normcase(os.path.abspath(module_path)) == os.path.normcase(root), module_path
        cmds.loadPlugin("bz98maya_plugin.py", quiet=True)  # by name: found via the module's plug-ins folder
        assert cmds.pluginInfo("bz98maya_plugin", query=True, loaded=True)
        plugin_path = cmds.pluginInfo("bz98maya_plugin", query=True, path=True)
        assert under(plugin_path, root), plugin_path

        from bz98maya import maya_io, menu
        from bz98maya.core import ogre_io, toolkit, vdf_io
        menu.RAISE_ERRORS = True
        maya_io.BLOCK_ON_ERRORS = False  # the fixture is not a validator-clean vehicle

        def run(handler, path):
            original = menu._ask
            menu._ask = lambda *args, **kwargs: path
            try:
                handler()
            finally:
                menu._ask = original

        # VDF: import, export, read back.
        src_vdf = os.path.join(work, "src_vdf", "eyepoint.vdf")
        run(menu.import_vdf, src_vdf)
        root_node = cmds.ls(selection=True, long=True)[0]
        out_vdf = os.path.join(work, "out_vdf", "eyepoint.vdf")
        os.makedirs(os.path.dirname(out_vdf))
        cmds.select(root_node, replace=True)
        run(menu.export_vdf, out_vdf)
        src_scene, out_scene = vdf_io.read_vdf(src_vdf), vdf_io.read_vdf(out_vdf)
        assert len(out_scene.nodes) == len(src_scene.nodes), (len(out_scene.nodes), len(src_scene.nodes))
        assert os.path.isfile(os.path.join(work, "out_vdf", "fxh11.geo"))

        # Ogre mesh: import, export, read back.
        cmds.file(new=True, force=True)
        src_mesh = os.path.join(work, "synthetic.mesh")
        run(menu.import_mesh, src_mesh)
        mesh_root = cmds.ls(selection=True, long=True)[0]
        out_mesh = os.path.join(work, "out_synthetic.mesh")
        cmds.select(mesh_root, replace=True)
        run(menu.export_mesh, out_mesh)
        src_m, out_m = ogre_io.read_ogre_mesh(src_mesh), ogre_io.read_ogre_mesh(out_mesh)
        assert len(out_m.nodes) == len(src_m.nodes) == 2, (len(out_m.nodes), len(src_m.nodes))

        bundled = os.path.join(root, "bz98tools")
        assert os.path.normcase(toolkit.find_toolkit_dir()) == os.path.normcase(bundled), toolkit.find_toolkit_dir()
        assert os.path.normcase(list(sys.modules["bz98tools"].__path__)[0]) == os.path.normcase(bundled)
        loaded = [(n, getattr(m, "__file__", None)) for n, m in list(sys.modules.items())
                  if n.split(".")[0] in ("bz98maya", "bz98tools")]
        bad = [(n, f) for n, f in loaded if f and not under(f, root)]
        assert not bad, bad
        assert any(n.startswith("bz98tools.ogrefast.pure") for n, _ in loaded)
        assert any(n == "bz98tools.vdf_file" for n, _ in loaded)
        print("MAYA_PACKAGE_OK", len(loaded), "modules from", root)
        code = 0
    except BaseException:
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        # Without this, mayapy's exit counts as a crash and opens an Autodesk error-report dialog.
        maya.standalone.uninitialize()
    except Exception:
        pass
    os._exit(code)
    """
)


class MayaPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bz98maya_pkg_")
        cls.archive = package_maya.build("v9.8.7-rc", os.path.join(cls.tmp, "dist"))
        cls.modules_dir = os.path.join(cls.tmp, "modules")  # stands in for Documents/maya/modules
        with zipfile.ZipFile(cls.archive) as zf:
            cls.names = zf.namelist()
            zf.extractall(cls.modules_dir)
        cls.module_root = os.path.join(cls.modules_dir, "bz98maya")
        # An unrelated bz98tools folder beside the module must never be picked over the bundled copy.
        decoy = os.path.join(cls.modules_dir, "bz98tools", "bzrmodelporter")
        os.makedirs(decoy)
        with open(os.path.join(decoy, "bzgeo_serializer.py"), "w", encoding="utf-8") as handle:
            handle.write("raise ImportError('decoy bz98tools was imported')\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_temp_folder_is_outside_the_repository(self):
        repo = os.path.normcase(_REPO_ROOT) + os.sep
        self.assertFalse(os.path.normcase(self.tmp).startswith(repo))

    def test_layout_unzips_into_a_modules_folder(self):
        package_maya.check_archive(self.archive)
        self.assertEqual(sorted(set(os.listdir(self.modules_dir)) - {"bz98tools"}), ["bz98maya", "bz98maya.mod"])
        with open(os.path.join(self.modules_dir, "bz98maya.mod"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[0], "+ bz98maya 9.8.7 ./bz98maya")
        self.assertIn("PYTHONPATH +:= .", lines)
        for rel in ("plug-ins/bz98maya_plugin.py", "bz98maya/menu.py", "bz98tools/vdf_file.py", "README.md", "LICENSE"):
            self.assertTrue(os.path.isfile(os.path.join(self.module_root, rel)), rel)

    def test_bundle_drops_terrain_art_native_backend_and_bytecode(self):
        for name in self.names:
            segments = name.split("/")
            self.assertNotIn("map_assets", segments, name)
            self.assertNotIn("__pycache__", segments, name)
            self.assertNotIn("vendor", segments, name)
            if name.endswith((".pyd", ".exe", ".so", ".pyc", ".dll")):
                self.assertEqual(name, "bz98maya/bz98tools/lib/lzo_bridge.dll")
        self.assertLess(os.path.getsize(self.archive), 5 * 1024 * 1024)

    def test_module_version_follows_the_release_label(self):
        self.assertEqual(package_maya.module_version("v1.5.0"), "1.5.0")
        self.assertEqual(package_maya.module_version("v1.5.0-rc"), "1.5.0")
        self.assertIsNone(package_maya.module_version("ci"))
        self.assertIn("+ bz98maya 0.1.0 ./bz98maya", package_maya.module_description("ci"))

    def test_entries_have_fixed_date_and_mode(self):
        with zipfile.ZipFile(self.archive) as zf:
            for info in zf.infolist():
                self.assertEqual(info.date_time, package_maya.ZIP_DATE_TIME, info.filename)
                self.assertEqual(info.external_attr, 0o644 << 16, info.filename)
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED, info.filename)

    def test_repo_dev_module_file_is_unchanged(self):
        with open(os.path.join(_REPO_ROOT, "maya", "bz98maya.mod"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().split()[:4], ["+", "bz98maya", "0.1.0", "."])

    def test_core_imports_from_the_extracted_copy_alone(self):
        script = os.path.join(self.tmp, "core_child.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(CORE_CHILD)
        env = _child_env(self.modules_dir, os.path.join(self.tmp, "appdir"))
        result = subprocess.run(
            [sys.executable, script, self.module_root],
            cwd=self.tmp, env=env, capture_output=True, text=True, timeout=CHILD_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CORE_OK", result.stdout)

    @unittest.skipUnless(HAVE_MAYA, "maya is not importable; run under mayapy for the plug-in check")
    def test_extracted_module_loads_and_runs_vdf_and_mesh_in_maya(self):
        sys.path.insert(0, _HERE)
        sys.path.insert(0, os.path.join(_REPO_ROOT, "maya"))
        import _bootstrap  # noqa: F401  (repo toolkit, only to write the inputs in this process)
        from bz98maya.core import axes, ogre_io, vdf_io
        from bz98maya.core.model import Node, Scene
        from test_maya_core import _write_cube_geo

        work = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(work, "src_vdf"))
        shutil.copy(os.path.join(_HERE, "fixtures", "eyepoint.vdf"), os.path.join(work, "src_vdf"))
        _write_cube_geo(os.path.join(work, "src_vdf", "fxh11.geo"), "fxh11")
        quad = vdf_io.Mesh(
            positions=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            faces=[(0, 1, 2), (0, 2, 3)],
            face_materials=["mat_a", "mat_a"],
            normals=[(0, 0, 1)] * 6,
            uv_sets=[[(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)]],
        )
        tri = vdf_io.Mesh(
            positions=[(0, 0, 0), (1, 0, 0), (0, 0, 1)],
            faces=[(0, 2, 1)],
            face_materials=["mat_b"],
            normals=[(0, 1, 0)] * 3,
            uv_sets=[[(0, 0), (0, 1), (1, 0)]],
        )
        scene = Scene(nodes=[Node("quad", None, axes.identity4(), "part", quad),
                             Node("tri", None, axes.identity4(), "part", tri)])
        ogre_io.write_ogre_mesh(scene, os.path.join(work, "synthetic.mesh"))

        script = os.path.join(self.tmp, "maya_child.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(MAYA_CHILD)
        env = _child_env(self.modules_dir, os.path.join(self.tmp, "appdir"))
        result = subprocess.run(
            [sys.executable, script, self.module_root, work],
            cwd=self.tmp, env=env, capture_output=True, text=True, timeout=CHILD_TIMEOUT,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output[-6000:])
        self.assertIn("MAYA_PACKAGE_OK", result.stdout, output[-6000:])
        print("\n" + [line for line in result.stdout.splitlines() if "MAYA_PACKAGE_OK" in line][0])


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MayaPackageTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.stdout.flush()
    sys.stderr.flush()
    code = 0 if result.wasSuccessful() else 1
    if HAVE_MAYA:
        os._exit(code)  # the parent never initialised Maya, but mayapy can still hang on exit
    return code


if __name__ == "__main__":
    sys.exit(main())
