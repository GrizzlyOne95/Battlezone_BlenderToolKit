from . import probe_native_backend


def _fallback(operator, reason, legacy_handler, *args, **kwargs):
    if reason:
        print(f"Fast Ogre backend unavailable, falling back to legacy XML path: {reason}")
    return legacy_handler(*args, **kwargs)


def _map_tangent_format(
    export_tangents, export_binormals, zero_tangents_binormals, tangent_parity
):
    if zero_tangents_binormals:
        return "ZERO"
    if not export_tangents:
        return "TANGENT_0"
    if export_binormals:
        return "ALL" if tangent_parity else "TANGENT_3"
    return "TANGENT_4"


def _selected_mesh_objects(context):
    return [
        obj
        for obj in context.view_layer.objects
        if obj.select_get() and obj.type != "ARMATURE"
    ]


def _pure_export_mode(context, export_poses, export_animation):
    selected = _selected_mesh_objects(context)
    armatures = {
        armature
        for armature in (obj.find_armature() for obj in selected)
        if armature is not None
    }
    if len(armatures) > 1:
        return None, "pure Python export currently requires all selected rigged meshes to share one armature"

    if export_poses:
        for obj in selected:
            shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
            key_blocks = getattr(shape_keys, "key_blocks", None)
            if key_blocks and len(key_blocks) > 1:
                return None, "pure Python shape-key/pose export is not implemented yet"

    # Normal action export is now supported for rigged meshes. The shared
    # exporter invokes the pure AnimationData compatibility layer with
    # use_scale_keyframe=False, matching the add-on's current fast-path setup.
    return ("rigged" if armatures else "static"), None


def _write_materials(
    filepath,
    selected_objects,
    export_materials,
    overwrite_material,
    copy_textures,
):
    if not export_materials:
        return

    from ..ogretools import OgreExport as legacy_export

    material_data = {}
    legacy_export.bCollectMaterialData(material_data, selected_objects)
    legacy_export.xSaveMaterialData(
        filepath if filepath.lower().endswith(".mesh") else filepath + ".mesh",
        material_data,
        overwrite_material,
        copy_textures,
    )


def import_mesh(
    operator,
    context,
    filepath,
    legacy_handler,
    xml_converter=None,
    keep_xml=False,
    import_normals=True,
    normal_mode="custom",
    import_shapekeys=True,
    import_animations=False,
    round_frames=True,
    use_selected_skeleton=False,
    import_materials=True,
):
    legacy_kwargs = dict(
        xml_converter=xml_converter,
        keep_xml=keep_xml,
        import_normals=import_normals,
        normal_mode=normal_mode,
        import_shapekeys=import_shapekeys,
        import_animations=import_animations,
        round_frames=round_frames,
        use_selected_skeleton=use_selected_skeleton,
        import_materials=import_materials,
    )

    native_available, native_reason = probe_native_backend()
    if native_available:
        from . import ogre_importer

        try:
            print("Using native Ogre backend for mesh import.")
            return ogre_importer.load(
                operator,
                context,
                filepath,
                import_normals=import_normals,
                normal_mode=normal_mode,
                import_shapekeys=import_shapekeys,
                import_animations=import_animations,
                round_frames=round_frames,
                use_selected_skeleton=use_selected_skeleton,
                create_materials=import_materials,
            )
        except Exception as exc:
            native_reason = f"native import failed: {exc}"

    pure_reason = None
    try:
        from .pure import blender_importer

        print(
            "Using pure Python Ogre backend for mesh import"
            + (f"; native backend unavailable: {native_reason}" if native_reason else ".")
        )
        return blender_importer.load(
            operator,
            context,
            filepath,
            import_normals=import_normals,
            normal_mode=normal_mode,
            import_shapekeys=import_shapekeys,
            import_animations=import_animations,
            round_frames=round_frames,
            use_selected_skeleton=use_selected_skeleton,
            create_materials=import_materials,
        )
    except Exception as exc:
        pure_reason = f"pure Python import unavailable: {exc}"

    reasons = "; ".join(
        reason for reason in (native_reason, pure_reason) if reason
    ) or "no fast backend is available"
    return _fallback(
        operator,
        reasons,
        legacy_handler,
        operator,
        context,
        filepath,
        **legacy_kwargs,
    )


