"""The dockable "Battlezone" authoring panel (``maya.cmds`` UI).

Two layers:

* the **controller** (``selected_part``, ``part_state``, ``set_part_attr``,
  ``model_node`` ...) reads and writes the authoring attributes and runs
  validation; it needs ``maya.cmds`` but no UI, so it works headless;
* the **view** (``build_ui``, ``show_panel``) generates the forms from the schema in
  ``core/schema.py`` (labels and tooltips are Blender's own) and needs a main window.

The panel shows the selected transform's GEO / VDF / SDF fields, the model header
(on the group an import creates), Blender's GEO Type Reference, the validation
results and the authoring tools.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import maya.cmds as cmds

from . import animation_tools, attributes, maya_io
from .core import anim as anim_core
from .core import authoring, schema, validate

WORKSPACE_CONTROL = "BattlezoneWorkspaceControl"
GROUP_ORDER = (
    ("GEO", "Battlezone GEO", True),
    ("Flags", "Engine Object Flags", False),
    ("Bounds", "Collision / Authored Bounds", False),
    ("Damage", "Damage Representation", False),
    ("Eyepoint", "Eyepoint / POV", False),
    ("Spinner", "VDF Extras: Spinner", False),
    ("SDF", "SDF Extras", False),
    ("Experimental", "Experimental (raw VDF matrix, GEO header / face defaults)", False),
)
MODEL_GROUPS = (
    ("Model", "Internal Name"),
    ("VDF", "VDF Settings"),
    ("SDF", "SDF Settings"),
    ("LOD", "Level of Detail"),
    ("Animation", "Animation Header"),
    ("Raw", "VDF Raw Data"),
)
INFO_BUTTONS = (
    ("Redux Model System Info", "DUAL_COMPONENT_INFO_LINES"),
    ("Advanced VDF Notes", "ADVANCED_VDF_INFO_LINES"),
    ("Pivot / Dummyroot Info", "PIVOT_DUMMYROOT_INFO_LINES"),
    ("Validation Checks", "VALIDATION_CHECK_LINES"),
)


# Fixed height for wrapped labels: six lines, room for the longest GEO hint
# (~190 chars) and the eyepoint scale warning at the panel's 320 px minimum
# width. Maya sizes a frameLayout once, so a wordWrap text that grows on
# refresh squashes the rows below it.
WRAPPED_LABEL_HEIGHT = 84

# --- controller ----------------------------------------------------------------

def selected_part() -> Optional[str]:
    """Long name of the first selected transform (a shape selects its transform)."""
    for item in cmds.ls(selection=True, long=True) or []:
        if "transform" in (cmds.nodeType(item, inherited=True) or []):
            return item
        parents = cmds.listRelatives(item, parent=True, fullPath=True)
        if parents:
            return parents[0]
    return None


def part_state(node: Optional[str]) -> Dict[str, Any]:
    """Attribute values for ``node``: its own where tagged, Blender's defaults elsewhere."""
    props = attributes.read_part(node) if node and attributes.is_part(node) else {}
    values, _extra = authoring.part_attr_values(props)
    return values


def set_part_attr(node: str, attr: str, value: Any) -> None:
    """Set one authoring attribute (tagging the transform first if needed)."""
    fld = authoring.fields_by_attr(schema.part_fields())[attr]
    attributes.ensure_part(node)
    attributes.set_field(node, fld, value)


def effective_flags(node: Optional[str]) -> int:
    props = attributes.read_part(node) if node and attributes.is_part(node) else {}
    return int(props.get("flags", 0)) & 0xFFFFFFFF


def flags_unknown_hex(node: Optional[str]) -> str:
    authoring.part_known_keys()
    sem = authoring._semantics()
    return sem.format_hex(sem.unknown_flag_bits(effective_flags(node)))


def model_node(selection: Optional[str] = None) -> Optional[str]:
    """The group carrying the model header: selected / an ancestor of it, else the first in the scene."""
    candidates = []
    if selection:
        candidates.append(selection)
        path = selection
        while "|" in path.lstrip("|"):
            path = path.rsplit("|", 1)[0]
            candidates.append(path)
    for node in candidates:
        if attributes.is_model(node):
            return node
    for node in cmds.ls(type="transform", long=True) or []:
        if attributes.is_model(node):
            return node
    return None


