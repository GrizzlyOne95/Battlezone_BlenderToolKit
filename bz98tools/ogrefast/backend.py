import os
import shutil
import subprocess

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


def _mesh_output_path(filepath):
    path = os.fspath(filepath)
    return path if path.lower().endswith(".mesh") else f"{path}.mesh"


def _resolve_mesh_upgrader(xml_converter=None):
    """Locate the BZR-compatible OgreMeshUpgrader next to the configured tools.

    Blender's XML converter setting normally points at the bundled ogretools
    directory, so prefer a sibling upgrader before falling back to the copy
    shipped with the add-on itself.
    """

    candidates = []
    if xml_converter:
        converter_path = os.path.abspath(os.fspath(xml_converter))
        converter_dir = (
            converter_path if os.path.isdir(converter_path) else os.path.dirname(converter_path)
        )
        candidates.extend(
            (
                os.path.join(converter_dir, "OgreMeshUpgrader.exe"),
                os.path.join(converter_dir, "OgreMeshUpgrader"),
            )
        )

    bundled_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ogretools"
    )
    candidates.extend(
        (
            os.path.join(bundled_dir, "OgreMeshUpgrader.exe"),
            os.path.join(bundled_dir, "OgreMeshUpgrader"),
        )
    )

    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.isfile(candidate):
            return candidate
    return None


