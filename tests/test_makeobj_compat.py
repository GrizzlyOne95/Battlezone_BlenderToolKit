# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors

import math
import unittest

import _bootstrap  # noqa: F401
from bz98tools import makeobj_compat


class MakeObjNormalTests(unittest.TestCase):
    def test_equal_weight_adjacent_face_normals(self):
        result = makeobj_compat.makeobj_vertex_normals(
            3,
            [(0, 1, 2), (0, 1, 2)],
            [(10.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
        )
        expected = math.sqrt(0.5)
        for normal in result:
            self.assertAlmostEqual(normal[0], expected, places=7)
            self.assertAlmostEqual(normal[1], expected, places=7)
            self.assertAlmostEqual(normal[2], 0.0, places=7)

    def test_isolated_vertex_uses_fallback(self):
        result = makeobj_compat.makeobj_vertex_normals(
            2,
            [(0,)],
            [(0.0, 0.0, 4.0)],
            fallback_normals=[(1.0, 0.0, 0.0), (0.0, 3.0, 0.0)],
        )
        self.assertEqual(result[0], (0.0, 0.0, 1.0))
        self.assertEqual(result[1], (0.0, 1.0, 0.0))


class MakeObjPolyColorTests(unittest.TestCase):
    def _pixels_2x2(self):
        # Blender bottom-up rows: bottom = red, green; top = blue, white.
        return [
            1.0, 0.0, 0.0, 1.0,
            0.0, 1.0, 0.0, 1.0,
            0.0, 0.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0,
        ]

    def test_full_texture_average(self):
        rgb = makeobj_compat.makeobj_polygon_rgb(
            [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
            2,
            2,
            self._pixels_2x2(),
        )
        self.assertEqual(rgb, (127, 127, 127))

    def test_makeobj_bitmask_wrap(self):
        # U=2 maps to integer x=2 on a 2px texture, then MakeOBJ wraps with
        # x & 1. Use a small triangle whose edge samples include wrapped x=2.
        rgb = makeobj_compat.makeobj_polygon_rgb(
            [(2.0, 1.0), (2.0, 0.0), (3.0, 0.0)],
            2,
            2,
            self._pixels_2x2(),
        )
        self.assertIsNotNone(rgb)
        self.assertTrue(all(0 <= channel <= 255 for channel in rgb))

    def test_linear_to_srgb(self):
        self.assertAlmostEqual(
            makeobj_compat.linear_to_srgb(0.21404114), 0.5, places=5
        )


if __name__ == "__main__":
    unittest.main()
