# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Pure-Python helpers for MakeOBJ-compatible GEO authoring.

The algorithms in this module are reconstructed from MakeObj 1.1.5 (2015-01-24)
static analysis. They intentionally live outside Blender so their behavior can be
covered by the normal bpy-free test suite.
"""

from __future__ import annotations

import math


def _normalize3(value, fallback=(0.0, 0.0, 1.0)):
    x, y, z = (float(value[0]), float(value[1]), float(value[2]))
    length_sq = x * x + y * y + z * z
    if length_sq <= 1.0e-30:
        return tuple(float(v) for v in fallback)
    inv = 1.0 / math.sqrt(length_sq)
    return (x * inv, y * inv, z * inv)


def makeobj_vertex_normals(
    vertex_count,
    face_vertices,
    face_normals,
    *,
    fallback_normals=None,
):
    """Rebuild vertex normals the way legacy MakeOBJ does.

    MakeOBJ adds the (unit) normal of every face that references a vertex, then
    normalizes that sum. Face area is deliberately *not* used as a weight.
    ``fallback_normals`` is only used for vertices that have no usable adjacent
    face normal.
    """

    vertex_count = max(0, int(vertex_count))
    sums = [[0.0, 0.0, 0.0] for _ in range(vertex_count)]
    touched = [False] * vertex_count

    for vertices, normal in zip(face_vertices, face_normals):
        nx, ny, nz = _normalize3(normal, fallback=(0.0, 0.0, 0.0))
        if nx == 0.0 and ny == 0.0 and nz == 0.0:
            continue
        for raw_index in vertices:
            index = int(raw_index)
            if 0 <= index < vertex_count:
                sums[index][0] += nx
                sums[index][1] += ny
                sums[index][2] += nz
                touched[index] = True

    result = []
    for index, value in enumerate(sums):
        if touched[index]:
            result.append(_normalize3(value))
            continue
        if fallback_normals is not None and index < len(fallback_normals):
            result.append(_normalize3(fallback_normals[index]))
        else:
            result.append((0.0, 0.0, 1.0))
    return result


def linear_to_srgb(value):
    """Encode a scene-linear channel using the standard sRGB transfer curve."""

    value = max(0.0, min(1.0, float(value)))
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def _channel_to_byte(value, *, encode_srgb=False):
    value = float(value)
    if encode_srgb:
        value = linear_to_srgb(value)
    value = max(0.0, min(1.0, value))
    # stb_image supplies bytes; converting Blender's float image buffer back to
    # the nearest byte most closely reproduces that input to MakeOBJ.
    return max(0, min(255, int(value * 255.0 + 0.5)))


def _trunc_coord(value, extent):
    # MakeOBJ's VC9 helper uses CVTTSD2SI on supported CPUs: truncate toward 0.
    return int(float(value) * float(max(0, int(extent) - 1)))


def _bresenham_points(x0, y0, x1, y1):
    """Yield integer edge pixels, inclusive of both endpoints."""

    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            break
        e2 = err * 2
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def makeobj_polygon_rgb(
    geo_uvs,
    width,
    height,
    pixels,
    *,
    channels=4,
    blender_bottom_up=True,
    encode_srgb=False,
):
    """Compute the legacy MakeOBJ ``ComputePolyColor`` RGB value.

    ``geo_uvs`` are Battlezone/DirectX-style UV coordinates (V=0 at the top),
    matching the UV values written to GEO. MakeOBJ 1.1.5 scales coordinates by
    ``size - 1``, truncates toward zero, rasterizes an inclusive integer span,
    wraps each sampled coordinate with ``coord & (size - 1)``, then averages the
    raw RGB bytes with integer division.

    The bit-mask wrapping is intentionally reproduced instead of ``%``: that is
    what MakeOBJ does and is exact for the power-of-two texture sizes expected by
    legacy Battlezone assets.
    """

    width, height = int(width), int(height)
    channels = int(channels)
    if width <= 0 or height <= 0 or channels < 3 or len(geo_uvs) < 3:
        return None
    if pixels is None or len(pixels) < width * height * channels:
        return None

    points = [(_trunc_coord(u, width), _trunc_coord(v, height)) for u, v in geo_uvs]

    left = {}
    right = {}
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        for x, y in _bresenham_points(x0, y0, x1, y1):
            if y not in left or x < left[y]:
                left[y] = x
            if y not in right or x > right[y]:
                right[y] = x

    if not left:
        return None

    mask_x = width - 1
    mask_y = height - 1
    red = green = blue = count = 0

    for y in range(min(left), max(left) + 1):
        if y not in left or y not in right:
            continue
        x0, x1 = left[y], right[y]
        if x1 < x0:
            x0, x1 = x1, x0
        wrapped_y = y & mask_y
        # Blender's Image.pixels buffer is bottom-up while stb_image (used by
        # MakeOBJ) presents the first row at the top. Flip only at buffer read.
        sample_y = height - 1 - wrapped_y if blender_bottom_up else wrapped_y
        row_base = sample_y * width
        for x in range(x0, x1 + 1):
            sample_x = x & mask_x
            offset = (row_base + sample_x) * channels
            red += _channel_to_byte(pixels[offset], encode_srgb=encode_srgb)
            green += _channel_to_byte(pixels[offset + 1], encode_srgb=encode_srgb)
            blue += _channel_to_byte(pixels[offset + 2], encode_srgb=encode_srgb)
            count += 1

    if count <= 0:
        return None
    return (red // count, green // count, blue // count)