def export_mesh(
    operator,
    context,
    filepath,
    legacy_handler,
    xml_converter=None,
    keep_xml=False,
    export_tangents=True,
    export_binormals=True,
    zero_tangents_binormals=False,
    export_colour=True,
    tangent_parity=True,
    apply_transform=True,
    apply_modifiers=True,
    export_materials=True,
    overwrite_material=False,
    copy_textures=False,
    export_skeleton=True,
    export_poses=True,
    export_animation=False,
    renormalize_weights=True,
    batch_export=False,
):
    legacy_kwargs = dict(
        xml_converter=xml_converter,
        keep_xml=keep_xml,
        export_tangents=export_tangents,
        export_binormals=export_binormals,
        zero_tangents_binormals=zero_tangents_binormals,
        export_colour=export_colour,
        tangent_parity=tangent_parity,
        apply_transform=apply_transform,
        apply_modifiers=apply_modifiers,
        export_materials=export_materials,
        overwrite_material=overwrite_material,
        copy_textures=copy_textures,
        export_skeleton=export_skeleton,
        export_poses=export_poses,
        export_animation=export_animation,
        renormalize_weights=renormalize_weights,
        batch_export=batch_export,
    )

    if batch_export:
        return _fallback(
            operator,
            "fast batch mesh export is not wired yet",
            legacy_handler,
            operator,
            context,
            filepath,
            **legacy_kwargs,
        )

    selected_objects = _selected_mesh_objects(context)
    tangent_format = _map_tangent_format(
        export_tangents,
        export_binormals,
        zero_tangents_binormals,
        tangent_parity,
    )

    native_available, native_reason = probe_native_backend()
    if native_available:
        from . import ogre_exporter

        try:
            print("Using native Ogre backend for mesh export.")
            result = ogre_exporter.save(
                operator,
                context,
                filepath,
                tangent_format=tangent_format,
                export_colour=export_colour,
                apply_transform=apply_transform,
                apply_modifiers=apply_modifiers,
                export_skeleton=export_skeleton,
                export_poses=export_poses,
                export_animation=export_animation,
                export_all_bones=False,
                mesh_optimize=True,
                export_version="V_1_10",
                is_visual_keying=False,
                use_scale_keyframe=False,
                num_fake_pose=0,
                renormalize_weights=renormalize_weights,
            )
            if result == {"FINISHED"}:
                _write_materials(
                    filepath,
                    selected_objects,
                    export_materials,
                    overwrite_material,
                    copy_textures,
                )
            return result
        except Exception as exc:
            native_reason = f"native export failed: {exc}"

    pure_mode, pure_reason = _pure_export_mode(
        context, export_poses, export_animation
    )
    if pure_mode:
        try:
            if pure_mode == "rigged":
                from .pure import blender_rigged_exporter as pure_exporter
            else:
                from .pure import blender_exporter as pure_exporter

            print(
                f"Using pure Python Ogre backend for {pure_mode} mesh export"
                + (f"; native backend unavailable: {native_reason}" if native_reason else ".")
            )
            result = pure_exporter.save(
                operator,
                context,
                filepath,
                tangent_format=tangent_format,
                export_colour=export_colour,
                apply_transform=apply_transform,
                apply_modifiers=apply_modifiers,
                **(
                    dict(
                        export_skeleton=export_skeleton,
                        export_poses=export_poses,
                        export_animation=export_animation,
                        renormalize_weights=renormalize_weights,
                    )
                    if pure_mode == "rigged"
                    else dict(mesh_optimize=True)
                ),
            )
            if result == {"FINISHED"}:
                _write_materials(
                    filepath,
                    selected_objects,
                    export_materials,
                    overwrite_material,
                    copy_textures,
                )
            return result
        except Exception as exc:
            pure_reason = f"pure Python export failed: {exc}"

    reasons = "; ".join(
        reason for reason in (native_reason, pure_reason) if reason
    ) or "no fast backend is available"
    return _fallback(
        operator,
        reasons,
        legacy_handler,
        operator,
        context,
        filepath,
        **legacy_kwargs,
    )