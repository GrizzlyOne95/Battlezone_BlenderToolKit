# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Pure-Python tests for safe Redux pilot skeleton animation patching."""

import io
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools.bzrmodelporter.ogreskeleton import Skeleton  # noqa: E402
from bz98tools.bzrmodelporter.spacial import Quaternion, Vector3  # noqa: E402
from bz98tools.pilot_animation_patch import (  # noqa: E402
    PilotAnimationPatchError,
    SERIALIZER_V1_10,
    SERIALIZER_V1_80,
    VersionPreservingSkeletonSerializer,
    dump_skeleton,
    patch_animations,
    read_skeleton_stream,
    validate_skeleton_compatibility,
)


def make_skeleton(version=SERIALIZER_V1_80, idle_value=1.0, run_value=2.0):
    skeleton = Skeleton()
    skeleton.serializer_version = version
    root = skeleton.create_bone(
        "Bip01", 0, Vector3(0.0, 1.0, 2.0), Quaternion(1.0, 0.0, 0.0, 0.0)
    )
    child = skeleton.create_bone(
        "Bip01_Pelvis",
        6,
        Vector3(0.0, 0.5, 0.0),
        Quaternion(1.0, 0.0, 0.0, 0.0),
    )
    root.add_child(child)

    idle = skeleton.create_animation("idle", 1.5)
    idle_track = idle.create_track(child)
    idle_track.create_keyframe(
        0.0,
        rot=Quaternion(1.0, 0.0, 0.0, 0.0),
        trans=Vector3(idle_value, 0.0, 0.0),
    )
    idle_track.create_keyframe(
        1.5,
        rot=Quaternion(0.98, 0.0, 0.2, 0.0),
        trans=Vector3(idle_value, 0.1, 0.0),
    )

    run = skeleton.create_animation("runForward", 0.63)
    run_track = run.create_track(child)
    run_track.create_keyframe(
        0.0,
        rot=Quaternion(1.0, 0.0, 0.0, 0.0),
        trans=Vector3(0.0, run_value, 0.0),
    )
    run_track.create_keyframe(
        0.63,
        rot=Quaternion(0.95, 0.3, 0.0, 0.0),
        trans=Vector3(0.0, run_value + 0.25, 0.0),
    )
    return skeleton


class VersionPreservingSerializerTests(unittest.TestCase):
    def _round_trip(self, version):
        original = make_skeleton(version=version)
        payload = dump_skeleton(original, validate_chunk_sizes=True)
        loaded = read_skeleton_stream(
            io.BytesIO(payload), validate_chunk_sizes=True
        )
        self.assertEqual(loaded.serializer_version, version)
        self.assertEqual([bone.handle for bone in loaded.bones()], [0, 6])
        self.assertEqual(loaded.get_bone(6).name, "Bip01_Pelvis")
        self.assertEqual(loaded.get_bone(6).parent.handle, 0)
        self.assertEqual(list(loaded.animation_map), ["idle", "runForward"])
        self.assertAlmostEqual(loaded.animation_map["runForward"].duration, 0.63, places=5)
        self.assertAlmostEqual(
            loaded.animation_map["runForward"].get_track(6).keyframe_list[0].translation.y,
            2.0,
            places=5,
        )

    def test_v180_round_trip(self):
        self._round_trip(SERIALIZER_V1_80)

    def test_v110_round_trip(self):
        self._round_trip(SERIALIZER_V1_10)

    def test_default_write_uses_skeleton_source_version(self):
        skeleton = make_skeleton(version=SERIALIZER_V1_10)
        stream = io.BytesIO()
        serializer = VersionPreservingSkeletonSerializer(stream)
        serializer.write(skeleton)
        loaded = read_skeleton_stream(io.BytesIO(stream.getvalue()))
        self.assertEqual(loaded.serializer_version, SERIALIZER_V1_10)


class PilotAnimationPatchTests(unittest.TestCase):
    def test_selected_patch_preserves_unselected_animation_and_stock_bones(self):
        stock = make_skeleton(idle_value=1.0, run_value=2.0)
        replacement = make_skeleton(idle_value=100.0, run_value=200.0)

        stock_bone = stock.get_bone(6)
        original_idle = stock.animation_map["idle"]
        patched = patch_animations(stock, replacement, ["runForward"])

        self.assertEqual(patched, ["runForward"])
        self.assertIs(stock.animation_map["idle"], original_idle)
        self.assertEqual(
            stock.animation_map["idle"].get_track(6).keyframe_list[0].translation.x,
            1.0,
        )
        self.assertEqual(
            stock.animation_map["runForward"].get_track(6).keyframe_list[0].translation.y,
            200.0,
        )
        self.assertIs(
            stock.animation_map["runForward"].get_track(6).target_bone,
            stock_bone,
        )

    def test_duplicate_requested_names_patch_once(self):
        stock = make_skeleton()
        replacement = make_skeleton(run_value=22.0)
        patched = patch_animations(
            stock, replacement, ["runForward", "runForward"]
        )
        self.assertEqual(patched, ["runForward"])

    def test_missing_animation_is_rejected_before_mutation(self):
        stock = make_skeleton()
        replacement = make_skeleton()
        original_run = stock.animation_map["runForward"]
        with self.assertRaises(PilotAnimationPatchError):
            patch_animations(stock, replacement, ["notAStockClip"])
        self.assertIs(stock.animation_map["runForward"], original_run)

    def test_bone_name_mismatch_is_rejected(self):
        stock = make_skeleton()
        replacement = make_skeleton()
        replacement.get_bone(6).change_name("WrongPelvis")
        with self.assertRaises(PilotAnimationPatchError):
            validate_skeleton_compatibility(stock, replacement)

    def test_parent_mismatch_is_rejected(self):
        stock = make_skeleton()
        replacement = make_skeleton()
        replacement.parent_bone(replacement.get_bone(6), None)
        with self.assertRaises(PilotAnimationPatchError):
            validate_skeleton_compatibility(stock, replacement)

    def test_bind_pose_numeric_noise_is_tolerated(self):
        stock = make_skeleton()
        replacement = make_skeleton()
        replacement.get_bone(6).position.y += 5.0e-5
        self.assertTrue(validate_skeleton_compatibility(stock, replacement))

    def test_material_bind_pose_change_is_rejected(self):
        stock = make_skeleton()
        replacement = make_skeleton()
        replacement.get_bone(6).position.y += 0.05
        with self.assertRaises(PilotAnimationPatchError):
            validate_skeleton_compatibility(stock, replacement)


if __name__ == "__main__":
    unittest.main()
