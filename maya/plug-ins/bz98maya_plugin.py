"""Battlezone 98 / Redux import and export: adds a "Battlezone" menu to Maya.

Load from the Plug-in Manager (or put the ``maya`` folder on MAYA_MODULE_PATH
and use ``bz98maya.mod``). The plug-in registers no nodes or commands; it only
builds the menu, so initialize / uninitialize create and delete it.

The ``bz98maya`` package lives one folder above this file. The toolkit's
serializers are located by ``bz98maya.core.toolkit`` (``BZ98TOOLKIT_PATH``
override, then the repository this folder sits in).
"""

import os
import sys
import traceback

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACKAGE_ROOT = os.path.dirname(_HERE)  # the folder that contains bz98maya/
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

MENU_NAME = "BattlezoneMenu"

VDF_FILTER = "Battlezone Vehicle (*.vdf)"
MESH_FILTER = "Ogre Mesh (*.mesh)"


def maya_useNewAPI():
    """Tell Maya this plug-in module uses Python API 2.0."""
    pass


def _maya_io():
    # Imported lazily so a missing toolkit is reported when a menu item is used,
    # not as a plug-in load failure.
    import bz98maya.maya_io as maya_io

    return maya_io


def _ask(file_mode, file_filter, caption, ok_caption):
    result = cmds.fileDialog2(
        fileMode=file_mode,  # 1 = existing file, 0 = any file (save)
        fileFilter=file_filter,
        caption=caption,
        okCaption=ok_caption,
    )
    return result[0] if result else None


def _run(action, path):
    try:
        action(path)
    except Exception as exc:  # show it in the script editor and as a dialog
        traceback.print_exc()
        cmds.confirmDialog(title="Battlezone", message=str(exc), button=["OK"], icon="critical")


def _import_vdf(*_args):
    path = _ask(1, VDF_FILTER, "Import Battlezone VDF", "Import")
    if path:
        _run(_maya_io().import_vdf, path)


def _export_vdf(*_args):
    path = _ask(0, VDF_FILTER, "Export Battlezone VDF (writes .geo files beside it)", "Export")
    if path:
        if not path.lower().endswith(".vdf"):
            path += ".vdf"
        _run(_maya_io().export_vdf, path)


def _import_mesh(*_args):
    path = _ask(1, MESH_FILTER, "Import Ogre Mesh", "Import")
    if path:
        _run(_maya_io().import_ogre_mesh, path)


def _export_mesh(*_args):
    path = _ask(0, MESH_FILTER, "Export Ogre Mesh", "Export")
    if path:
        if not path.lower().endswith(".mesh"):
            path += ".mesh"
        _run(_maya_io().export_ogre_mesh, path)


def _create_menu():
    if cmds.about(batch=True):
        return
    _delete_menu()
    main_window = mel.eval("$tmp = $gMainWindow")
    cmds.menu(MENU_NAME, label="Battlezone", parent=main_window, tearOff=True)
    cmds.menuItem(label="Import VDF...", command=_import_vdf, parent=MENU_NAME)
    cmds.menuItem(label="Export VDF...", command=_export_vdf, parent=MENU_NAME)
    cmds.menuItem(divider=True, parent=MENU_NAME)
    cmds.menuItem(label="Import Ogre Mesh...", command=_import_mesh, parent=MENU_NAME)
    cmds.menuItem(label="Export Ogre Mesh...", command=_export_mesh, parent=MENU_NAME)


def _delete_menu():
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)


def initializePlugin(plugin):
    om.MFnPlugin(plugin, "Battlezone Blender ToolKit", "0.1.0", "Any")
    # At startup auto-load the main window may not exist yet; defer until the UI is idle.
    cmds.evalDeferred(_create_menu, lowestPriority=True)


def uninitializePlugin(plugin):
    _delete_menu()
