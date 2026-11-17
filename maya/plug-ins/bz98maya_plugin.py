"""Battlezone 98 / Redux import and export: adds a "Battlezone" menu to Maya.

Load from the Plug-in Manager (or put the ``maya`` folder on MAYA_MODULE_PATH
and use ``bz98maya.mod``). The plug-in registers no nodes or commands; it only
builds the menu (``bz98maya/menu.py``), so initialize / uninitialize create and
delete it.

The ``bz98maya`` package lives one folder above this file. The toolkit's
serializers are located by ``bz98maya.core.toolkit`` (``BZ98TOOLKIT_PATH``
override, then the repository this folder sits in).
"""

import os
import sys

import maya.api.OpenMaya as om
import maya.cmds as cmds

# Maya executes plug-in files instead of importing them, so __file__ is not defined;
# the code object still carries the path.
_HERE = os.path.dirname(os.path.abspath((lambda: None).__code__.co_filename))
_PACKAGE_ROOT = os.path.dirname(_HERE)  # the folder that contains bz98maya/
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)


def maya_useNewAPI():
    """Tell Maya this plug-in module uses Python API 2.0."""
    pass


def _create_menu():
    from bz98maya import menu

    menu.create_menu()


def initializePlugin(plugin):
    om.MFnPlugin(plugin, "Battlezone Blender ToolKit", "0.1.0", "Any")
    # At startup auto-load the main window may not exist yet; defer until the UI is idle.
    cmds.evalDeferred(_create_menu, lowestPriority=True)


def uninitializePlugin(plugin):
    from bz98maya import menu

    menu.delete_menu()
