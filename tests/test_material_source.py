"""bpy-free tests for the Blender add-on's .material source round trip."""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _blender_export():
    stubs = {}
    for name in ("bpy", "mathutils"):
        if name not in sys.modules:
            stubs[name] = sys.modules[name] = types.ModuleType(name)
    if "mathutils" in stubs:
        for cls in ("Vector", "Matrix", "Quaternion"):
            setattr(stubs["mathutils"], cls, type(cls, (), {}))
    try:
        return _load("bz98_export_for_source_tests", "bz98tools/ogretools/OgreExport.py")
    finally:
        for name in stubs:
            sys.modules.pop(name, None)


msrc = _load("bz98_material_source_tests", "bz98tools/ogretools/material_source.py")

STOCK = (
    'import * from "BZBase.material"\n\n'
    "material avwaks_avwalk00 : BZBase\n{\n"
    "\tset_texture_alias DiffuseMap avwaks_avwalk00_D.png\n"
    "\tset_texture_alias NormalMap flat_N.dds\n"
    "\tset_texture_alias SpecularMap black.dds\n"
    "\tset_texture_alias EmissiveMap black.dds\n\n"
    '\tset $diffuse "1 1 1"\n\tset $ambient "1 1 1"\n\tset $specular ".7 .7 .7"\n'
    '\tset $shininess "127"\n\tset $glow "1 1 1"\n}\n\n'
    "material avwaks_avcpit01_cockpit : BZBaseCockpit\n{\n"
    "\tset_texture_alias DiffuseMap avwaks_avcpit01_D.png\n"
    "\tset_texture_alias NormalMap flat_N.dds\n\n"
    '\tset $diffuse "1 1 1"\n\tset $shininess "127"\n}\n'
)


class FakeMaterial(dict):
    def __init__(self, name, diffuse=(0.8, 0.8, 0.8)):
        super().__init__()
        self.name = name
        self.use_nodes = False
        self.diffuse_color = (*diffuse, 1.0)


class MaterialSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bzmatsrc_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.mesh = os.path.join(self.tmp, "avwaks.mesh")
        with open(os.path.join(self.tmp, "avwaks.material"), "w", encoding="utf-8") as h:
            h.write(STOCK)

    def _round_trip(self, names, change=None):
        export = _blender_export()
        mats = {}
        for name in names:
            mat = FakeMaterial(name)
            self.assertTrue(msrc.attach_source(mat, self.mesh, name.replace(".001", "")))
            if change:
                change(mat)
            diffuse, tex = msrc.read_colour_texture(mat)
            info = {"ambient": list(diffuse), "diffuse": list(diffuse), "specular": list(diffuse)}
            src = msrc.source_info(mat, diffuse, tex)
            if src:
                info["source"] = src
            mats[name] = info
        out_mesh = os.path.join(self.tmp, "out.mesh")
        export.xSaveMaterialData(out_mesh, {"materials": mats}, True, False)
        with open(os.path.join(self.tmp, "out.material"), encoding="utf-8") as h:
            return h.read()

    def test_stock_blocks_round_trip_with_parent_and_aliases(self):
        text = self._round_trip(["avwaks_avwalk00", "avwaks_avcpit01_cockpit"])
        self.assertEqual(text, STOCK.rstrip() + "\n\n")
        self.assertIn("avwaks_avcpit01_cockpit : BZBaseCockpit", text)
        self.assertIn("NormalMap flat_N.dds", text)

    def test_changed_diffuse_only_changes_diffuse(self):
        def change(mat):
            mat.diffuse_color = (0.25, 0.5, 0.75, 1.0)

        text = self._round_trip(["avwaks_avwalk00"], change)
        self.assertEqual(text, STOCK.split("material avwaks_avcpit01")[0].replace('$diffuse "1 1 1"', '$diffuse "0.25 0.5 0.75"').rstrip() + "\n\n")

    def test_changed_diffuse_texture_replaces_only_the_diffuse_alias(self):
        source = msrc.source_info(
            _with_source(self.mesh, "avwaks_avwalk00"), (1, 1, 1), "C:/art/new_D.png"
        )
        self.assertEqual(source["texture_override"], "new_D.png")
        text = msrc.source_block_text(source)
        self.assertIn("set_texture_alias DiffuseMap new_D.png", text)
        self.assertIn("NormalMap flat_N.dds", text)

    def test_unchanged_snapshot_has_no_overrides(self):
        mat = _with_source(self.mesh, "avwaks_avwalk00")
        source = msrc.source_info(mat, (0.8, 0.8, 0.8), "")
        self.assertNotIn("diffuse_override", source)
        self.assertNotIn("texture_override", source)

    def test_no_source_output_is_byte_identical_to_the_old_generator(self):
        export = _blender_export()
        mats = {
            "hull": {"ambient": [1, 1, 1], "diffuse": [0.5, 0.5, 0.5], "specular": [0, 0, 0],
                     "emissive": [0, 0, 0], "texture": "hull_d.dds"},
            "cockpit_glass": {"ambient": [1, 1, 1], "diffuse": [1, 1, 1], "specular": [0, 0, 0],
                              "emissive": [0, 0, 0]},
        }
        export.xSaveMaterialData(os.path.join(self.tmp, "n.mesh"), {"materials": mats}, True, False)
        with open(os.path.join(self.tmp, "n.material"), encoding="utf-8") as h:
            text = h.read()
        expected = (
            'import * from "BZBase.material"\n\n'
            "material hull : BZBase\n{\n"
            "\tset_texture_alias DiffuseMap hull_d.dds\n\tset_texture_alias NormalMap hull_n.dds\n"
            "\tset_texture_alias SpecularMap hull_S.dds\n\tset_texture_alias EmissiveMap hull_e.dds\n\n"
            '\tset $diffuse "0.5 0.5 0.5"\n\tset $ambient "1 1 1"\n\tset $specular "0 0 0"\n'
            '\tset $shininess "127"\n}\n\n'
            "material cockpit_glass : BZBaseCockpit\n{\n"
            "\tset_texture_alias DiffuseMap cockpit_glass_d.dds\n\tset_texture_alias NormalMap cockpit_glass_n.dds\n"
            "\tset_texture_alias SpecularMap cockpit_glass_S.dds\n\tset_texture_alias EmissiveMap cockpit_glass_e.dds\n\n"
            '\tset $diffuse "1 1 1"\n\tset $ambient "1 1 1"\n\tset $specular "0 0 0"\n'
            '\tset $shininess "127"\n}\n\n'
        )
        self.assertEqual(text, expected)

    def test_renamed_materials_get_their_own_header_even_with_a_shared_source(self):
        text = self._round_trip(["avwaks_avcpit01_cockpit.001", "avwaks_avcpit01_cockpit"])
        self.assertIn("material avwaks_avcpit01_cockpit.001 : BZBaseCockpit\n{", text)
        self.assertIn("\nmaterial avwaks_avcpit01_cockpit : BZBaseCockpit\n{", text)
        self.assertEqual(text.count("set_texture_alias NormalMap flat_N.dds"), 2)

    def test_preamble_merges_unique_imports_and_adds_bzbase_for_unsourced(self):
        a = {"source": {"raw": "x", "preamble": 'import * from "Other.material"\n// c\nimport * from "BZBase.material"\n'}}
        b = {"source": {"raw": "y", "preamble": 'import * from "Other.material"\nimport * from "More.material"\n'}}
        text = msrc.preamble_for({"a": a, "b": b})
        self.assertEqual(
            text,
            'import * from "Other.material"\nimport * from "BZBase.material"\nimport * from "More.material"\n\n',
        )
        only_other = {"source": {"raw": "x", "preamble": 'import * from "Other.material"\n'}}
        self.assertEqual(msrc.preamble_for({"a": only_other}),
                         'import * from "Other.material"\nimport * from "BZBase.material"\n\n')
        mixed = msrc.preamble_for({"a": b, "gen": {"diffuse": [1, 1, 1]}})
        self.assertEqual(mixed.count('import * from "BZBase.material"'), 1)
        self.assertEqual(msrc.preamble_for({"gen": {}}), msrc.STANDARD_PREAMBLE)

    def test_mixed_file_has_one_bzbase_import_and_both_materials(self):
        export = _blender_export()
        mat = FakeMaterial("avwaks_avwalk00")
        self.assertTrue(msrc.attach_source(mat, self.mesh))
        d, tx = msrc.read_colour_texture(mat)
        mats = {
            "avwaks_avwalk00": {"diffuse": list(d), "source": msrc.source_info(mat, d, tx)},
            "gen": {"ambient": [1, 1, 1], "diffuse": [1, 1, 1], "specular": [0, 0, 0]},
        }
        export.xSaveMaterialData(os.path.join(self.tmp, "mix.mesh"), {"materials": mats}, True, False)
        with open(os.path.join(self.tmp, "mix.material"), encoding="utf-8") as h:
            text = h.read()
        self.assertEqual(text.count("import * from"), 1)
        self.assertIn("material avwaks_avwalk00 : BZBase\n", text)
        self.assertIn("material gen : BZBase\n", text)

    def test_header_forms_parse_and_rename_keeps_the_parent(self):
        text = "material a:BZBase\n{\n}\nmaterial b: BZBaseCockpit\n{\n}\nmaterial c{\n}\n"
        found = msrc.parse_material_text(text)
        self.assertEqual(set(found), {"a", "b", "c"})
        self.assertTrue(found["a"]["raw"].startswith("material a:BZBase"))
        self.assertEqual(msrc.block_for({"source": found["a"]}, "a.001").split("\n")[0], "material a.001:BZBase")
        self.assertEqual(msrc.block_for({"source": found["b"]}, "z").split("\n")[0], "material z: BZBaseCockpit")
        self.assertEqual(msrc.block_for({"source": found["c"]}, "z").split("\n")[0], "material z{")

    def test_reimport_refreshes_stored_props_but_missing_source_keeps_them(self):
        mat = FakeMaterial("avwaks_avwalk00")
        mat["bz_material_source"] = "stale"
        self.assertTrue(msrc.attach_source(mat, self.mesh))
        self.assertIn("set_texture_alias", mat["bz_material_source"])
        keep = FakeMaterial("nope")
        keep["bz_material_source"] = "kept"
        self.assertTrue(msrc.attach_source(keep, self.mesh))
        self.assertEqual(keep["bz_material_source"], "kept")
        self.assertFalse(msrc.attach_source(None, self.mesh))

    def test_texture_key_handles_both_slash_styles(self):
        self.assertEqual(msrc.texture_key("C:\\art\\new_D.png"), "new_D.png")
        self.assertEqual(msrc.texture_key("//rel/new_D.png"), "new_D.png")
        mat = _with_source(self.mesh, "avwaks_avwalk00")
        mat["bz_material_snapshot"] = '{"diffuse": [0.8, 0.8, 0.8], "texture": "a_D.png"}'
        self.assertNotIn("texture_override", msrc.source_info(mat, (0.8, 0.8, 0.8), "C:\\x\\a_D.png"))

    def test_local_parent_is_flagged(self):
        text = "abstract material base_local\n{\n}\nmaterial kid : base_local\n{\n}\nmaterial ok : BZBase\n{\n}\n"
        found = msrc.parse_material_text(text)
        self.assertEqual(found["kid"]["local_parent"], "base_local")
        self.assertEqual(found["ok"]["local_parent"], "")

    def test_unknown_material_has_no_source_and_other_files_are_searched(self):
        self.assertFalse(msrc.attach_source(FakeMaterial("nope"), self.mesh))
        other = os.path.join(self.tmp, "other.mesh")
        self.assertTrue(msrc.attach_source(FakeMaterial("avwaks_avwalk00"), other))


def _with_source(mesh, name):
    mat = FakeMaterial(name)
    assert msrc.attach_source(mat, mesh)
    return mat


if __name__ == "__main__":
    unittest.main()
