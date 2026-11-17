"""Export dialog controller (Maya-free): kind, mode, option values and presets.

``ExportSession`` holds the option values for one export kind, switches Blender's three
modes (Legacy only / Legacy + Redux / Redux only), applies / saves / deletes presets and
hands the export to a ``runner(kind, path, options)`` (the Maya layer supplies it).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import export_options as eo

LEGACY_KINDS = ("VDF", "SDF", "GEO")
MODES = (eo.MODE_LEGACY, eo.MODE_LEGACY_REDUX, eo.MODE_REDUX)


class ExportSession:
    def __init__(self, kind: str = "VDF", preset_dir: Optional[str] = None, runner: Optional[Callable] = None):
        self.runner = runner
        self.kind = kind
        self.preset_dir = preset_dir
        self.options: Dict[str, Any] = eo.default_options(kind)
        self.memory: Dict[str, Any] = {}  # values set so far, carried across kinds that share the option
        self.last_legacy_kind = kind if kind in LEGACY_KINDS else "VDF"

    # --- kind / mode
    def set_kind(self, kind: str) -> None:
        if kind not in eo.KINDS:
            raise ValueError("unknown export kind %s" % kind)
        if kind in LEGACY_KINDS:
            self.last_legacy_kind = kind
        self.memory.update(self.options)
        self.kind = kind
        self.options = eo.default_options(kind)
        self.options.update({k: v for k, v in self.memory.items() if k in self.options})

    @property
    def mode(self) -> str:
        return eo.export_mode(self.kind, self.options)

    def set_mode(self, mode: str) -> None:
        if mode == eo.MODE_REDUX:
            self.set_kind("MESH")
            return
        if self.kind == "MESH":
            self.set_kind(self.last_legacy_kind)
        self.options["auto_port_ogre"] = mode == eo.MODE_LEGACY_REDUX

    def set_option(self, name: str, value: Any) -> None:
        if name not in self.options:
            raise KeyError("%s has no option %s" % (self.kind, name))
        self.options[name] = list(value) if isinstance(value, (list, tuple)) else value

    @property
    def mode_text(self) -> str:
        return eo.MODE_TEXT[self.mode]

    # --- presets (Blender: Apply / Save / Delete Export Preset; MESH has none)
    def builtin_presets(self) -> List[tuple]:
        return [(key, label) for key, label, _values in eo.builtin_presets(self.kind)]

    def custom_presets(self) -> List[str]:
        return [entry["label"] for entry in eo.list_custom_presets(self.kind, self._dir())]

    def _dir(self) -> Optional[str]:
        return self.preset_dir if self.preset_dir else None

    def apply_builtin(self, key: str) -> None:
        for preset_key, _label, values in eo.builtin_presets(self.kind):
            if preset_key == key:
                self.options = eo.apply_preset_values(self.kind, self.options, values)
                return
        raise KeyError("no built-in preset %s for %s" % (key, self.kind))

    def apply_custom(self, label: str) -> None:
        values = eo.load_custom_preset(self.kind, label, self._dir())
        if values is None:
            raise KeyError("preset %r could not be loaded" % label)
        self.options = eo.apply_preset_values(self.kind, self.options, values)

    def save_preset(self, label: str) -> str:
        return eo.save_custom_preset(self.kind, label, self.options, self._dir())

    def delete_preset(self, label: str) -> bool:
        return eo.delete_custom_preset(self.kind, label, self._dir())

    # --- run
    def extension(self) -> str:
        return eo.EXTENSION[self.kind]

    def export(self, path: str):
        if self.runner is None:
            raise RuntimeError("no export runner is attached to this session")
        return self.runner(self.kind, path, dict(self.options))
