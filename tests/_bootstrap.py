# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""
Test bootstrap: make the pure-Python bz98tools modules importable WITHOUT
Blender. The real bz98tools/__init__.py imports bpy at module scope; we
register a lightweight package object pointing at the same directory so
submodule imports (and their relative imports) resolve normally while the
bpy-dependent __init__ is never executed.
"""

import os
import sys
import types

_PKG_NAME = "bz98tools"
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_PKG_DIR = os.path.join(_REPO_ROOT, "bz98tools")


def ensure_package():
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = types.ModuleType(_PKG_NAME)
    pkg.__path__ = [_PKG_DIR]
    pkg.__doc__ = "Test-only package shell (bpy-free bootstrap)."
    sys.modules[_PKG_NAME] = pkg
    return pkg


ensure_package()
