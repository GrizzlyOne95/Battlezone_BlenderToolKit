"""The Battlezone menu: file dialogs and the callables the menu items run.

Kept out of the plug-in file (which Maya executes rather than imports) so tests can
import it and drive the handlers with file paths in place of the dialogs.
"""

import traceback

import maya.cmds as cmds
import maya.mel as mel

MENU_NAME = "BattlezoneMenu"
RAISE_ERRORS = False  # tests set this so failures are not swallowed by a dialog

VDF_FILTER = "Battlezone Vehicle (*.vdf)"
SDF_FILTER = "Battlezone Structure (*.sdf)"
MESH_FILTER = "Ogre Mesh (*.mesh)"


def _maya_io():
    # Imported lazily so a missing toolkit is reported when a menu item is used,
    # not as a plug-in load failure.
    from . import maya_io

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
        if RAISE_ERRORS:
            raise
        traceback.print_exc()
        cmds.confirmDialog(title="Battlezone", message=str(exc), button=["OK"], icon="critical")


def _import(action_name, file_filter, caption):
    path = _ask(1, file_filter, caption, "Import")
    if path:
        _run(getattr(_maya_io(), action_name), path)


def _export(action_name, file_filter, caption, extension):
    path = _ask(0, file_filter, caption, "Export")
    if path:
        if not path.lower().endswith(extension):
            path += extension
        _run(getattr(_maya_io(), action_name), path)


def import_vdf(*_args):
    _import("import_vdf", VDF_FILTER, "Import Battlezone VDF")


def export_vdf(*_args):
    _export("export_vdf", VDF_FILTER, "Export Battlezone VDF (writes .geo files beside it)", ".vdf")


def import_sdf(*_args):
    _import("import_sdf", SDF_FILTER, "Import Battlezone SDF")


def export_sdf(*_args):
    _export("export_sdf", SDF_FILTER, "Export Battlezone SDF (writes .geo files beside it)", ".sdf")


def import_mesh(*_args):
    _import("import_ogre_mesh", MESH_FILTER, "Import Ogre Mesh")


def export_mesh(*_args):
    _export("export_ogre_mesh", MESH_FILTER, "Export Ogre Mesh", ".mesh")


def create_menu():
    """Build the menu; a no-op without a main window (batch / standalone)."""
    if cmds.about(batch=True):
        return False
    delete_menu()
    main_window = mel.eval("$tmp = $gMainWindow")
    cmds.menu(MENU_NAME, label="Battlezone", parent=main_window, tearOff=True)
    cmds.menuItem(label="Import VDF...", command=import_vdf, parent=MENU_NAME)
    cmds.menuItem(label="Export VDF...", command=export_vdf, parent=MENU_NAME)
    cmds.menuItem(divider=True, parent=MENU_NAME)
    cmds.menuItem(label="Import SDF...", command=import_sdf, parent=MENU_NAME)
    cmds.menuItem(label="Export SDF...", command=export_sdf, parent=MENU_NAME)
    cmds.menuItem(divider=True, parent=MENU_NAME)
    cmds.menuItem(label="Import Ogre Mesh...", command=import_mesh, parent=MENU_NAME)
    cmds.menuItem(label="Export Ogre Mesh...", command=export_mesh, parent=MENU_NAME)
    return True


def delete_menu():
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)
