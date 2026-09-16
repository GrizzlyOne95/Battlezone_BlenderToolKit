from __future__ import annotations

import os
import traceback
from typing import List

import bmesh
import bpy
import numpy as np

from .kenshi_compat import KenshiObjectSerializer, MeshVersion
from .pose_compat import SubMeshData


class UnsupportedPureExport(RuntimeError):
    pass


def can_export_static(context, *, export_skeleton=False, export_poses=False, export_animation=False):
    if export_skeleton or export_poses or export_animation:
        return False, "skeleton, pose, and animation export are not implemented in the pure backend yet"

    selected = [
        obj
        for obj in context.view_layer.objects
        if obj.select_get() and obj.type != "ARMATURE"
    ]
    if any(obj.find_armature() is not None for obj in selected):
        return False, "rigged mesh export is not implemented in the pure backend yet"
    return True, None


def save(
    operator,
    context,
    filepath,
    *,
    tangent_format="TANGENT_4",
    export_colour=False,
    apply_transform=True,
    apply_modifiers=True,
    mesh_optimize=True,
):
    if not filepath.lower().endswith(".mesh"):
        filepath = f"{filepath}.mesh"

    selected_objects: List[bpy.types.Object] = [
        obj
        for obj in context.view_layer.objects
        if obj.select_get() and obj.type != "ARMATURE"
    ]
    if not selected_objects:
        operator.report({"WARNING"}, "No objects selected for export")
        return {"CANCELLED"}

    supported, reason = can_export_static(context)
    if not supported:
        raise UnsupportedPureExport(reason)

    try:
        if context.active_object:
            bpy.ops.object.mode_set(mode="OBJECT")
        if apply_transform:
            bpy.ops.object.transform_apply(rotation=True, scale=True)

        serializer = KenshiObjectSerializer()
        _, filename = os.path.split(filepath)
        mesh_data = serializer.create_mesh(filename)
        submeshes = []

        for submesh_index, obj in enumerate(selected_objects):
            submeshes.append(
                _collect_submesh(
                    context,
                    obj,
                    submesh_index,
                    tangent_format=tangent_format,
                    export_colour=export_colour,
                    apply_modifiers=apply_modifiers,
                    optimize=mesh_optimize,
                )
            )

        mesh_data.set_submeshes(submeshes)
        serializer.save_mesh(mesh_data, filepath, MeshVersion.V_1_10)
        operator.report({"INFO"}, "Export successful (pure Python Ogre backend)")
        return {"FINISHED"}
    except UnsupportedPureExport:
        raise
    except Exception:
        err_mes = traceback.format_exc()
        print(err_mes)
        operator.report({"ERROR"}, f"Pure Ogre export error!\n{err_mes}")
        raise


def _ordered_uv_layers(mesh):
    """Return active UV first, then preserve Blender collection order."""

    active = mesh.uv_layers.active
    if active is None:
        return []
    return [active] + [layer for layer in mesh.uv_layers if layer.name != active.name]


def _collect_uv_sets(mesh, loop_count):
    values = []
    for layer in _ordered_uv_layers(mesh):
        array = np.empty(loop_count * 2, dtype=np.float32)
        mesh.attributes[layer.name].data.foreach_get("vector", array)
        values.append(array.reshape(-1, 2))
    if not values:
        return np.empty(2, dtype=np.float32)
    if len(values) == 1:
        return values[0]
    return np.stack(values, axis=0)


