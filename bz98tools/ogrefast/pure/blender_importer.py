from __future__ import annotations

"""Blender adapter for the unified pure static/rigged Ogre binary reader."""

import os

from .. import ogre_importer as shared
from .kenshi_facade import KenshiObjectSerializer


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
    # Unsupported mesh-animation data, scale-key skeleton animations or linked
    # animation sources raise here before Blender is modified, allowing
    # backend.py to hand the file to the legacy XML importer safely.
    mesh_data = serializer.load_mesh(mesh_file)

    original_selection = list(context.selected_objects)
    try:
        for obj in original_selection:
            if obj.type == "MESH":
                obj.select_set(False)

        import_info_log = []
        skeleton_data = None
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

        if import_animations and skeleton_data is not None and selected_skeleton is not None:
            render = context.scene.render
            if round_frames:
                fps = int(round(skeleton_data.calc_animation_fps()))
                if fps > 0:
                    print("Setting FPS to", fps)
                    render.fps = fps
            shared.create_animation(
                animations=skeleton_data.get_animations(),
                import_info_log=import_info_log,
                armature=selected_skeleton,
                fps=render.fps,
                round_frames=round_frames,
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
