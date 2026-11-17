"""Stock ``.map`` texture lookup and conversion to PNG (Maya-free).

Blender's importer finds a ``.map`` (import folder, a user folder, or extracted from a stock
ZFS archive), decodes it with the toolkit's ACT palettes and loads the result as an image.
That code lives in ``bz98tools/import_geo.py``, which imports ``bpy``; rather than copy it,
the module is compiled here from its source with only ``import bpy`` and the add-on
reload call left out, so the lookup rules, palettes and decoders are the toolkit's own.
Only the last step (turning pixels into an image) differs: a PNG file Maya can load.
"""

from __future__ import annotations

import ast
import os
import struct
import types
import zlib
from typing import Dict, List, Optional

from .toolkit import ensure_toolkit, find_toolkit_dir

_NS: Dict[str, object] = {}


def _namespace() -> Dict[str, object]:
    if _NS:
        return _NS
    ensure_toolkit()
    path = os.path.join(find_toolkit_dir(), "import_geo.py")
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    ns: Dict[str, object] = {
        "__name__": "bz98tools.import_geo",
        "__package__": "bz98tools",
        "__file__": path,
        "bpy": types.SimpleNamespace(
            path=types.SimpleNamespace(abspath=lambda p: os.path.abspath(p) if p else "")
        ),
    }
    for node in tree.body:
        if isinstance(node, ast.Import) and any(alias.name == "bpy" for alias in node.names):
            continue
        if isinstance(node, ast.Expr) and "reload" in ast.dump(node):
            continue
        exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    _NS.update(ns)
    return _NS


def resolve_map_file(map_name: str, base_dir: str, texture_dir: str = "", texture_zfs: str = "") -> Optional[str]:
    """Blender's ``_resolve_map_file``: base folder, texture folder, then the ZFS archive."""
    return _namespace()["_resolve_map_file"](map_name, base_dir, texture_dir, texture_zfs)


def _write_rgba_png(path: str, width: int, height: int, rgba: bytes) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    stride = width * 4
    raw = b"".join(b"\0" + rgba[row * stride:(row + 1) * stride] for row in range(height))
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        handle.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        handle.write(chunk(b"IEND", b""))


def map_to_png(map_path: str) -> Optional[str]:
    """Blender's ``_load_bzmap_to_image`` without the image: returns the .png path (next to the .map)."""
    ns = _namespace()
    map_path = os.fspath(map_path)
    png_path = os.path.splitext(map_path)[0] + ".png"
    if os.path.exists(png_path):
        return png_path
    try:
        with open(map_path, "rb") as handle:
            header = handle.read(8)
    except OSError:
        return None
    if len(header) < 8:
        return None
    row_bytes, pixel_format, height, _unknown = struct.unpack("<4H", header)
    BZMapFormat = ns["BZMapFormat"]
    if pixel_format == BZMapFormat.INDEXED:
        palette = ns["_get_palette_for_dir"](os.path.dirname(map_path))
        if not palette:
            return None
        return ns["_write_indexed_map_to_png"](map_path, palette, png_path)

    try:
        bz = ns["BZMap"]()
        with open(map_path, "rb") as handle:
            ns["BZMapSerializer"]().deserialize(handle, bz)
    except Exception:
        return None
    width, height = bz.get_size()
    buf = bz.get_buffer()
    if buf is None:
        return None
    stride = bz.row_byte_size
    fmt = bz.pixel_format
    if fmt == BZMapFormat.ARGB4444:
        pixels = ns["_decode_argb4444"](buf, width, height, stride)
    elif fmt == BZMapFormat.RGB565:
        pixels = ns["_decode_rgb565"](buf, width, height, stride)
    elif fmt == BZMapFormat.ARGB8888:
        pixels = ns["_decode_argb8888"](buf, width, height, stride, xrgb=False)
    elif fmt == BZMapFormat.XRGB8888:
        pixels = ns["_decode_argb8888"](buf, width, height, stride, xrgb=True)
    else:
        return None
    if len(pixels) != width * height * 4:
        return None
    # Blender image pixels are bottom-up floats 0..1; PNG rows are top-down bytes.
    data = bytes(max(0, min(255, int(round(float(v) * 255.0)))) for v in pixels)
    row = width * 4
    top_down = b"".join(data[r * row:(r + 1) * row] for r in range(height - 1, -1, -1))
    _write_rgba_png(png_path, width, height, top_down)
    return png_path


def texture_for_map_name(
    map_name: str, base_dir: str, texture_dir: str = "", texture_zfs: str = ""
) -> Optional[str]:
    """PNG for a face's ``.map`` texture name (found per Blender's rules, converted if needed)."""
    map_path = resolve_map_file(map_name, base_dir, texture_dir, texture_zfs)
    return map_to_png(map_path) if map_path else None
