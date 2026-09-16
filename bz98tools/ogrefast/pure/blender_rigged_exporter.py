from __future__ import annotations

"""Pure Blender mesh/rig/pose collector with full Ogre UV-set preservation.

Bones and animation channels still use the established historical collector so
native and pure paths share their coordinate semantics.  Mesh collection lives
here because the old compiled API only accepted one UV array, while the pure
serializer can preserve all Ogre texture-coordinate sets.
"""

import os
import traceback
from typing import List

import bmesh
import bpy
import numpy as np

from .. import ogre_exporter
from .blender_exporter import _collect_uv_sets
from .kenshi_facade import (
    KenshiObjectSerializer,
    MeshVersion,
    SkeletonVersion,
    SubMeshData,
)


def save(
    operator,
    context,
    filepath,
    *,
    tangent_format="TANGENT_4",
    export_colour=False,
    apply_transform=True,
    apply_modifiers=True,
    export_skeleton=True,
    export_poses=False,
    export_animation=False,
    renormalize_weights=True,
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

    try:
        export_info_log = []
        if context.active_object:
            bpy.ops.object.mode_set(mode="OBJECT")
        if apply_transform:
            bpy.ops.object.transform_apply(rotation=True, scale=True)

        serializer = KenshiObjectSerializer(logfile=ogre_exporter._get_log_file())
        armature = selected_objects[0].find_armature()

        folder, filename = os.path.split(filepath)
        mesh_data = serializer.create_mesh(filename)
        skeleton_filename = f"{os.path.splitext(filename)[0]}.skeleton"
        skeleton_data = None
        if export_skeleton and armature:
            skeleton_data = serializer.create_skeleton(skeleton_filename)
        else:
            export_skeleton = False

        ogre_exporter.collect_bones(
            export_info_log=export_info_log,
            mesh_data=mesh_data,
            skeleton_data=skeleton_data,
            armature=armature,
            export_all_bones=False,
            export_skeleton=export_skeleton,
        )

        _collect_mesh(
            operator=operator,
            context=context,
            export_info_log=export_info_log,
            mesh_data=mesh_data,
            selected_objects=selected_objects,
            apply_modifiers=apply_modifiers,
            export_colour=export_colour,
            tangent_format=tangent_format,
            export_poses=export_poses,
            optimize=True,
            renormalize_weights=renormalize_weights,
        )

        if skeleton_data:
            if export_animation:
                ogre_exporter.collect_animations(
                    context=context,
                    export_info_log=export_info_log,
                    skeleton_data=skeleton_data,
                    armature=armature,
                    use_scale_keyframe=False,
                )
            mesh_data.set_linked_skeleton_name(skeleton_filename)

        serializer.save_mesh(mesh_data, filepath, MeshVersion.V_1_10)
        if skeleton_data:
            serializer.save_skeleton(
                skeleton_data,
                os.path.join(folder, skeleton_filename),
                SkeletonVersion.V_Latest,
            )

        print("\n".join(export_info_log))
        operator.report({"INFO"}, "Export successful (pure Python Ogre backend)")
        return {"FINISHED"}
    except Exception:
        err_mes = traceback.format_exc()
        print(err_mes)
        operator.report({"ERROR"}, f"Pure rigged Ogre export error!\n{err_mes}")
        raise


def _collect_mesh(
    *,
    operator,
    context,
    export_info_log,
    mesh_data,
    selected_objects,
    apply_modifiers,
    export_colour,
    tangent_format,
    export_poses,
    optimize,
    renormalize_weights,
):
    submeshes = []
    for submesh_index, obj in enumerate(selected_objects):
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

            active_uv = mesh.uv_layers.active
            effective_tangent_format = tangent_format if active_uv else "TANGENT_0"
            if effective_tangent_format != "TANGENT_0":
                mesh.calc_tangents(uvmap=active_uv.name)

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

            # Unlike the historical CP311 API, the pure serializer accepts a
            # (uv_set, loop, 2) tensor and includes every set in vertex splits.
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

            if export_poses and mesh.shape_keys and mesh.shape_keys.key_blocks:
                for shape_key in mesh.shape_keys.key_blocks:
                    nd_shape = np.empty(vertex_count * 3, dtype=np.float32)
                    shape_key.data.foreach_get("co", nd_shape)
                    nd_relative = np.empty(vertex_count * 3, dtype=np.float32)
                    shape_key.relative_key.data.foreach_get("co", nd_relative)
                    nd_shape = nd_shape - nd_relative
                    if nd_shape.sum() == 0:
                        continue
                    submesh.append_shapekey(
                        shape_key.name,
                        nd_shape.reshape(vertex_count, 3),
                        out_source_indices,
                    )

            bone_assignments = ogre_exporter._collect_vertex_bone_assignments(
                operator,
                mesh_data,
                obj,
                mesh,
                renormalize_weights,
            )
            submesh.set_bone_assignments(bone_assignments, out_source_indices)
            export_info_log.append(f"Export mesh {obj.name}")
            submeshes.append(submesh)
        finally:
            temp_object.to_mesh_clear()

    mesh_data.set_submeshes(submeshes)


__all__ = ["save"]
