# Battlezone Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of Battlezone Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Thin addon bootstrap.

The historical addon entry point grew into a large monolithic module. Keep that
implementation byte-for-byte in ``_addon_core.py`` and execute it in this module's
namespace so existing class ``__module__`` values, package-relative imports, and
public symbols remain compatible. New self-contained feature modules can then
register around the legacy core without invasive edits to it.
"""

from pathlib import Path as _Path

_core_path = _Path(__file__).with_name("_addon_core.py")
with _core_path.open("r", encoding="utf-8") as _core_handle:
    _core_code = compile(_core_handle.read(), str(_core_path), "exec")
exec(_core_code, globals(), globals())

del _core_code
del _core_handle

_core_register = register
_core_unregister = unregister

from . import makeobj_compat_ui as _makeobj_compat_ui
from . import pilot_animation_reference_ui as _pilot_animation_reference_ui
from . import pilot_animation_ui as _pilot_animation_ui

# Public addon identity and release version. Keep the package name ``bz98tools``
# stable for Blender installs and existing package-relative imports.
bl_info = dict(bl_info)
bl_info["name"] = "Battlezone Blender Toolkit"
bl_info["description"] = (
    "Import, export, validate, and author classic Battlezone and Battlezone 98 Redux assets."
)
bl_info["version"] = (1, 4, 10)


def register():
    _core_register()
    _pilot_animation_ui.register()
    _pilot_animation_reference_ui.register()
    _makeobj_compat_ui.register()


def unregister():
    try:
        _makeobj_compat_ui.unregister()
    finally:
        try:
            _pilot_animation_reference_ui.unregister()
        finally:
            try:
                _pilot_animation_ui.unregister()
            finally:
                _core_unregister()
