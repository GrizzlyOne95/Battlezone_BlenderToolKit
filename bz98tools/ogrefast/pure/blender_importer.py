from __future__ import annotations

"""Blender adapter for the pure static/rigged Ogre binary reader."""

import importlib
import os
import sys

from . import kenshi_compat
from .rigged_import_compat import KenshiObjectSerializer


def _load_shared_blender_helpers():
    """Import ogre_importer even when the CPython-specific module is unavailable.

    ogre_importer imports ``kenshi_blender_tool`` at module scope. For the pure
    path we supply the compatibility module long enough to import its Blender
    object-construction helpers. The helpers then operate on our compatible
    MeshData/SubMeshData/SkeletonData instances.
    """

    module_name = "kenshi_blender_tool"
    previous = sys.modules.get(module_name)
    inserted = previous is None
    if inserted:
        sys.modules[module_name] = kenshi_compat
    try:
        return importlib.import_module("bz98tools.ogrefast.ogre_importer")
    finally:
        if inserted:
            sys.modules.pop(module_name, None)


def load(
    operator,
    context,
    filepath,
    *,
    import_normals=True,
    normal_mode="custom",
    import_shapekeys=True,
    import_animations=False,
    round_frames=False,
    use_selected_skeleton=False,
    create_materials=True,
):
    if not os.path.isfile(filepath):
        raise FileNotFoundError(filepath)
    if not filepath.lower().endswith(".mesh"):
        raise ValueError("pure Ogre importer only accepts .mesh files")

    folder, mesh_file = os.path.split(filepath)
    serializer = KenshiObjectSerializer()
    serializer.add_resource_location(folder)
    # Unsupported poses/mesh animations or animated linked skeletons raise
    # here, before Blender is modified, allowing backend.py to use XML.
    mesh_data = serializer.load_mesh(mesh_file)

    shared = _load_shared_blender_helpers()
    original_selection = list(context.selected_objects)
    try:
        for obj in original_selection:
            if obj.type == "MESH":
                obj.select_set(False)

        import_info_log = []
        selected_skeleton = (
            context.active_object
            if use_selected_skeleton
            and context.active_object
            and context.active_object.type == "ARMATURE"
            else None
        )
        bone_map = {}

        if selected_skeleton is not None:
            for bone in selected_skeleton.data.bones:
                if "OGREID" in bone:
                    bone_map[int(bone["OGREID"])] = bone.name
            if not bone_map:
                raise RuntimeError("selected armature has no OGRE bone IDs")
        else:
            skeleton_filename = mesh_data.get_linked_skeleton_name()
            if skeleton_filename:
                skeleton_data = mesh_data.get_linked_skeleton()
                if skeleton_data is None:
                    raise RuntimeError(
                        f"failed to load linked skeleton {skeleton_filename!r}"
                    )
                selected_skeleton, bone_map = shared.create_skeleton(
                    context=context,
                    import_info_log=import_info_log,
                    skeleton_data=skeleton_data,
                    skeleton_name=os.path.splitext(skeleton_filename)[0],
                )

        shared.create_mesh(
            context=context,
            operator=operator,
            import_info_log=import_info_log,
            mesh_data=mesh_data,
            armature=selected_skeleton,
            bone_map=bone_map,
            mesh_name=os.path.splitext(mesh_file)[0],
            import_normals=import_normals,
            normal_mode=normal_mode,
            import_shapekeys=import_shapekeys,
            create_materials=create_materials,
        )
        if import_info_log:
            print("\n".join(import_info_log))
        operator.report({"INFO"}, "Import successful (pure Python Ogre backend)")
        return {"FINISHED"}
    finally:
        for obj in original_selection:
            if obj.type == "MESH":
                obj.select_set(True)


__all__ = ["load"]
