"""bpy-free tests for the explicit Jak Person compatibility profile."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools import jak_animation_builder as jak  # noqa: E402
from bz98tools import pilot_animation_profiles as profiles  # noqa: E402


class JakPersonCompatTests(unittest.TestCase):
    def test_person_index_table_remains_exact(self):
        self.assertEqual(
            profiles.PERSON_ANIMATION_INDEX_TO_NAME,
            {
                0: "stand2Kneel",
                1: "kneel2stand",
                2: "idle",
                3: "fireRecoilSniper",
                4: "runForward",
                5: "runBackward",
                6: "runLeft",
                7: "runRight",
                8: "death1",
                9: "idleParachute",
                10: "landParachute",
                11: "jump",
            },
        )

    def test_sniper_fsm_drives_three_stage_melee(self):
        compat = jak.jak_person_compat_aliases()
        self.assertEqual(compat["stand2Kneel"], "attack1")
        self.assertEqual(compat["fireRecoilSniper"], "attack2")
        self.assertEqual(compat["kneel2stand"], "attack3")
        # Indices resolve through the shared Person contract.
        self.assertEqual(profiles.person_animation_index("stand2Kneel"), 0)
        self.assertEqual(profiles.person_animation_index("kneel2stand"), 1)
        self.assertEqual(profiles.person_animation_index("fireRecoilSniper"), 3)

    def test_run_directions_resolve_to_walk(self):
        compat = jak.jak_person_compat_aliases()
        for name in (
            "runForward",
            "runBackward",
            "runLeft",
            "runRight",
            "walkForward",
            "walkBackward",
            "walkLeft",
            "walkRight",
        ):
            self.assertEqual(compat[name], "walk", name)

    def test_death_slots_resolve_to_death(self):
        compat = jak.jak_person_compat_aliases()
        self.assertEqual(compat["death1"], "death")
        self.assertEqual(compat["death2"], "death")

    def test_parachute_eject_resolve_safely_to_idle(self):
        compat = jak.jak_person_compat_aliases()
        for name in (
            "idleParachute",
            "landParachute",
            "idleEject",
            "idleElect",
            "Take_001",
        ):
            self.assertEqual(compat[name], "idle", name)

    def test_native_jak_actions_remain_present(self):
        # Compat aliases must only point at real baked/ODF Jak Actions.
        native = {
            "walk",
            "run",
            "jump",
            "idle",
            "death",
            "curious",
            "attack1",
            "attack2",
            "attack3",
            "attack4",
            "eat1",
            "eat2",
        }
        compat = jak.jak_person_compat_aliases()
        for dest, src in compat.items():
            self.assertIn(src, native | {"walk", "death", "idle"}, dest)
        # Every known Redux pilot clip is covered by compat + baked idle/jump.
        covered = set(compat) | {"idle", "jump"}
        for name in profiles.KNOWN_PILOT_CLIPS:
            self.assertIn(name, covered, name)

    def test_generic_default_stays_stock_safe(self):
        # The generic fallback must NOT inherit the Jak melee experiment.
        self.assertEqual(jak.DEFAULT_COMPAT_ALIASES["stand2Kneel"], "idle")
        self.assertNotIn("fireRecoilSniper", jak.DEFAULT_COMPAT_ALIASES)
        self.assertNotIn("kneel2stand", jak.DEFAULT_COMPAT_ALIASES)

    def test_compat_helper_returns_copy(self):
        first = jak.jak_person_compat_aliases()
        first["stand2Kneel"] = "idle"
        self.assertEqual(jak.JAK_PERSON_COMPAT_ALIASES["stand2Kneel"], "attack1")


class JakImportGuardTests(unittest.TestCase):
    def test_ancestor_detection(self):
        class _Node:
            def __init__(self, parent=None):
                self.parent = parent

        root = _Node()
        mesh = _Node(root)
        armature = _Node(mesh)
        bone = _Node(armature)
        # The XSI-style cycle case: mesh is an ancestor of the armature.
        self.assertTrue(jak._is_helper_ancestor(mesh, armature))
        self.assertTrue(jak._is_helper_ancestor(mesh, bone))
        self.assertTrue(jak._is_helper_ancestor(mesh, mesh))
        # Normal cases must still follow the stock importer path.
        self.assertFalse(jak._is_helper_ancestor(armature, mesh))
        self.assertFalse(jak._is_helper_ancestor(bone, mesh))
        self.assertFalse(jak._is_helper_ancestor(root, None))

    def test_cycle_guard_context_manager_exists(self):
        self.assertTrue(hasattr(jak, "_cycle_safe_fbx_import"))
        guard = jak._cycle_safe_fbx_import()
        self.assertTrue(hasattr(guard, "__enter__"))
        self.assertTrue(hasattr(guard, "__exit__"))


if __name__ == "__main__":
    unittest.main()