def _upgrade_mesh_for_bzr(operator, filepath, xml_converter=None):
    """Run OgreMeshUpgrader in-place so BZR receives its expected VB layout.

    Redux's Ogre 1.10 renderer is unusually sensitive to the post-upgrader
    vertex-buffer organization used by stock assets. A directly serialized
    mesh can be structurally valid yet render with broken textures/UVs until
    OgreMeshUpgrader reorganizes the streams. Keep that target-specific step
    outside the pure serializer and apply it to every successful fast export.
    """

    mesh_path = os.path.abspath(_mesh_output_path(filepath))
    if not os.path.isfile(mesh_path):
        operator.report({"ERROR"}, f"Fast Ogre export did not produce {mesh_path}")
        return False

    upgrader = _resolve_mesh_upgrader(xml_converter)
    if upgrader is None:
        operator.report(
            {"WARNING"},
            "OgreMeshUpgrader was not found; run the exported .mesh through the BZR Ogre tools before testing textures/UVs.",
        )
        return True

    command = [upgrader, mesh_path]
    if os.name != "nt" and upgrader.lower().endswith(".exe"):
        wine = shutil.which("wine")
        if wine is None:
            operator.report(
                {"WARNING"},
                "OgreMeshUpgrader.exe is available but Wine is not; run the exported .mesh through OgreMeshUpgrader before BZR testing.",
            )
            return True
        command.insert(0, wine)

    try:
        result = subprocess.run(
            command,
            cwd=os.path.dirname(mesh_path) or None,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        operator.report({"ERROR"}, f"OgreMeshUpgrader failed to start: {exc}")
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            detail = detail.splitlines()[-1]
            operator.report(
                {"ERROR"},
                f"OgreMeshUpgrader failed with exit code {result.returncode}: {detail}",
            )
        else:
            operator.report(
                {"ERROR"},
                f"OgreMeshUpgrader failed with exit code {result.returncode}",
            )
        return False

    if not os.path.isfile(mesh_path):
        operator.report({"ERROR"}, "OgreMeshUpgrader removed the exported mesh unexpectedly")
        return False

    print(f"OgreMeshUpgrader post-process complete: {mesh_path}")
    return True


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

    # The shared ogre_exporter collector is required for rigging, actions and
    # shape-key pose collection. Ordinary static geometry can keep using the
    # smaller direct Blender adapter.
    if armatures or export_poses or export_animation:
        return "collector", None
    return "static", None


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


def export_skeleton(
    operator,
    context,
    filepath,
    *,
    apply_transform=True,
    export_animation=False,
    export_all_bones=False,
    export_version="V_1_10",
    is_visual_keying=False,
    use_scale_keyframe=False,
    legacy_handler=None,
):
    """Export a standalone Ogre skeleton through native or pure fast backends.

    The native CP311 extension remains the preferred reference implementation
    when it can load. Blender/Python combinations that cannot import it use the
    pure serializer and the same Blender collection code.
    """

    output_path = (
        filepath if str(filepath).lower().endswith(".skeleton") else f"{filepath}.skeleton"
    )
    common_kwargs = dict(
        apply_transform=apply_transform,
        export_animation=export_animation,
        export_all_bones=export_all_bones,
        export_version=export_version,
        is_visual_keying=is_visual_keying,
        use_scale_keyframe=use_scale_keyframe,
    )

    native_available, native_reason = probe_native_backend()
    if native_available:
        from . import ogre_exporter

        try:
            print("Using native Ogre backend for skeleton export.")
            result = ogre_exporter.save_skeleton(
                operator=operator,
                context=context,
                filepath=filepath,
                **common_kwargs,
            )
            if result == {"FINISHED"} and os.path.isfile(output_path):
                return result
            native_reason = "native skeleton export did not produce an output file"
        except Exception as exc:
            native_reason = f"native skeleton export failed: {exc}"

    pure_reason = None
    try:
        from .pure import blender_skeleton_exporter

        print(
            "Using pure Python Ogre backend for skeleton export"
            + (f"; native backend unavailable: {native_reason}" if native_reason else ".")
        )
        result = blender_skeleton_exporter.save(
            operator=operator,
            context=context,
            filepath=filepath,
            **common_kwargs,
        )
        if result == {"FINISHED"} and os.path.isfile(output_path):
            return result
        pure_reason = "pure Python skeleton export did not produce an output file"
    except Exception as exc:
        pure_reason = f"pure Python skeleton export failed: {exc}"

    reasons = "; ".join(
        reason for reason in (native_reason, pure_reason) if reason
    ) or "no skeleton fast backend is available"
    if legacy_handler is not None:
        return _fallback(
            operator,
            reasons,
            legacy_handler,
            operator,
            context,
            filepath,
            **common_kwargs,
        )

    print(f"Ogre skeleton export unavailable: {reasons}")
    operator.report({"ERROR"}, f"Ogre skeleton export failed: {reasons}")
    return {"CANCELLED"}


def _export_batch_selected(
    operator,
    context,
    filepath,
    legacy_handler,
    legacy_kwargs,
):
    from .batch import export_selected_individually

    if not _selected_mesh_objects(context):
        operator.report({"WARNING"}, "No objects selected for export")
        return {"CANCELLED"}

    single_kwargs = dict(legacy_kwargs)
    single_kwargs["batch_export"] = False

    def export_one(output_path, _obj):
        return export_mesh(
            operator,
            context,
            output_path,
            legacy_handler,
            **single_kwargs,
        )

    try:
        results = export_selected_individually(context, filepath, export_one)
    except Exception as exc:
        operator.report({"ERROR"}, f"Batch Ogre export failed: {exc}")
        return {"CANCELLED"}

    failed = [
        (obj.name, output_path, result)
        for obj, output_path, result in results
        if result != {"FINISHED"}
    ]
    if failed:
        detail = ", ".join(name for name, _, _ in failed)
        operator.report({"ERROR"}, f"Batch Ogre export failed for: {detail}")
        return {"CANCELLED"}

    operator.report({"INFO"}, f"Batch export successful ({len(results)} meshes)")
    return {"FINISHED"}


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
        return _export_batch_selected(
            operator,
            context,
            filepath,
            legacy_handler,
            legacy_kwargs,
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
                if not _upgrade_mesh_for_bzr(operator, filepath, xml_converter):
                    return {"CANCELLED"}
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
            if pure_mode == "collector":
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
                    if pure_mode == "collector"
                    else dict(mesh_optimize=True)
                ),
            )
            if result == {"FINISHED"}:
                if not _upgrade_mesh_for_bzr(operator, filepath, xml_converter):
                    return {"CANCELLED"}
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
