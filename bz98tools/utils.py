from typing import Any
import os
import math
import hashlib
import bpy


def get_default_ogre_xml_converter():
    """Try to find OgreXMLConverter.exe in the bundled ogretools folder."""
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    candidate = os.path.join(addon_dir, "ogretools", "OgreXMLConverter.exe")
    if os.path.isfile(candidate):
        return candidate
    # Fallback: empty string; user can pick it manually
    return ""


def get_default_zfs_cache_dir():
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(addon_dir, "_cache", "zfs")


def get_export_preset_dir(export_kind):
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(addon_dir, "_presets", export_kind.lower())


def get_zfs_archive_cache_dir(zfs_path, cache_root=None):
    if not zfs_path:
        return ""
    cache_root = os.path.abspath(cache_root or get_default_zfs_cache_dir())
    archive_name = os.path.splitext(os.path.basename(zfs_path))[0]
    archive_hash = hashlib.sha1(zfs_path.encode("utf-8")).hexdigest()[:8]
    return os.path.join(cache_root, f"{archive_name}_{archive_hash}")


def fix_geo_export_name(name, lod):
    geofilename = list(name)
    if len(geofilename) > 8:
        geofilename = geofilename[0:8]
    if lod in (1, 2, 3):
        geofilename[3] = str(lod)
    else:
        geofilename[3] = "3"
    geofilename[4] = "1"
    return "".join(geofilename)


def sanitize_quick_geo_prefix(value):
    source = (value or "").strip().lower()
    chars = [ch for ch in source if ch.isalnum()]
    if not chars:
        chars = list("veh")
    return ("".join(chars) + "veh")[:3]


def create_box_mesh(name, size):
    half = float(size) * 0.5
    verts = [
        (-half, -half, -half),
        (half, -half, -half),
        (half, half, -half),
        (-half, half, -half),
        (-half, -half, half),
        (half, -half, half),
        (half, half, half),
        (-half, half, half),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def derive_legacy_texture_name(name):
    derived = os.path.splitext((name or "").strip())[0].replace(" ", "_").lower()
    return derived[:8]


def open_path_in_shell(path):
    target_path = os.path.abspath(path)
    if hasattr(bpy.ops, "wm") and hasattr(bpy.ops.wm, "path_open"):
        bpy.ops.wm.path_open(filepath=target_path)
        return
    if hasattr(os, "startfile"):
        os.startfile(target_path)
        return
    raise RuntimeError("No supported path-open handler is available.")


def blender_local_to_engine_matrix(obj):
    """Convert a Blender object's local transform into the legacy engine
    MAT_3D_FILE layout (right/up/front/posit rows, 12 floats).

    Replicates the VDF/GEO export math: rotation remapped through the YZX
    convention, scale baked into basis rows, translation swapped to
    (x, z, y). Shared by VLOC binding and raw-matrix capture.
    """
    euler = mathutils.Euler((0.0, math.radians(45.0), 0.0), "YZX")
    euler[:] = (
        obj.rotation_euler.x,
        obj.rotation_euler.z,
        obj.rotation_euler.y,
    )
    rot_matrix = euler.to_matrix()
    sx, sy, sz = obj.scale
    scale_mat = mathutils.Matrix(
        (
            (sx, 0.0, 0.0),
            (0.0, sy, 0.0),
            (0.0, 0.0, sz),
        )
    )
    thematrix = rot_matrix @ scale_mat
    translation = obj.matrix_local.to_translation()
    return [
        thematrix[0][0],
        thematrix[0][1],
        thematrix[0][2],
        thematrix[1][0],
        thematrix[1][1],
        thematrix[1][2],
        thematrix[2][0],
        thematrix[2][1],
        thematrix[2][2],
        translation.x,
        translation.z,
        translation.y,
    ]
