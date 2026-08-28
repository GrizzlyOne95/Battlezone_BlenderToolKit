# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""bpy-free tests for Redux pilot profile and clip matching helpers."""

import ast
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools import pilot_animation_profiles as profiles  # noqa: E402


def _named_bones(count, *special_names):
    names = [f"Bone_{index:03d}" for index in range(count)]
    for index, name in enumerate(special_names):
        names[index] = name
    return {index: name for index, name in enumerate(names)}


class PilotProfileTests(unittest.TestCase):
    def test_soviet_third_person_signature(self):
        result = profiles.detect_pilot_profile(
            _named_bones(32, "ssp11ctr", "Bip01", "ssp11mg1", "ssp21mg1"),
            serializer_version=profiles.SERIALIZER_V1_80,
        )
        self.assertEqual(result["key"], "ssp_tp")
        self.assertEqual(result["label"], "Soviet Third Person")
        self.assertEqual(result["expected_bone_count"], 32)
        self.assertEqual(result["expected_serializer"], profiles.SERIALIZER_V1_80)
        self.assertEqual(result["confidence"], "HIGH")
        self.assertFalse(result["warnings"])

    def test_soviet_first_person_signature_uses_pov_helper(self):
        result = profiles.detect_pilot_profile(
            _named_bones(32, "ssp11ctr", "Bip01", "ssp21mg1", "ssp11GC1", "ssp11POV"),
            serializer_version=profiles.SERIALIZER_V1_10,
        )
        self.assertEqual(result["key"], "ssp_fp")
        self.assertEqual(result["label"], "Soviet First Person")
        self.assertEqual(result["expected_bone_count"], 32)
        self.assertEqual(result["expected_serializer"], profiles.SERIALIZER_V1_10)
        self.assertEqual(result["confidence"], "HIGH")

    def test_american_third_person_signature(self):
        result = profiles.detect_pilot_profile(
            _named_bones(71, "asp11ctr", "Bip01", "asp21mg1"),
            serializer_version=profiles.SERIALIZER_V1_80,
        )
        self.assertEqual(result["key"], "asp_tp")
        self.assertEqual(result["label"], "American Third Person")
        self.assertEqual(result["confidence"], "HIGH")

    def test_american_first_person_signature(self):
        result = profiles.detect_pilot_profile(
            _named_bones(73, "asp11ctr", "Bip01", "asp21mg1", "asp11GC1", "asp11POV"),
            serializer_version=profiles.SERIALIZER_V1_10,
        )
        self.assertEqual(result["key"], "asp_fp")
        self.assertEqual(result["label"], "American First Person")
        self.assertEqual(result["confidence"], "HIGH")

    def test_black_dog_first_person_signature(self):
        result = profiles.detect_pilot_profile(
            _named_bones(73, "bsp11ctr", "Bip01", "bsp21mg1", "bsp11GC1", "bsp11POV"),
            serializer_version=profiles.SERIALIZER_V1_10,
        )
        self.assertEqual(result["key"], "bsp_fp")
        self.assertEqual(result["label"], "Black Dog First Person")

    def test_serializer_mismatch_is_a_warning(self):
        result = profiles.detect_pilot_profile(
            _named_bones(73, "asp11ctr", "asp11POV"),
            serializer_version=profiles.SERIALIZER_V1_80,
        )
        self.assertTrue(result["known"])
        self.assertTrue(result["warnings"])
        self.assertIn(profiles.SERIALIZER_V1_10, result["warnings"][0])

    def test_unknown_rig_is_not_force_fit(self):
        result = profiles.detect_pilot_profile(_named_bones(46, "CreatureRoot"))
        self.assertFalse(result["known"])
        self.assertEqual(result["key"], "unknown_unknown")
        self.assertEqual(result["label"], "Unknown / Custom Pilot Rig")


class PilotClipMatchingTests(unittest.TestCase):
    def test_exact_clip_name_matches(self):
        self.assertTrue(profiles.action_name_matches_clip("runForward", "runForward"))

    def test_case_and_blender_suffix_are_tolerated(self):
        self.assertTrue(profiles.action_name_matches_clip("runForward", "RUNFORWARD.001"))

    def test_idle_eject_elect_stock_variant_is_aliased(self):
        self.assertTrue(profiles.action_name_matches_clip("idleEject", "idleElect"))
        self.assertTrue(profiles.action_name_matches_clip("idleElect", "idleEject.003"))

    def test_unrelated_action_does_not_match(self):
        self.assertFalse(profiles.action_name_matches_clip("runForward", "walkForward"))


class PilotUISyntaxTests(unittest.TestCase):
    def test_ui_module_parses_without_importing_blender(self):
        path = os.path.join(_REPO_ROOT, "bz98tools", "pilot_animation_ui.py")
        with open(path, "r", encoding="utf-8") as handle:
            ast.parse(handle.read(), filename=path)

    def test_addon_bootstrap_parses_without_importing_blender(self):
        path = os.path.join(_REPO_ROOT, "bz98tools", "__init__.py")
        with open(path, "r", encoding="utf-8") as handle:
            ast.parse(handle.read(), filename=path)


if __name__ == "__main__":
    unittest.main()
