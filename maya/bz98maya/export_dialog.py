"""The export dialog: Blender's three export modes and options, with presets.

``core/export_session.ExportSession`` is the controller (no UI, no Maya): it holds the option
values for one export kind, switches modes, applies / saves / deletes presets and runs the
export. ``show_dialog`` builds a ``cmds`` window over a session from Blender's option definitions
(``core/export_options.py``), so labels, tooltips, enums and defaults are Blender's.

Modes (as in Blender): **Legacy only** (VDF / SDF / GEO files), **Legacy + Redux** (the same
plus Redux ``.mesh`` / ``.skeleton`` / ``.material`` / ``.dds`` by the toolkit's auto-port),
**Redux only** (the BZR ``.mesh`` and ``.material`` alone).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import maya.cmds as cmds

from . import maya_io
from .core import export_options as eo
from .core.export_session import LEGACY_KINDS, MODES, ExportSession


def _runner(kind: str, path: str, options: Dict[str, Any]):
    if kind == "MESH":
        return maya_io.export_redux_mesh(path, options)
    return maya_io.export_legacy(kind, path, options)


def new_session(kind: str = "VDF", preset_dir: Optional[str] = None) -> ExportSession:
    return ExportSession(kind, preset_dir, _runner)


# --- view --------------------------------------------------------------------------

WINDOW = "bzExportWindow"
_SESSION: Optional[ExportSession] = None


def _control_for(option: eo.Option, session: ExportSession, controls: Dict[str, Any]):
    tip = option.tip or option.label
    name = option.name

    def on_change(*_args):
        control, _opt = controls[name]
        session.set_option(name, _read(control, option))

    if option.kind == "bool":
        control = cmds.checkBox(label=option.label, annotation=tip, value=bool(session.options[name]),
                                changeCommand=on_change)
    elif option.kind == "enum":
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2)
        cmds.text(label=option.label, annotation=tip, align="left")
        control = cmds.optionMenu(annotation=tip, changeCommand=on_change)
        for ident, label, _tip in option.items:
            cmds.menuItem(label=label, annotation=_tip)
        labels = {ident: label for ident, label, _t in option.items}
        cmds.optionMenu(control, edit=True, value=labels.get(session.options[name], option.items[0][1]))
        cmds.setParent("..")
    elif option.kind == "string":
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2)
        cmds.text(label=option.label, annotation=tip, align="left")
        control = cmds.textField(annotation=tip, text=str(session.options[name] or ""), changeCommand=on_change)
        cmds.setParent("..")
    elif option.kind in ("floatvec", "intvec"):
        values = list(session.options[name])
        rows = []
        cmds.columnLayout(adjustableColumn=True)
        cmds.text(label=option.label, annotation=tip, align="left")
        for start in range(0, len(values), 4):
            chunk = values[start:start + 4]
            maker = cmds.floatFieldGrp if option.kind == "floatvec" else cmds.intFieldGrp
            rows.append(maker(numberOfFields=len(chunk), annotation=tip, value=chunk + [0] * (4 - len(chunk)),
                              changeCommand=on_change))
        cmds.setParent("..")
        control = rows
    elif option.kind == "int":
        control = cmds.intFieldGrp(label=option.label, annotation=tip, value1=int(session.options[name] or 0),
                                   changeCommand=on_change)
    else:
        control = cmds.floatFieldGrp(label=option.label, annotation=tip, value1=float(session.options[name] or 0.0),
                                     changeCommand=on_change)
    controls[name] = (control, option)


def _read(control, option: eo.Option):
    if option.kind == "bool":
        return cmds.checkBox(control, query=True, value=True)
    if option.kind == "enum":
        label = cmds.optionMenu(control, query=True, value=True)
        return next((ident for ident, lab, _t in option.items if lab == label), option.default)
    if option.kind == "string":
        return cmds.textField(control, query=True, text=True)
    if option.kind in ("floatvec", "intvec"):
        out: List[Any] = []
        query = cmds.floatFieldGrp if option.kind == "floatvec" else cmds.intFieldGrp
        for row in control:
            out.extend(query(row, query=True, value=True))
        return out[:option.size]
    if option.kind == "int":
        return cmds.intFieldGrp(control, query=True, value1=True)
    return cmds.floatFieldGrp(control, query=True, value1=True)


def show_dialog(kind: str = "VDF") -> ExportSession:
    """Open the export dialog (needs the Maya UI). Returns the session driving it."""
    global _SESSION
    if cmds.about(batch=True):
        raise RuntimeError("the export dialog needs the Maya UI (batch mode has none)")
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    session = _SESSION = new_session(kind)
    cmds.window(WINDOW, title="Battlezone Export", widthHeight=(460, 640))
    outer = cmds.columnLayout(adjustableColumn=True)
    holder = {"form": None}

    def rebuild(*_args):
        if holder["form"]:
            cmds.deleteUI(holder["form"])
        cmds.setParent(outer)
        holder["form"] = cmds.columnLayout(adjustableColumn=True, rowSpacing=3)
        _build_form(session, rebuild)

    rebuild()
    cmds.showWindow(WINDOW)
    return session


def _build_form(session: ExportSession, rebuild) -> None:
    controls: Dict[str, Any] = {}
    cmds.frameLayout(label="Output Mode", collapsable=False, marginWidth=6)
    cmds.columnLayout(adjustableColumn=True)
    mode_menu = cmds.optionMenu(label="Mode", changeCommand=lambda m: (session.set_mode(m), rebuild()))
    for mode in MODES:
        cmds.menuItem(label=mode)
    cmds.optionMenu(mode_menu, edit=True, value=session.mode)
    cmds.text(label=session.mode_text, align="left", wordWrap=True)
    if session.kind != "MESH":
        kind_menu = cmds.optionMenu(label="Format", changeCommand=lambda k: (session.set_kind(k), rebuild()))
        for kind in LEGACY_KINDS:
            cmds.menuItem(label=kind)
        cmds.optionMenu(kind_menu, edit=True, value=session.kind)
    cmds.setParent("..")
    cmds.setParent("..")

    if session.kind != "MESH":
        cmds.frameLayout(label="Presets", collapsable=True, marginWidth=6)
        cmds.columnLayout(adjustableColumn=True)
        for key, label in session.builtin_presets():
            cmds.button(label="Apply: " + label, command=lambda *_a, k=key: (session.apply_builtin(k), rebuild()))
        for label in session.custom_presets():
            cmds.rowLayout(numberOfColumns=2, adjustableColumn=1)
            cmds.button(label="Apply saved: " + label, command=lambda *_a, l=label: (session.apply_custom(l), rebuild()))
            cmds.button(label="Delete", command=lambda *_a, l=label: (session.delete_preset(l), rebuild()))
            cmds.setParent("..")
        cmds.button(label="Save Current Settings...", command=lambda *_a: _save_preset(session, rebuild))
        cmds.setParent("..")
        cmds.setParent("..")

    groups = [("Options", [o for o in eo.option_fields(session.kind) if not o.name.startswith("ogre_")])]
    redux_options = [o for o in eo.option_fields(session.kind) if o.name.startswith("ogre_")]
    if redux_options and session.options.get("auto_port_ogre"):
        groups.append(("Redux Output", [o for o in redux_options if not o.vdf_only]))
        vdf_only = [o for o in redux_options if o.vdf_only]
        if vdf_only:
            groups.append(("VDF-Specific Redux Options", vdf_only))
    for title, options in groups:
        if not options:
            continue
        cmds.frameLayout(label=title, collapsable=True, marginWidth=6)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
        for option in options:
            if option.name == "auto_port_ogre":
                _control_for(option, session, controls)
                cmds.checkBox(controls[option.name][0], edit=True, changeCommand=lambda v: (
                    session.set_option("auto_port_ogre", v), rebuild()))
            else:
                _control_for(option, session, controls)
        cmds.setParent("..")
        cmds.setParent("..")

    cmds.button(label="Export...", height=32, command=lambda *_a: _run_export(session))


def _save_preset(session: ExportSession, rebuild) -> None:
    result = cmds.promptDialog(title="Save Export Preset", message="Preset name:", button=["Save", "Cancel"],
                               defaultButton="Save", cancelButton="Cancel", dismissString="Cancel")
    if result == "Save":
        try:
            session.save_preset(cmds.promptDialog(query=True, text=True))
        except ValueError as exc:
            cmds.warning(str(exc))
        rebuild()


def _run_export(session: ExportSession) -> None:
    ext = session.extension()
    picked = cmds.fileDialog2(fileMode=0, fileFilter="*%s" % ext, caption="Export (%s)" % session.mode,
                              okCaption="Export")
    if not picked:
        return
    path = picked[0] if picked[0].lower().endswith(ext) else picked[0] + ext
    try:
        session.export(path)
    except Exception as exc:
        cmds.confirmDialog(title="Battlezone", message=str(exc), button=["OK"], icon="critical")
