import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from bz98tools.ogrefast.pure.kenshi_compat import (
    BoneData,
    OgreQuaternion,
    SkeletonData,
    SkeletonVersion,
    Vector3,
)
from bz98tools.ogrefast.pure.skeleton_compat import KenshiObjectSerializer


class PureOgreSkeletonTests(unittest.TestCase):
    def test_bone_hierarchy_roundtrip(self):
        skeleton = SkeletonData("vehicle.skeleton", "General")
        skeleton.set_bones(
            [
                BoneData(
                    0,
                    "root",
                    Vector3(1.0, 2.0, 3.0),
                    OgreQuaternion(1.0, 0.0, 0.0, 0.0),
                    Vector3(1.0, 1.0, 1.0),
                    "",
                    ["turret"],
                ),
                BoneData(
                    1,
                    "turret",
                    Vector3(0.0, 4.0, -2.0),
                    OgreQuaternion(0.9238795, 0.0, 0.3826834, 0.0),
                    Vector3(1.0, 1.25, 1.0),
                    "root",
                    [],
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "vehicle.skeleton"
            writer = KenshiObjectSerializer()
            writer.save_skeleton(skeleton, path, SkeletonVersion.V_1_8)
            self.assertTrue(path.is_file())

            reader = KenshiObjectSerializer()
            reader.add_resource_location(temp_dir)
            loaded = reader.load_skeleton(path.name)

        bones = loaded.get_bones(has_helper=True)
        self.assertEqual([bone.id for bone in bones], [0, 1])
        self.assertEqual([bone.name for bone in bones], ["root", "turret"])
        self.assertEqual(bones[0].parent_name, "")
        self.assertEqual(bones[0].child_names, ["turret"])
        self.assertEqual(bones[1].parent_name, "root")
        self.assertAlmostEqual(bones[0].position.x, 1.0, places=6)
        self.assertAlmostEqual(bones[0].position.y, 2.0, places=6)
        self.assertAlmostEqual(bones[0].position.z, 3.0, places=6)
        self.assertAlmostEqual(bones[1].rotate.w, 0.9238795, places=6)
        self.assertAlmostEqual(bones[1].rotate.y, 0.3826834, places=6)
        self.assertAlmostEqual(bones[1].scale.y, 1.25, places=6)

    def test_missing_parent_is_rejected(self):
        skeleton = SkeletonData("bad.skeleton", "General")
        skeleton.set_bones(
            [
                BoneData(
                    0,
                    "child",
                    Vector3(),
                    OgreQuaternion(),
                    Vector3(1.0, 1.0, 1.0),
                    "missing",
                    [],
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.skeleton"
            with self.assertRaises(RuntimeError):
                KenshiObjectSerializer().save_skeleton(
                    skeleton, path, SkeletonVersion.V_1_8
                )


if __name__ == "__main__":
    unittest.main()
