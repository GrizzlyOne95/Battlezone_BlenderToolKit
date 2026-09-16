from __future__ import annotations

"""Blender adapter for the pure static Ogre binary reader."""

import importlib
import os
import sys

from . import kenshi_compat
from .import_compat import KenshiObjectSerializer


def _load_shared_blender_helpers():
    """Import ogre_importer even when the CPython-specific module is unavailable.

    ogre_importer imports ``kenshi_blender_tool`` at module scope. For the pure
    path we supply the compatibility module long enough to import its Blender
    object-construction helpers. The helpers then operate on our compatible
    MeshData/SubMeshData instances.
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
    # Unsupported rigging/poses/mesh animations raise here, before Blender is
    # modified, allowing backend.py to fall through to the legacy importer.
    mesh_data = serializer.load_mesh(mesh_file)

    shared = _load_shared_blender_helpers()
    original_selection = list(context.selected_objects)
    try:
        for obj in original_selection:
            if obj.type == "MESH":
                obj.select_set(False)

        import_info_log = []
        shared.create_mesh(
            context=context,
            operator=operator,
            import_info_log=import_info_log,
            mesh_data=mesh_data,
            armature=None,
            bone_map={},
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
