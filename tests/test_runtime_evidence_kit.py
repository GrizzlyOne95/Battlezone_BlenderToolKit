# Battlezone 98R Blender ToolKit
# Copyright (C) 2024-2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Tests for scripts/make_runtime_evidence_kit.py (bpy-free).

The evidence kit is the harness behind docs/RUNTIME_SEMANTIC_VERIFICATION.md:
these tests prove it enforces the document's normative controls at build time
(8-char stems, SHA-256 manifest agreement, unique filenames, control list,
INCONCLUSIVE-default session record).
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _path in (_HERE, _ROOT, _SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "make_runtime_evidence_kit",
        os.path.join(_SCRIPTS, "make_runtime_evidence_kit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceKitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = _load_module()
        cls.out = tempfile.mkdtemp(prefix="bz_evidence_test_")
        cls.kit.main(["--out", cls.out])
        with open(os.path.join(cls.out, "evidence_manifest.json"),
                  encoding="utf-8") as handle:
            cls.manifest = json.load(handle)
        with open(os.path.join(cls.out, "RUNTIME_SESSION_RECORD.md"),
                  encoding="utf-8") as handle:
            cls.record = handle.read()

    def test_every_asset_manifest_hash_matches_bytes(self):
        for asset in self.manifest["assets"]:
            with open(os.path.join(self.out, asset["file"]), "rb") as handle:
                data = handle.read()
            self.assertEqual(len(data), asset["bytes"], asset["case"])
            self.assertEqual(hashlib.sha256(data).hexdigest(),
                             asset["sha256"], asset["case"])

    def test_all_stems_within_bz_limit(self):
        limit = self.kit.MAX_STEM_LEN
        for asset in self.manifest["assets"]:
            stem = asset["file"].split(".")[0]
            self.assertLessEqual(len(stem), limit,
                                 "%s exceeds BZ filename limit" % asset["case"])
            self.assertTrue(asset["file"].endswith(".vdf"))

    def test_filenames_unique_and_case_ids_unique(self):
        files = [a["file"] for a in self.manifest["assets"]]
        cases = [a["case"] for a in self.manifest["assets"]]
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(len(cases), len(set(cases)))

    def test_phases_covered_in_priority_order(self):
        phases = {a["phase"] for a in self.manifest["assets"]}
        # A=VLOC40-priority doc uses A..F; highest-risk semantics present.
        self.assertTrue({"A", "B", "C", "D", "E", "F"} <= phases)

    def test_transform_controls_present(self):
        authored_blob = json.dumps(self.manifest["assets"])
        # signed/fractional/non-round translation values (control 9)
        self.assertIn("-2.75", authored_blob)
        self.assertIn("1.137", authored_blob)
        # non-90-degree rotations (control 10)
        self.assertIn("37", authored_blob)
        self.assertIn("130", authored_blob)
        # combined rotation+translation (control 11)
        self.assertTrue(any(
            "rotation_deg" in json.dumps(a.get("authored", {}))
            and "translation" in json.dumps(a.get("authored", {}))
            for a in self.manifest["assets"]))
        # non-identity parent transform case exists (control 12)
        self.assertTrue(any("parent_matrix" in a.get("authored", {})
                            for a in self.manifest["assets"]))

    def test_damage_bracketing_assets_exist(self):
        d_cases = [a for a in self.manifest["assets"] if a["phase"] == "D"]
        purposes = " ".join(a["purpose"] for a in d_cases).lower()
        self.assertTrue(any("lod" in p or "ladder" in p
                            for p in purposes.split(";")))
        names = [a["case"] for a in d_cases]
        self.assertTrue(any("M" in n for n in names),
                         "missing-band fallback probe required")

    def test_session_record_defaults_inconclusive_and_lists_controls(self):
        self.assertIn("INCONCLUSIVE", self.record)
        self.assertEqual(self.record.count("| INCONCLUSIVE |"),
                         len(self.manifest["assets"]))
        for control in ("sha256", "UNPATCHED", "restart", "bracket",
                        "INCONCLUSIVE"):
            self.assertIn(control, self.record)

    def test_control_list_complete(self):
        blob = json.dumps(self.manifest["controls"])
        blob_lower = blob.lower()
        for needle in ("sha256", "renderer", "dll/mod inventory",
                       "revision", "unpatched", "8 characters",
                       "non-round", "non-90-degree", "combined",
                       "parent transform", "both directions",
                       "per asset class", "player traversal",
                       "inconclusive"):
            self.assertIn(needle, blob_lower)

    def test_stem_violation_fails_closed(self):
        with self.assertRaises(SystemExit):
            self.kit.enforce_stem("ninechars.vdf")


if __name__ == "__main__":
    unittest.main()
