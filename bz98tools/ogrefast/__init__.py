from typing import Any
import importlib
import importlib.machinery
import os
import platform
import sys

_VENDOR_ROOT = os.path.join(os.path.dirname(__file__), "vendor")
_NATIVE_ROOT = os.path.join(_VENDOR_ROOT, "kenshi_blender_tool")
_DLL_DIR_HANDLE = None
_PROBE_RESULT = None


def _prepare_native_import():
    global _DLL_DIR_HANDLE

    if _VENDOR_ROOT not in sys.path:
        sys.path.insert(0, _VENDOR_ROOT)

    if os.name == "nt" and os.path.isdir(_NATIVE_ROOT):
        if hasattr(os, "add_dll_directory"):
            if _DLL_DIR_HANDLE is None:
                _DLL_DIR_HANDLE = os.add_dll_directory(_NATIVE_ROOT)
        else:
            os.environ["PATH"] = _NATIVE_ROOT + os.pathsep + os.environ.get("PATH", "")


def _has_runtime_extension():
    if not os.path.isdir(_NATIVE_ROOT):
        return False, "native Ogre backend files are missing"

    abi_suffixes = tuple(
        suffix for suffix in importlib.machinery.EXTENSION_SUFFIXES if suffix != ".pyd"
    )
    generic_suffix = ".pyd"
    for filename in os.listdir(_NATIVE_ROOT):
        if filename.startswith("Kenshi_blender_tool") and filename.endswith(
            abi_suffixes
        ):
            return True, None
        if filename == f"Kenshi_blender_tool{generic_suffix}":
            return True, None

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    found = sorted(
        filename
        for filename in os.listdir(_NATIVE_ROOT)
        if filename.startswith("Kenshi_blender_tool") and filename.endswith(".pyd")
    )
    expected = ", ".join(abi_suffixes)
    return (
        False,
        f"native Ogre backend has no Python {version} extension; "
        f"expected suffix {expected}; found {found or 'none'}",
    )


def probe_native_backend(force=False):
    global _PROBE_RESULT

    if _PROBE_RESULT is not None and not force:
        return _PROBE_RESULT

    if os.name != "nt":
        _PROBE_RESULT = (
            False,
            "native Ogre backend is currently bundled for Windows only",
        )
        return _PROBE_RESULT

    if platform.machine().lower() not in {"amd64", "x86_64"}:
        _PROBE_RESULT = (False, "native Ogre backend requires a 64-bit Python runtime")
        return _PROBE_RESULT

    has_extension, reason = _has_runtime_extension()
    if not has_extension:
        _PROBE_RESULT = (False, reason)
        return _PROBE_RESULT

    try:
        importlib.import_module("numpy")
    except Exception as exc:
        _PROBE_RESULT = (False, f"numpy is unavailable: {exc}")
        return _PROBE_RESULT

    try:
        _prepare_native_import()
        module = importlib.import_module("kenshi_blender_tool")
        if getattr(module, "__bz98_pure_backend__", False):
            _PROBE_RESULT = (
                False,
                "pure kenshi_blender_tool compatibility facade is active",
            )
        else:
            _PROBE_RESULT = (True, None)
    except Exception as exc:
        _PROBE_RESULT = (False, str(exc))

    return _PROBE_RESULT


def ensure_kenshi_blender_tool():
    """Return the native module or install the pure drop-in compatibility facade.

    Existing Blender collector code imports ``kenshi_blender_tool`` directly.
    Installing the pure module under that historical import name lets the same
    collector run on Python ABIs where the old ``.pyd`` cannot load, while
    keeping CPython 3.11/native behavior unchanged.
    """

    existing = sys.modules.get("kenshi_blender_tool")
    if existing is not None:
        return existing

    native_available, native_reason = probe_native_backend()
    if native_available:
        return importlib.import_module("kenshi_blender_tool")

    facade = importlib.import_module(f"{__name__}.pure.kenshi_facade")
    facade.__bz98_native_unavailable_reason__ = native_reason
    sys.modules["kenshi_blender_tool"] = facade
    return facade


# Install the compatibility surface before any ogrefast importer/exporter
# submodule executes its historical ``from kenshi_blender_tool import *``.
ensure_kenshi_blender_tool()
