# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Additional safety regressions for pilot animation patching."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools.bzrmodelporter.ogreskeleton import Bone  # noqa: E402
from bz98tools.bzrmodelporter.spacial import Quaternion  # noqa: E402
from bz98tools.pilot_animation_patch import (  # noqa: E402
    PilotAnimationPatchError,
    patch_animations,
    validate_skeleton_compatibility,
)
from test_pilot_animation_patch import make_skeleton  # noqa: E402


class PilotAnimationPatchSafetyTests(unittest.TestCase):
    def test_bind_quaternion_sign_flip_is_equivalent(self):
        stock = make_skeleton()
        replacement = make_skeleton()
        bone = replacement.get_bone(6)
        q = bone.orientation
        bone.orientation = Quaternion(-q.w, -q.x, -q.y, -q.z)
        self.assertTrue(validate_skeleton_compatibility(stock, replacement))

    def test_multi_clip_patch_is_transactional(self):
        stock = make_skeleton(idle_value=1.0, run_value=2.0)
        replacement = make_skeleton(idle_value=100.0, run_value=200.0)
        original_idle = stock.animation_map["idle"]
        original_run = stock.animation_map["runForward"]

        # Corrupt only the second requested animation track. Skipping bind-pose
        # validation here isolates the clone/apply transaction behavior.
        bad_target = Bone(replacement, "WrongPelvis", 6)
        replacement.animation_map["runForward"].get_track(6).target_bone = bad_target

        with self.assertRaises(PilotAnimationPatchError):
            patch_animations(
                stock,
                replacement,
                ["idle", "runForward"],
                validate_bind_pose=False,
            )

        self.assertIs(stock.animation_map["idle"], original_idle)
        self.assertIs(stock.animation_map["runForward"], original_run)


if __name__ == "__main__":
    unittest.main()
