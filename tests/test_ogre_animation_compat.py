import tempfile
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

from bz98tools.ogrefast.pure.animation_compat import (
    AnimationData,
    UnsupportedAnimationScale,
)
from bz98tools.ogrefast.pure.kenshi_compat import (
    BoneData,
    Matrix3,
    OgreQuaternion,
    SkeletonVersion,
    Vector3,
)
from bz98tools.ogrefast.pure.skeleton_compat import KenshiObjectSerializer


class PureOgreAnimationTests(unittest.TestCase):
    @staticmethod
    def _skeleton(serializer):
        skeleton = serializer.create_skeleton("animated.skeleton")
        skeleton.set_bones(
            [
                BoneData(
                    0,
                    "root",
                    Vector3(),
                    OgreQuaternion(),
                    Vector3(1.0, 1.0, 1.0),
                    "",
                    [],
                )
            ]
        )
        return skeleton

    def test_native_oracle_transform_contract_and_roundtrip(self):
        basis = np.asarray(
            [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        )
        times = np.asarray([0.0, 1.0], dtype=np.float32)
        locations = np.asarray(
            [[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]], dtype=np.float32
        )
        rotations = np.asarray(
            [
                [1.0, 0.9238795],
                [0.0, 0.3826834],
                [0.0, 0.0],
                [0.0, 0.0],
            ],
            dtype=np.float32,
        )

        animation = AnimationData()
        animation.name = "turn"
        animation.length = 1.0
        animation.append_animation_track(
            "root",
            Matrix3(basis.tolist()),
            times,
            locations,
            rotations,
            np.ones((3, 2), dtype=np.float32),
            False,
        )

        stored = animation._tracks[0]
        np.testing.assert_allclose(
            stored.translations,
            np.asarray([[-2.0, -1.0, 3.0], [-5.0, -4.0, 6.0]], dtype=np.float32),
            atol=1e-6,
        )
        np.testing.assert_allclose(
            stored.rotations,
            np.asarray(
                [[1.0, 0.0, 0.0, 0.0], [0.9238795, 0.0, 0.0, 0.3826834]],
                dtype=np.float32,
            ),
            atol=1e-6,
        )

        writer = KenshiObjectSerializer()
        skeleton = self._skeleton(writer)
        skeleton.add_animation(animation)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "animated.skeleton"
            writer.save_skeleton(skeleton, path, SkeletonVersion.V_1_8)

            reader = KenshiObjectSerializer()
            reader.add_resource_location(temp_dir)
            loaded = reader.load_skeleton(path.name)

        loaded_animations = loaded.get_animations()
        self.assertEqual(len(loaded_animations), 1)
        self.assertEqual(loaded_animations[0].name, "turn")
        self.assertAlmostEqual(loaded_animations[0].length, 1.0, places=6)

        # ogre_importer supplies the transpose/inverse of the exporter basis.
        blender_tracks = loaded_animations[0].get_animations(
            {"root": Matrix3(basis.T.tolist())}, fps=30.0, round_frame=False
        )
        self.assertEqual(len(blender_tracks), 1)
        track = blender_tracks[0]
        np.testing.assert_allclose(track.nd_locations[:, 0::2], [[0.0, 30.0]] * 3)
        np.testing.assert_allclose(track.nd_locations[:, 1::2], locations, atol=1e-6)
        np.testing.assert_allclose(track.nd_rotations[:, 0::2], [[0.0, 30.0]] * 4)
        np.testing.assert_allclose(track.nd_rotations[:, 1::2], rotations, atol=1e-6)
        self.assertFalse(track.has_scale)

    def test_scale_key_export_fails_closed(self):
        animation = AnimationData()
        with self.assertRaises(UnsupportedAnimationScale):
            animation.append_animation_track(
                "root",
                Matrix3([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
                np.asarray([0.0], dtype=np.float32),
                np.zeros((3, 1), dtype=np.float32),
                np.asarray([[1.0], [0.0], [0.0], [0.0]], dtype=np.float32),
                np.ones((3, 1), dtype=np.float32),
                True,
            )


if __name__ == "__main__":
    unittest.main()
