# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Optional MakeOBJ-compatible behavior for GEO export.

This module wraps the existing exporter rather than changing its default path. If
both compatibility switches are disabled, the original ``export_geo.geoexport``
function is called exactly as before. When either switch is enabled, the
compatibility exporter mirrors the current GEO writer while substituting the two
algorithms recovered from MakeObj 1.1.5:

* equal-weight adjacent-face vertex normals; and
* texture-rasterized ``ComputePolyColor`` face RGB values.
"""

from __future__ import annotations

import struct

from . import export_geo, geo_classes, makeobj_compat

_ORIGINAL_GEOEXPORT = None
_WARNED_POLY_COLOR = set()


def _settings(context):
    scene = getattr(context, "scene", None)
    return (
        bool(getattr(scene, "bz_makeobj_legacy_normals", False)),
        bool(getattr(scene, "bz_makeobj_compute_poly_color", False)),
    )


def _get_image_node(material):
    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return None
    image_nodes = [
        node
        for node in getattr(node_tree, "nodes", [])
        if getattr(node, "type", "") == "TEX_IMAGE"
        and getattr(node, "image", None) is not None
    ]
    if not image_nodes:
        return None
    return next(
        (node for node in image_nodes if getattr(node, "select", False)),
        image_nodes[0],
    )


def _warn_poly_color_once(obj, material, reason):
    key = (getattr(obj, "name", ""), getattr(material, "name", ""), reason)
    if key in _WARNED_POLY_COLOR:
        return
    _WARNED_POLY_COLOR.add(key)
    print(
        "[BZ GEO Export] MakeOBJ ComputePolyColor fallback for %s / %s: %s; "
        "using material diffuse color."
        % (
            getattr(obj, "name", "<object>"),
            getattr(material, "name", "<material>"),
            reason,
        )
    )


def _image_pixel_data(image, cache):
    key_fn = getattr(image, "as_pointer", None)
    key = key_fn() if callable(key_fn) else id(image)
    if key in cache:
        return cache[key]

    size = getattr(image, "size", None)
    if size is None or len(size) < 2:
        return None
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        return None

    try:
        pixels = tuple(float(v) for v in image.pixels[:])
    except Exception:
        return None

    expected = width * height * 4
    if len(pixels) < expected:
        return None

    colorspace = getattr(getattr(image, "colorspace_settings", None), "name", "")
    encode_srgb = "srgb" in str(colorspace).lower()
    value = (width, height, pixels, encode_srgb)
    cache[key] = value
    return value


def _compute_poly_color(obj, material, face, uv_layer, image_cache):
    node = _get_image_node(material)
    image = getattr(node, "image", None) if node is not None else None
    if image is None:
        _warn_poly_color_once(obj, material, "no image texture node")
        return None

    image_data = _image_pixel_data(image, image_cache)
    if image_data is None:
        _warn_poly_color_once(obj, material, "image pixels are unavailable")
        return None
    width, height, pixels, encode_srgb = image_data

    geo_uvs = []
    for loop_index in range(face.loop_start, face.loop_start + face.loop_total):
        uv = uv_layer[loop_index].uv
        # GEO/DirectX V increases downward. The normal exporter already writes
        # exactly this transform, so feed the same coordinates to MakeOBJ's
        # recovered rasterizer.
        geo_uvs.append((float(uv[0]), 1.0 - float(uv[1])))

    rgb = makeobj_compat.makeobj_polygon_rgb(
        geo_uvs,
        width,
        height,
        pixels,
        channels=4,
        blender_bottom_up=True,
        encode_srgb=encode_srgb,
    )
    if rgb is None:
        _warn_poly_color_once(obj, material, "polygon could not be rasterized")
    return rgb


def _compat_geoexport(context, filepath, obj, *, face_plane_mode="CURRENT"):
    use_legacy_normals, use_poly_color = _settings(context)

    Vertices = []
    Normals = []
    Faces = []

    if obj is None:
        raise ValueError("No object provided for GEO export.")

    view_layer, previous_active = export_geo._prepare_export_object(context, obj)
    mesh = obj.data
    image_cache = {}

    try:
        def _get_face_attr_int(attr_name, face_index, default_value=0):
            attrs = getattr(mesh, "attributes", None)
            if attrs is None:
                return int(default_value)
            attr = attrs.get(attr_name)
            if attr is None or attr.domain != "FACE":
                return int(default_value)
            try:
                return int(attr.data[face_index].value)
            except Exception:
                return int(default_value)

        def _get_face_attr_float(attr_name, face_index, default_value=0.0):
            attrs = getattr(mesh, "attributes", None)
            if attrs is None:
                return float(default_value)
            attr = attrs.get(attr_name)
            if attr is None or attr.domain != "FACE":
                return float(default_value)
            try:
                return float(attr.data[face_index].value)
            except Exception:
                return float(default_value)

        def _has_face_plane_attrs(face_index):
            attrs = getattr(mesh, "attributes", None)
            if attrs is None:
                return False
            for attr_name in (
                "bz_face_plane_x",
                "bz_face_plane_y",
                "bz_face_plane_z",
                "bz_face_plane_d",
            ):
                attr = attrs.get(attr_name)
                if attr is None or attr.domain != "FACE":
                    return False
                if face_index >= len(attr.data):
                    return False
            return True

        # Keep the original exporter's material-index failsafe.
        mat_count = len(mesh.materials)
        if mat_count > 0:
            for poly in mesh.polygons:
                idx = getattr(poly, "material_index", 0)
                if idx is None or idx < 0 or idx >= mat_count:
                    print(
                        "[BZ GEO Export] Warning: Invalid material index %s on polygon %s; resetting to 0."
                        % (idx, poly.index)
                    )
                    poly.material_index = 0

        if use_legacy_normals:
            normal_values = makeobj_compat.makeobj_vertex_normals(
                len(mesh.vertices),
                [tuple(poly.vertices) for poly in mesh.polygons],
                [tuple(poly.normal) for poly in mesh.polygons],
                fallback_normals=[tuple(vertex.normal) for vertex in mesh.vertices],
            )
        else:
            normal_values = [tuple(vertex.normal) for vertex in mesh.vertices]

        for vertex, normal in zip(mesh.vertices, normal_values):
            Vertices.append(
                geo_classes.GEOVertex([vertex.co.x, vertex.co.z, vertex.co.y])
            )
            Normals.append(
                geo_classes.GEONormal([-normal[0], -normal[2], -normal[1]])
            )

        face_plane_mode = (face_plane_mode or "CURRENT").upper()

        # The stock exporter creates an active UV map on demand. Do it once
        # before the face loop so ComputePolyColor sees the same UVs that will
        # actually be serialized.
        if mesh.uv_layers.active is None:
            mesh.uv_layers.new()
        uv_layer = mesh.uv_layers.active.data

        for face in mesh.polygons:
            facematerial = ""
            facecolor = [1, 1, 1]
            computed_rgb = None

            if (
                obj.data.materials is not None
                and len(obj.data.materials) > 0
                and face.material_index is not None
            ):
                mat = obj.data.materials[face.material_index]
                tex_name = ""
                if hasattr(mat, "MaterialPropertyGroup"):
                    raw = mat.MaterialPropertyGroup.MapTexture
                    tex_name = (raw or "").strip()

                    if not tex_name:
                        tex_name = export_geo._get_image_derived_texture_name(mat)
                    if not tex_name:
                        tex_name = export_geo._derive_legacy_texture_name(mat.name)

                    if tex_name:
                        mat.MaterialPropertyGroup.MapTexture = tex_name

                facematerial = tex_name
                facecolor = getattr(mat, "diffuse_color", facecolor)
                if use_poly_color:
                    computed_rgb = _compute_poly_color(
                        obj, mat, face, uv_layer, image_cache
                    )

            if computed_rgb is not None:
                r, g, b = computed_rgb
            else:
                r = int(facecolor[0] * 255)
                g = int(facecolor[1] * 255)
                b = int(facecolor[2] * 255)

            geo_pg = getattr(obj, "GEOPropertyGroup", None)
            default_unknown = (
                getattr(geo_pg, "GEOFaceUnknownDefault", 0) if geo_pg else 0
            )
            default_parent = (
                getattr(geo_pg, "GEOFaceParentDefault", 0) if geo_pg else 0
            )
            default_node = (
                getattr(geo_pg, "GEOFaceNodeDefault", 0) if geo_pg else 0
            )
            default_shade = (
                getattr(geo_pg, "GEOFaceShadeTypeDefault", 4) if geo_pg else 4
            )
            default_texture = (
                getattr(geo_pg, "GEOFaceTextureTypeDefault", 0) if geo_pg else 0
            )
            default_xluscent = (
                getattr(geo_pg, "GEOFaceXluscentTypeDefault", 0) if geo_pg else 0
            )

            face_unknown = _get_face_attr_int(
                "bz_face_unknown_raw", face.index, default_unknown
            )
            face_parent = _get_face_attr_int(
                "bz_face_parent", face.index, default_parent
            )
            face_node = _get_face_attr_int(
                "bz_face_node", face.index, default_node
            )
            shade_type = (
                _get_face_attr_int(
                    "bz_face_shade_type", face.index, default_shade
                )
                & 0xFF
            )
            texture_type = (
                _get_face_attr_int(
                    "bz_face_texture_type", face.index, default_texture
                )
                & 0xFF
            )
            xluscent_type = (
                _get_face_attr_int(
                    "bz_face_xluscent_type", face.index, default_xluscent
                )
                & 0xFF
            )
            string_header_bytes = bytes(
                [shade_type, texture_type, xluscent_type]
            )

            if face_plane_mode == "PRESERVE" and _has_face_plane_attrs(face.index):
                plane_x = _get_face_attr_float(
                    "bz_face_plane_x", face.index, face.center.x
                )
                plane_y = _get_face_attr_float(
                    "bz_face_plane_y", face.index, face.center.y
                )
                plane_z = _get_face_attr_float(
                    "bz_face_plane_z", face.index, face.center.z
                )
                plane_d = _get_face_attr_float(
                    "bz_face_plane_d", face.index, 1.0
                )
            elif face_plane_mode in {"RECOMPUTE", "DX_FIX"}:
                plane_x, plane_y, plane_z, plane_d = export_geo._compute_face_plane(
                    mesh, face
                )
            else:
                plane_x = face.center.x
                plane_y = face.center.y
                plane_z = face.center.z
                plane_d = 1.0

            NewFace = geo_classes.GEOFace(
                [
                    face.index,
                    len(face.vertices),
                    r,
                    g,
                    b,
                    plane_x,
                    plane_y,
                    plane_z,
                    plane_d,
                    face_unknown,
                    string_header_bytes,
                    facematerial,
                    face_parent,
                    face_node,
                    "",
                ]
            )

            curvert = 0
            for loop_index in range(
                face.loop_start, face.loop_start + face.loop_total
            ):
                vert_index = face.vertices[curvert]
                u = uv_layer[loop_index].uv[0]
                v = 1.0 - uv_layer[loop_index].uv[1]

                NewFace.VertList.append(
                    geo_classes.PolygonVert([vert_index, vert_index, u, v])
                )
                curvert += 1

            Faces.append(NewFace)

        with open(filepath, mode="wb") as file:
            geo_pg = getattr(obj, "GEOPropertyGroup", None)
            header_unknown = (
                int(getattr(geo_pg, "GEOHeaderUnknown", 69)) if geo_pg else 69
            )
            header_unknown2 = (
                int(getattr(geo_pg, "GEOHeaderUnknown2", 0)) if geo_pg else 0
            )
            NewHeader = geo_classes.GEOHeader(
                [
                    "OEG.",
                    header_unknown,
                    obj.name,
                    len(Vertices),
                    len(Faces),
                    header_unknown2,
                ]
            )
            buffer = bytearray(36)
            struct.pack_into("=4si16siii", buffer, 0, *NewHeader.Read())
            file.write(buffer)

            for vertex in Vertices:
                buffer = bytearray(12)
                struct.pack_into("=fff", buffer, 0, *vertex.Read())
                file.write(buffer)

            for normal in Normals:
                buffer = bytearray(12)
                struct.pack_into("=fff", buffer, 0, *normal.Read())
                file.write(buffer)

            for face in Faces:
                buffer = bytearray(55)
                struct.pack_into(
                    "=iiBBBffffi3s13sii", buffer, 0, *face.Read()
                )
                file.write(buffer)
                for vert in face.VertList:
                    buffer = bytearray(16)
                    struct.pack_into("=iiff", buffer, 0, *vert.Read())
                    file.write(buffer)
    finally:
        if (
            view_layer is not None
            and previous_active is not None
            and previous_active is not obj
        ):
            view_layer.objects.active = previous_active

    return {"FINISHED"}


def _wrapped_geoexport(context, filepath, obj, *, face_plane_mode="CURRENT"):
    use_legacy_normals, use_poly_color = _settings(context)
    if not use_legacy_normals and not use_poly_color:
        return _ORIGINAL_GEOEXPORT(
            context, filepath, obj, face_plane_mode=face_plane_mode
        )
    return _compat_geoexport(
        context, filepath, obj, face_plane_mode=face_plane_mode
    )


def install():
    global _ORIGINAL_GEOEXPORT
    if _ORIGINAL_GEOEXPORT is not None:
        return
    _ORIGINAL_GEOEXPORT = export_geo.geoexport
    export_geo.geoexport = _wrapped_geoexport


def uninstall():
    global _ORIGINAL_GEOEXPORT
    if _ORIGINAL_GEOEXPORT is None:
        return
    if export_geo.geoexport is _wrapped_geoexport:
        export_geo.geoexport = _ORIGINAL_GEOEXPORT
    _ORIGINAL_GEOEXPORT = None
