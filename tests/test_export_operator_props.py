"""Export operators must declare every ogre_* property they read.

Regression: ExportVDF/ExportSDF read self.ogre_normal_mode (and the shared
auto-port panel draws it) while only ExportGEO declared it, so VDF/SDF export
with "Also Create Redux Files" raised AttributeError.
"""

import ast
import os
import unittest

_CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "bz98tools", "_addon_core.py")
_SHARED_DRAW = "_draw_shared_autoport_options"


def _module():
    with open(_CORE, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _declared(cls):
    return {n.target.id for n in cls.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}


def _self_reads(cls):
    return {n.attr for n in ast.walk(cls)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "self" and n.attr.startswith("ogre_")}


def _drawn_by_shared_panel(tree):
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == _SHARED_DRAW:
            return {c.args[1].value for c in ast.walk(fn)
                    if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "prop"
                    and len(c.args) >= 2 and isinstance(c.args[1], ast.Constant)}
    return set()


def _calls_shared_panel(cls):
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == _SHARED_DRAW
               for n in ast.walk(cls))


class ExportOperatorPropertyTests(unittest.TestCase):
    def test_export_operators_declare_what_they_use(self):
        tree = _module()
        shared = _drawn_by_shared_panel(tree)
        self.assertIn("ogre_normal_mode", shared)
        checked = 0
        for cls in tree.body:
            if not (isinstance(cls, ast.ClassDef) and cls.name.startswith("Export")):
                continue
            used = _self_reads(cls)
            if _calls_shared_panel(cls):
                used |= shared
            missing = used - _declared(cls)
            self.assertFalse(missing, f"{cls.name} uses undeclared {sorted(missing)}")
            checked += 1
        self.assertGreaterEqual(checked, 3)

    def test_ogre_props_never_reach_the_legacy_exporter(self):
        # execute() passes as_keywords(ignore=...) to export_*.export(); an
        # ogre_* property missing from the ignore list becomes an unexpected
        # keyword argument there.
        tree = _module()
        for cls in tree.body:
            if not (isinstance(cls, ast.ClassDef) and cls.name.startswith("Export")):
                continue
            for call in ast.walk(cls):
                if not (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "as_keywords"):
                    continue
                ignored = {e.value for kw in call.keywords if kw.arg == "ignore"
                           for e in getattr(kw.value, "elts", []) if isinstance(e, ast.Constant)}
                leaked = {p for p in _declared(cls) if p.startswith("ogre_")} - ignored
                self.assertFalse(leaked, f"{cls.name} passes {sorted(leaked)} to export()")

    def test_vdf_and_sdf_have_normal_mode(self):
        tree = _module()
        by_name = {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)}
        for name in ("ExportGEO", "ExportVDF", "ExportSDF"):
            self.assertIn("ogre_normal_mode", _declared(by_name[name]), name)


if __name__ == "__main__":
    unittest.main()