def create_model_node(name: str = "bz_model") -> str:
    node = cmds.group(empty=True, name=name)
    node = cmds.ls(node, long=True)[0]
    attributes.write_model(node, {})
    return node


def model_state(node: Optional[str]) -> Dict[str, Any]:
    meta = attributes.read_model(node) if node and attributes.is_model(node) else {}
    values, _extra = authoring.meta_attr_values(meta)
    return values


def set_model_attr(node: str, attr: str, value: Any) -> None:
    fld = authoring.fields_by_attr(schema.model_fields())[attr]
    attributes.ensure_model(node)
    attributes.set_field(node, fld, value)


def role_text(type_value: int) -> str:
    return "Selected Role: %s" % schema.geo_type_label(int(type_value))


def hint_text(type_value: int) -> str:
    hint = schema.geo_type_hints().get(int(type_value))
    return ("%s: %s" % hint) if hint else ""


def lod_text(name: str) -> str:
    """Blender's per-object LOD label (character 4 of the name)."""
    if len(name) >= 4 and name[3] in "123":
        return "LOD%s part" % name[3]
    return "No LOD marker (name needs 1/2/3 as 4th character)"


def eyepoint_scale_warning(node: Optional[str]) -> str:
    if not node:
        return ""
    scale = cmds.getAttr(node + ".scale")[0]
    if any(abs(v - 1.0) > 0.001 for v in scale):
        return (
            "Object scale is (%.2f, %.2f, %.2f). The engine multiplies POV basis rows by 25, so scale "
            "directly changes first-person framing/head-bob - apply scale deliberately or clear it." % tuple(scale)
        )
    return "Unit scale (safe for camera math)."


def run_validation(preset: str = "AUTO") -> List[dict]:
    return maya_io.validate_scene("ALL", preset)


def issue_line(issue: dict) -> str:
    target = issue.get("target") or ""
    return "[%s] %s%s: %s" % (issue["severity"], target + " - " if target else "", issue.get("scope", ""), issue["message"])


def select_issue_target(issue: dict) -> Optional[str]:
    """Select the Maya transform an issue points at; returns its long name."""
    name = issue.get("object_name") or ""
    if not name:
        return None
    for transform in cmds.ls(type="transform", long=True) or []:
        part = cmds.getAttr(transform + "." + attributes.PART_NAME_ATTR) if attributes.exists(
            transform, attributes.PART_NAME_ATTR
        ) else transform.split("|")[-1]
        if part == name:
            cmds.select(transform, replace=True)
            return transform
    return None


def fix_issue_name(issue: dict) -> Optional[str]:
    node = select_issue_target(issue)
    return maya_io.fix_legacy_name(node) if node else None


def info_text(constant: str) -> str:
    return "\n".join(schema.info_lines(constant))


# --- view ------------------------------------------------------------------

