# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Test runner for the bpy-free semantic test suite."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))


def main():
    loader = unittest.TestLoader()
    suite = loader.discover(_HERE, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    results = runner.run(suite)
    return 0 if results.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
