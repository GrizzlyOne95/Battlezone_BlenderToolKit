import math
import unittest

from bz98tools.ogrefast.pure.kenshi_facade import OgreQuaternion


class PureKenshiQuaternionTests(unittest.TestCase):
    def test_identity_to_angle_axis(self):
        angle, axis = OgreQuaternion().to_angle_axis()
        self.assertAlmostEqual(angle.valueRadians(), 0.0, places=7)
        self.assertAlmostEqual(axis.x, 1.0, places=7)
        self.assertAlmostEqual(axis.y, 0.0, places=7)
        self.assertAlmostEqual(axis.z, 0.0, places=7)

    def test_quarter_turn_z_to_angle_axis(self):
        half = math.pi / 4.0
        angle, axis = OgreQuaternion(math.cos(half), 0.0, 0.0, math.sin(half)).to_angle_axis()
        self.assertAlmostEqual(angle.valueRadians(), math.pi / 2.0, places=6)
        self.assertAlmostEqual(axis.x, 0.0, places=6)
        self.assertAlmostEqual(axis.y, 0.0, places=6)
        self.assertAlmostEqual(axis.z, 1.0, places=6)

    def test_non_unit_quaternion_is_normalized(self):
        q = OgreQuaternion(2.0, 0.0, 0.0, 2.0)
        angle, axis = q.to_angle_axis()
        self.assertAlmostEqual(angle.valueRadians(), math.pi / 2.0, places=6)
        self.assertAlmostEqual(axis.z, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