class _Form:
    """Controls generated from the schema, refreshed from the current selection."""

    def __init__(self):
        self.part_controls: Dict[str, tuple] = {}
        self.model_controls: Dict[str, tuple] = {}
        self.labels: Dict[str, str] = {}
        self.issues: List[dict] = []
        self.node: Optional[str] = None
        self.model: Optional[str] = None
        self.preset = "AUTO"
        self.enum_values: Dict[str, Dict[str, int]] = {}  # attr -> {menu label: value}

    # --- field widgets
    def _field_row(self, fld: schema.Field, controls: Dict[str, tuple], on_change: Callable, label_text=None):
        tip = fld.tip or fld.label
        text = label_text or fld.label or fld.blender
        kind = fld.kind
        if kind == "bool":
            ctrl = cmds.checkBox(label=text, annotation=tip, changeCommand=lambda *_a, f=fld: on_change(f, None))
        elif kind == "enum":
            cmds.rowLayout(numberOfColumns=2, columnWidth2=(150, 220), adjustableColumn=2)
            cmds.text(label=text, align="left", annotation=tip)
            ctrl = cmds.optionMenu(annotation=tip, changeCommand=lambda *_a, f=fld: on_change(f, None))
            self.enum_values[fld.attr] = dict(fld.enum)
            for label, _value in fld.enum:
                cmds.menuItem(label=label)
            cmds.setParent("..")
        elif kind == "string":
            cmds.rowLayout(numberOfColumns=2, columnWidth2=(150, 220), adjustableColumn=2)
            cmds.text(label=text, align="left", annotation=tip)
            ctrl = cmds.textField(annotation=tip, changeCommand=lambda *_a, f=fld: on_change(f, None))
            cmds.setParent("..")
        elif kind == "int":
            ctrl = cmds.intFieldGrp(label=text, annotation=tip, columnWidth2=(150, 100),
                                    changeCommand=lambda *_a, f=fld: on_change(f, "grp"))
        elif kind == "float":
            ctrl = cmds.floatFieldGrp(label=text, annotation=tip, columnWidth2=(150, 100), precision=4,
                                      changeCommand=lambda *_a, f=fld: on_change(f, "grp"))
        elif kind == "float3":
            ctrl = cmds.floatFieldGrp(label=text, numberOfFields=3, annotation=tip, precision=4,
                                      columnWidth4=(150, 70, 70, 70),
                                      changeCommand=lambda *_a, f=fld: on_change(f, "grp"))
        elif kind == "int3":
            ctrl = cmds.intFieldGrp(label=text, numberOfFields=3, annotation=tip, columnWidth4=(150, 70, 70, 70),
                                    changeCommand=lambda *_a, f=fld: on_change(f, "grp"))
        elif kind == "floatarray":
            cmds.columnLayout(adjustableColumn=True, annotation=tip)
            cmds.text(label=text, align="left", annotation=tip)
            rows = []
            for r in range(3):
                rows.append(cmds.floatFieldGrp(numberOfFields=4, precision=4, annotation=tip,
                                               changeCommand=lambda *_a, f=fld: on_change(f, "grp")))
            ctrl = rows
            cmds.setParent("..")
        else:
            return
        controls[fld.attr] = (ctrl, fld)

    # --- reads / writes
    def _control_value(self, ctrl, fld: schema.Field):
        kind = fld.kind
        if kind == "bool":
            return cmds.checkBox(ctrl, query=True, value=True)
        if kind == "enum":
            label = cmds.optionMenu(ctrl, query=True, value=True)
            return self.enum_values[fld.attr].get(label, 0)
        if kind == "string":
            return cmds.textField(ctrl, query=True, text=True)
        if kind == "int":
            return cmds.intFieldGrp(ctrl, query=True, value1=True)
        if kind == "float":
            return cmds.floatFieldGrp(ctrl, query=True, value1=True)
        if kind == "float3":
            return tuple(cmds.floatFieldGrp(ctrl, query=True, value=True)[:3])
        if kind == "int3":
            return tuple(cmds.intFieldGrp(ctrl, query=True, value=True)[:3])
        if kind == "floatarray":
            out = []
            for row in ctrl:
                out.extend(cmds.floatFieldGrp(row, query=True, value=True)[:4])
            return tuple(out)
        return None

    def _set_control(self, ctrl, fld: schema.Field, value) -> None:
        kind = fld.kind
        if kind == "bool":
            cmds.checkBox(ctrl, edit=True, value=bool(value))
        elif kind == "enum":
            known = self.enum_values[fld.attr]
            by_value = {v: lab for lab, v in known.items()}
            label = by_value.get(int(value))
            if label is None:  # raw value outside the list: show it honestly
                label = "Unknown (0x%X)" % (int(value) & 0xFFFFFFFF)
                cmds.menuItem(label=label, parent=ctrl)
                known[label] = int(value)
            cmds.optionMenu(ctrl, edit=True, value=label)
        elif kind == "string":
            cmds.textField(ctrl, edit=True, text=value or "")
        elif kind == "int":
            cmds.intFieldGrp(ctrl, edit=True, value1=int(value))
        elif kind == "float":
            cmds.floatFieldGrp(ctrl, edit=True, value1=float(value))
        elif kind == "float3":
            cmds.floatFieldGrp(ctrl, edit=True, value=[float(v) for v in value] + [0.0])
        elif kind == "int3":
            cmds.intFieldGrp(ctrl, edit=True, value=[int(v) for v in value] + [0])
        elif kind == "floatarray":
            values = list(value) + [0.0] * (12 - len(value))
            for i, row in enumerate(ctrl):
                cmds.floatFieldGrp(row, edit=True, value=values[i * 4:(i + 1) * 4])

    def on_part_change(self, fld: schema.Field, raw):
        if not self.node:
            return
        ctrl, _ = self.part_controls[fld.attr]
        set_part_attr(self.node, fld.attr, self._control_value(ctrl, fld))
        self.refresh()

    def on_model_change(self, fld: schema.Field, raw):
        ctrl, _ = self.model_controls[fld.attr]
        if not self.model:
            self.model = create_model_node()
        set_model_attr(self.model, fld.attr, self._control_value(ctrl, fld))
        self.refresh()

    # --- layout
    def build(self):
        cmds.scrollLayout(childResizable=True, horizontalScrollBarThickness=0)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        cmds.frameLayout(label="Selected Part", collapsable=True, collapse=False, marginWidth=6)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
        self.labels["name"] = cmds.text(label="", align="left", font="boldLabelFont")
        self.labels["info"] = cmds.text(label="", align="left")
        self.labels["role"] = cmds.text(label="", align="left")
        self.labels["hint"] = cmds.text(label="", align="left", wordWrap=True, height=WRAPPED_LABEL_HEIGHT)
        cmds.button(label="Tag Selected as BZ Part", height=24, command=lambda *_a: self.tag_selected(),
                    annotation="Add every Battlezone attribute (Blender's defaults) to the selected transform")
        cmds.button(label="Generate Collision Settings", height=24, command=lambda *_a: self.generate_collision(),
                    annotation="Write mesh-derived GEO center, sphere radius and projectile box into the attributes")
        cmds.setParent("..")
        cmds.setParent("..")

        part_fields = schema.part_fields()
        for group, title, opened in GROUP_ORDER:
            cmds.frameLayout(label=title, collapsable=True, collapse=not opened, marginWidth=6)
            cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
            for fld in part_fields:
                if fld.group == group:
                    self._field_row(fld, self.part_controls, self.on_part_change)
                    if fld.blender == "GEOFlags":
                        self.labels["flags_hex"] = cmds.text(label="", align="left")
                    if fld.blender == "ObjFlagTeam":
                        self.labels["flags_unknown"] = cmds.text(label="", align="left",
                                                                 annotation=self._tip("FlagsUnknownHex"))
            if group == "Eyepoint":
                self.labels["eye"] = cmds.text(label="", align="left", wordWrap=True,
                                               height=WRAPPED_LABEL_HEIGHT)
            cmds.setParent("..")
            cmds.setParent("..")

        cmds.frameLayout(label="Model (VDF / SDF header)", collapsable=True, collapse=False, marginWidth=6)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
        self.labels["model"] = cmds.text(label="", align="left")
        model_fields = schema.model_fields()
        for group, title in MODEL_GROUPS:
            cmds.text(label=title, align="left", font="boldLabelFont")
            for fld in model_fields:
                if fld.group == group:
                    self._field_row(fld, self.model_controls, self.on_model_change)
        cmds.setParent("..")
        cmds.setParent("..")

        cmds.frameLayout(label="Animation Slots (VDF / SDF ANIM)", collapsable=True, collapse=True, marginWidth=6)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
        self.slot_list = cmds.textScrollList(height=110, allowMultiSelection=False,
                                             selectCommand=lambda *_a: self.on_slot_select())
        self.slot_fields = {}
        for key, kind in (("index", "int"), ("start", "int"), ("length", "int"), ("loop", "int"), ("speed", "float")):
            maker = cmds.intFieldGrp if kind == "int" else cmds.floatFieldGrp
            self.slot_fields[key] = maker(label=key.capitalize(), numberOfFields=1, columnWidth2=(70, 70),
                                          changeCommand=lambda *_a, k=key: self.on_slot_edit(k))
        cmds.rowLayout(numberOfColumns=5, adjustableColumn=1)
        cmds.button(label="Add", command=lambda *_a: self.slot_action("add"))
        cmds.button(label="Duplicate", command=lambda *_a: self.slot_action("duplicate"))
        cmds.button(label="Remove", command=lambda *_a: self.slot_action("remove"))
        cmds.button(label="Up", command=lambda *_a: self.slot_action("up"))
        cmds.button(label="Down", command=lambda *_a: self.slot_action("down"))
        cmds.setParent("..")
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1)
        self.preset_option = cmds.optionMenu()
        for item in anim_core.preset_items():
            cmds.menuItem(label=item[1], annotation=item[2])
        cmds.button(label="Add Animation Preset", command=lambda *_a: self.slot_action("preset"))
        cmds.setParent("..")
        cmds.button(label="Animation Index Reference", command=lambda *_a: cmds.confirmDialog(
            title="Animation Index Reference", message=anim_core.reference_text(), button=["OK"]))
        cmds.separator(style="in")
        cmds.text(label="Mirror Legacy Animation Keys", align="left", font="boldLabelFont")
        cmds.rowLayout(numberOfColumns=3, adjustableColumn=2)
        cmds.text(label="Source / Target token")
        self.mirror_tokens = cmds.textField(text=".L")
        self.mirror_target = cmds.textField(text=".R")
        cmds.setParent("..")
        self.mirror_axis = cmds.optionMenu(label="Axis")
        for axis_name in ("X", "Y", "Z"):
            cmds.menuItem(label=axis_name)
        cmds.button(label="Mirror Keys (selection, or whole scene)",
                    command=lambda *_a: self.slot_action("mirror"),
                    annotation="Copy each animated part's keys onto its counterpart named with the target token")
        cmds.setParent("..")
        cmds.setParent("..")

        cmds.frameLayout(label="Validation", collapsable=True, collapse=False, marginWidth=6)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2)
        cmds.text(label="Preset")
        self.preset_menu = cmds.optionMenu(changeCommand=lambda v: setattr(self, "preset", self._preset_id(v)))
        for item in schema.blender_constant("VALIDATION_PRESET_ITEMS") or (("AUTO", "General", ""),):
            cmds.menuItem(label=item[1], annotation=item[2])
        cmds.setParent("..")
        cmds.button(label="Validate Battlezone Scene", command=lambda *_a: self.validate(),
                    annotation="Validate scene objects for legacy Battlezone export")
        self.issue_list = cmds.textScrollList(height=140, allowMultiSelection=False,
                                              selectCommand=lambda *_a: self.on_issue_select())
        self.labels["issue_summary"] = cmds.text(label="", align="left")
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1)
        cmds.button(label="Select Target", command=lambda *_a: self.on_issue_select())
        cmds.button(label="Fix Legacy Name", command=lambda *_a: self.on_fix_name(),
                    annotation="Rename this object to a valid legacy Battlezone export name")
        cmds.setParent("..")
        cmds.setParent("..")
        cmds.setParent("..")

        cmds.frameLayout(label="GEO Type Reference", collapsable=True, collapse=True, marginWidth=6)
        cmds.scrollField(text=schema.geo_type_reference_text(), editable=False, wordWrap=False, height=220)
        cmds.setParent("..")

        cmds.frameLayout(label="Tools", collapsable=True, collapse=False, marginWidth=6)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
        cmds.button(label="Create Vehicle GEO Set", command=lambda *_a: self.run(maya_io.create_vehicle_geo_set),
                    annotation="Create a movable starter set of tiny standard vehicle GEOs")
        cmds.button(label="Select Mesh Parts", command=lambda *_a: self.run(maya_io.select_battlezone_objects, "MESH"))
        cmds.button(label="Select Non-Mesh Parts", command=lambda *_a: self.run(maya_io.select_battlezone_objects, "NON_MESH"))
        for label, constant in INFO_BUTTONS:
            cmds.button(label=label, command=lambda *_a, c=constant, t=label: cmds.confirmDialog(
                title=t, message=info_text(c), button=["OK"]))
        cmds.setParent("..")
        cmds.setParent("..")

        cmds.setParent("..")
        cmds.setParent("..")
        self.job = cmds.scriptJob(event=["SelectionChanged", self.refresh], parent=self.labels["name"])
        self.refresh()

    # --- animation slots
    def _slot_model(self) -> Optional[str]:
        return model_node(None)

    def refresh_slots(self, select: Optional[int] = None) -> None:
        model = self._slot_model()
        items = animation_tools.slots(model) if model else []
        cmds.textScrollList(self.slot_list, edit=True, removeAll=True)
        for item in items:
            cmds.textScrollList(self.slot_list, edit=True, append="slot %d: start %d, length %d, loop %d, speed %g" % (
                item["index"], item.get("start", 0), item.get("length", 0), item.get("loop", 0), item.get("speed", 15.0)))
        if select is not None and items:
            cmds.textScrollList(self.slot_list, edit=True, selectIndexedItem=min(select, len(items)) + 1)
        self.on_slot_select()

    def _slot_position(self) -> Optional[int]:
        picked = cmds.textScrollList(self.slot_list, query=True, selectIndexedItem=True)
        return picked[0] - 1 if picked else None

    def on_slot_select(self) -> None:
        model, position = self._slot_model(), self._slot_position()
        items = animation_tools.slots(model) if model else []
        item = items[position] if position is not None and position < len(items) else None
        for key, control in self.slot_fields.items():
            value = item.get(key, 0) if item else 0
            if key == "speed":
                cmds.floatFieldGrp(control, edit=True, value1=float(value))
            else:
                cmds.intFieldGrp(control, edit=True, value1=int(value))

    def on_slot_edit(self, key: str) -> None:
        model, position = self._slot_model(), self._slot_position()
        if not model or position is None:
            return
        control = self.slot_fields[key]
        value = cmds.floatFieldGrp(control, query=True, value1=True) if key == "speed" else cmds.intFieldGrp(
            control, query=True, value1=True)
        animation_tools.update_slot(model, position, **{key: value})
        self.refresh_slots(position)

    def slot_action(self, action: str) -> None:
        try:
            model = self._slot_model() or create_model_node()
            position = self._slot_position()
            select = position
            if action == "add":
                select = len(animation_tools.add_slot(model)) - 1
            elif action == "duplicate" and position is not None:
                animation_tools.duplicate_slot(model, position)
                select = position + 1
            elif action == "remove" and position is not None:
                animation_tools.remove_slot(model, position)
                select = max(0, position - 1)
            elif action in ("up", "down") and position is not None:
                select = animation_tools.move_slot(model, position, action.upper())
            elif action == "preset":
                label = cmds.optionMenu(self.preset_option, query=True, value=True)
                key = next(i[0] for i in anim_core.preset_items() if i[1] == label)
                animation_tools.add_preset(model, key)
            elif action == "mirror":
                count = animation_tools.mirror_keys(
                    cmds.textField(self.mirror_tokens, query=True, text=True),
                    cmds.textField(self.mirror_target, query=True, text=True),
                    cmds.optionMenu(self.mirror_axis, query=True, value=True))
                cmds.inViewMessage(amg="Battlezone: mirrored keys on %d part(s)." % count, pos="midCenter", fade=True)
            self.refresh_slots(select)
        except Exception as exc:
            cmds.confirmDialog(title="Battlezone", message=str(exc), button=["OK"], icon="critical")

    # --- helpers
    @staticmethod
    def _tip(blender: str) -> str:
        return (schema.blender_properties("GEOPropertyGroup").get(blender) or {}).get("description", "")

    @staticmethod
    def _preset_id(label: str) -> str:
        for item in schema.blender_constant("VALIDATION_PRESET_ITEMS") or ():
            if item[1] == label:
                return item[0]
        return "AUTO"

    def run(self, func, *args):
        try:
            func(*args)
        except Exception as exc:
            cmds.warning("Battlezone: %s" % exc)
        self.refresh()

    def tag_selected(self):
        node = selected_part()
        if node:
            attributes.ensure_part(node)
        self.refresh()

    def generate_collision(self):
        node = selected_part()
        if node:
            self.run(maya_io.generate_collision, node)

    def validate(self):
        try:
            self.issues = run_validation(self.preset)
        except Exception as exc:
            self.issues = []
            cmds.warning("Battlezone: %s" % exc)
        cmds.textScrollList(self.issue_list, edit=True, removeAll=True)
        for issue in self.issues:
            cmds.textScrollList(self.issue_list, edit=True, append=issue_line(issue))
        found = validate.counts(self.issues, "ALL")
        cmds.text(self.labels["issue_summary"], edit=True, label="%d errors, %d warnings, %d info" % (
            found["ERROR"], found["WARNING"], found["INFO"]))

    def _selected_issue(self) -> Optional[dict]:
        picked = cmds.textScrollList(self.issue_list, query=True, selectIndexedItem=True) or []
        return self.issues[picked[0] - 1] if picked else None

    def on_issue_select(self):
        issue = self._selected_issue()
        if issue:
            select_issue_target(issue)

    def on_fix_name(self):
        issue = self._selected_issue()
        if issue and issue.get("action") == "fix_name":
            self.run(fix_issue_name, issue)
            self.validate()

    def refresh(self, *_args):
        self.node = selected_part()
        self.model = model_node(self.node)
        self.refresh_slots(self._slot_position() or 0)
        values = part_state(self.node)
        tagged = bool(self.node and attributes.is_part(self.node))
        for attr, (ctrl, fld) in self.part_controls.items():
            if attr in values:
                self._set_control(ctrl, fld, values[attr])
        type_value = int(values["bzGEOType"])
        short = self.node.split("|")[-1] if self.node else "(nothing selected)"
        cmds.text(self.labels["name"], edit=True, label=short + ("" if tagged or not self.node else "  (not tagged: edits will tag it)"))
        cmds.text(self.labels["info"], edit=True, label=lod_text(short) if self.node else "")
        cmds.text(self.labels["role"], edit=True, label=role_text(type_value))
        cmds.text(self.labels["hint"], edit=True, label=hint_text(type_value))
        cmds.text(self.labels["flags_hex"], edit=True, label="Raw (effective): 0x%08X" % effective_flags(self.node))
        cmds.text(self.labels["flags_unknown"], edit=True, label="Unknown bits: %s" % flags_unknown_hex(self.node))
        cmds.text(self.labels["eye"], edit=True, label=eyepoint_scale_warning(self.node) if type_value == 40 else "")
        cmds.text(self.labels["model"], edit=True, label=("Group: " + self.model.split("|")[-1]) if self.model
                  else "No model group yet: editing a field creates one.")
        mvalues = model_state(self.model)
        for attr, (ctrl, fld) in self.model_controls.items():
            if attr in mvalues:
                self._set_control(ctrl, fld, mvalues[attr])


_FORM: Optional[_Form] = None


def build_ui() -> _Form:
    """Create the panel controls in the current layout (the workspace control's uiScript)."""
    global _FORM
    _FORM = _Form()
    _FORM.build()
    return _FORM


def show_panel() -> None:
    """Open (or raise) the dockable panel. Needs a main window."""
    if cmds.about(batch=True):
        raise RuntimeError("the Battlezone panel needs the Maya UI (batch mode has none)")
    if cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
        cmds.workspaceControl(WORKSPACE_CONTROL, edit=True, restore=True)
        return
    cmds.workspaceControl(
        WORKSPACE_CONTROL,
        label="Battlezone",
        uiScript="import bz98maya.panel as bz_panel; bz_panel.build_ui()",
        retain=False,
        initialWidth=420,
        minimumWidth=320,
        floating=False,
        dockToMainWindow=("right", False),
    )


def close_panel() -> None:
    if not cmds.about(batch=True) and cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL, control=True)