def _collect_submesh(
    context,
    obj,
    submesh_index,
    *,
    tangent_format,
    export_colour,
    apply_modifiers,
    optimize,
):
    submesh = SubMeshData()
    submesh.index = submesh_index
    submesh.submesh_name = obj.name
    submesh.material = next(
        (material.name for material in obj.data.materials if material), obj.name
    )

    temp_object = (
        obj.evaluated_get(context.evaluated_depsgraph_get())
        if apply_modifiers
        else obj
    )
    mesh = temp_object.to_mesh()
    try:
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bmesh.ops.triangulate(bm, faces=bm.faces)
            bm.to_mesh(mesh)
        finally:
            bm.free()

        uv_name = mesh.uv_layers.active.name if mesh.uv_layers.active else None
        effective_tangent_format = tangent_format if uv_name else "TANGENT_0"
        if effective_tangent_format != "TANGENT_0":
            mesh.calc_tangents(uvmap=uv_name)

        loop_count = len(mesh.loops)
        vertex_count = len(mesh.vertices)

        nd_vert_indices = np.empty(loop_count, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", nd_vert_indices)
        nd_loop_indices = np.empty(loop_count, dtype=np.int32)
        mesh.loops.foreach_get("index", nd_loop_indices)

        nd_positions = np.empty(vertex_count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", nd_positions)
        nd_positions = nd_positions.reshape(-1, 3)

        nd_normals = np.empty(loop_count * 3, dtype=np.float32)
        mesh.loops.foreach_get("normal", nd_normals)
        nd_normals = nd_normals.reshape(-1, 3)

        nd_texcoords = _collect_uv_sets(mesh, loop_count)

        tangent_dimensions = (
            4
            if effective_tangent_format in {"TANGENT_4", "ALL", "FLIPPED"}
            else 3
        )
        if effective_tangent_format != "TANGENT_0":
            nd_tangents = np.empty(loop_count * 3, dtype=np.float32)
            mesh.loops.foreach_get("tangent", nd_tangents)
            nd_tangents = nd_tangents.reshape(-1, 3)

            nd_bitangent_signs = np.empty(loop_count, dtype=np.float32)
            mesh.loops.foreach_get("bitangent_sign", nd_bitangent_signs)

            nd_bitangents = np.empty(loop_count * 3, dtype=np.float32)
            mesh.loops.foreach_get("bitangent", nd_bitangents)
            nd_bitangents = nd_bitangents.reshape(-1, 3)

            if effective_tangent_format == "ALL":
                nd_bitangents = nd_bitangents * nd_bitangent_signs.reshape(-1, 1)
            elif effective_tangent_format == "TANGENT_4":
                nd_bitangents = np.empty(3, dtype=np.float32)
            elif effective_tangent_format == "FLIPPED":
                nd_bitangents = -nd_bitangents * nd_bitangent_signs.reshape(-1, 1)
                nd_bitangent_signs = -nd_bitangent_signs
            elif effective_tangent_format == "ZERO":
                nd_tangents = np.zeros((loop_count, 3), dtype=np.float32)
                nd_bitangents = np.zeros((loop_count, 3), dtype=np.float32)
                nd_bitangent_signs = np.zeros(loop_count, dtype=np.float32)
        else:
            nd_tangents = np.empty(3, dtype=np.float32)
            nd_bitangent_signs = np.empty(1, dtype=np.float32)
            nd_bitangents = np.empty(3, dtype=np.float32)

        nd_colors = np.empty(4, dtype=np.float32)
        nd_alphas = np.empty(4, dtype=np.float32)
        if export_colour and len(mesh.color_attributes) > 0:
            for name, attribute in mesh.color_attributes.items():
                if (
                    not name.lower().startswith("alpha")
                    and attribute.domain == "CORNER"
                    and attribute.data_type == "BYTE_COLOR"
                ):
                    nd_colors = np.empty(loop_count * 4, dtype=np.float32)
                    attribute.data.foreach_get("color_srgb", nd_colors)
                    nd_colors = nd_colors.reshape(-1, 4)
                    break
            for name, attribute in mesh.color_attributes.items():
                if (
                    name.lower().startswith("alpha")
                    and attribute.domain == "CORNER"
                    and attribute.data_type == "BYTE_COLOR"
                ):
                    nd_alphas = np.empty(loop_count * 4, dtype=np.float32)
                    attribute.data.foreach_get("color_srgb", nd_alphas)
                    nd_alphas = nd_alphas.reshape(-1, 4)
                    break

        out_source_indices = submesh.set_vertex(
            nd_vert_indices=nd_vert_indices,
            nd_loop_indices=nd_loop_indices,
            nd_positions=nd_positions,
            nd_normals=nd_normals,
            nd_tangents=nd_tangents,
            nd_bitangent_signs=nd_bitangent_signs,
            nd_bitangents=nd_bitangents,
            nd_texcoords=nd_texcoords,
            nd_colors=nd_colors,
            nd_alphas=nd_alphas,
            tangent_dimensions=tangent_dimensions,
            optimize=optimize,
        )
        submesh.set_bone_assignments([], out_source_indices)
        return submesh
    finally:
        temp_object.to_mesh_clear()
