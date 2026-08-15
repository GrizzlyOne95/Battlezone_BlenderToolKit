from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

try:
    import bpy
except ImportError:
    bpy = None

from bpy.props import (
    BoolProperty,
    FloatProperty,
    StringProperty,
    EnumProperty,
    FloatVectorProperty,
    CollectionProperty,
    IntProperty,
)

from bpy_extras.io_utils import (
    ImportHelper,
    ExportHelper,
)

import os
import math
import hashlib
import json
import mathutils
import struct
import textwrap

from . import validation as bz_validation

if bpy is not None:
    from . import map_io as bz_map_io

ZFS_TEXTURE_EXTENSIONS = {
    ".map",
    ".pic",
    ".tga",
    ".dds",
    ".png",
    ".bmp",
    ".jpg",
    ".jpeg",
}

LEGACY_VEHICLE_FORWARD_LABEL = "Legacy VDF/SDF/GEO vehicle front: Blender +Y"
REDUX_MESH_FORWARD_LABEL = "Direct Redux .mesh vehicle front: Blender -Y"
AUTO_PORT_FORWARD_NOTE = (
    "Legacy + Redux export uses the legacy +Y setup, then converts for Redux."
)

GEO_FACE_PLANE_MODE_ITEMS = (
    (
        "CURRENT",
        "Current Toolkit Default",
        "Write the historical toolkit values: face center X/Y/Z and D = 1.0",
    ),
    (
        "PRESERVE",
        "Preserve Imported",
        "Write imported bz_face_plane_x/y/z/d attributes when present; otherwise use the current default",
    ),
    (
        "RECOMPUTE",
        "Recompute From Faces",
        "Write a normalized face plane and distance from the exported GEO-coordinate vertices",
    ),
    (
        "DX_FIX",
        "DX Normal Distance Fix",
        "Best-effort recreation of the old Max GEO exporter workaround for disappearing triangles; currently recomputes face plane normal and distance",
    ),
)


def get_default_ogre_xml_converter():
    """Try to find OgreXMLConverter.exe in the bundled ogretools folder."""
    addon_dir = os.path.dirname(__file__)
    candidate = os.path.join(addon_dir, "ogretools", "OgreXMLConverter.exe")
    if os.path.isfile(candidate):
        return candidate
    # Fallback: empty string; user can pick it manually
    return ""


def get_default_zfs_cache_dir():
    addon_dir = os.path.dirname(__file__)
    return os.path.join(addon_dir, "_cache", "zfs")


def get_export_preset_dir(export_kind):
    addon_dir = os.path.dirname(__file__)
    return os.path.join(addon_dir, "_presets", export_kind.lower())


def get_zfs_archive_cache_dir(zfs_path, cache_root=None):
    if not zfs_path:
        return ""
    cache_root = os.path.abspath(cache_root or get_default_zfs_cache_dir())
    archive_name = os.path.splitext(os.path.basename(zfs_path))[0]
    archive_hash = hashlib.sha1(zfs_path.encode("utf-8")).hexdigest()[:8]
    return os.path.join(cache_root, f"{archive_name}_{archive_hash}")


if "bpy" in locals():
    import importlib


# ----------------------------------------------------------
#  Updated for Blender 4.5 LTS compatibility
# ----------------------------------------------------------
bl_info = {
    "name": "Battlezone GEO/VDF/SDF Formats (For Blender 4.5 LTS)",
    "description": "Import and export GEO/VDF/SDF files from Battlezone (1998 / Redux).",
    "author": "GrizzlyOne95, Commando950, DivisionByZero, Business Lawyer, Kindrad; inspired by Lucius64",
    "version": (1, 4, 8),
    "blender": (4, 5, 1),
    "category": "Import-Export",
    "wiki_url": "https://commando950.neocities.org/docs/BZBlenderAddon/",
}

TERNARY_ITEMS = [
    ("AUTO", "Auto", "Use automatic detection"),
    ("YES", "Force Yes", "Force enabled"),
    ("NO", "Force No", "Force disabled"),
]

NORMAL_MODE_ITEMS = (
    ("CORRECT", "Correct Inverted", "Detect and flip mostly-inverted normals (default)"),
    ("NONE", "Leave Unchanged", "Do not modify normals"),
    ("FLIP", "Flip All", "Indiscriminately flip all normals"),
)

ANIMATION_PRESET_ITEMS = (
    (
        "DEPLOY_PAIR",
        "Deploy Pair",
        "Add slots 0 and 1 for common deploy/undeploy workflows",
    ),
    (
        "TURRET_PAIR",
        "Turret Pair",
        "Add slots 0 and 1 for common turret motion workflows",
    ),
    (
        "WALKER_CORE",
        "Walker Core",
        "Add slots 2 through 7 for a typical walker idle and movement set",
    ),
    (
        "PERSON_CORE",
        "Person Core",
        "Add slots 0 through 11 for a broad person animation starter set",
    ),
)

VALIDATION_PRESET_ITEMS = (
    ("AUTO", "General", "Run the standard legacy export checks"),
    (
        "VEHICLE",
        "Vehicle / VDF",
        "Run vehicle-oriented checks, including POV and COL helpers",
    ),
    (
        "TURRET",
        "Turret",
        "Require turret animation slots and turret/cockpit conventions",
    ),
    ("WALKER", "Walker", "Require standard walker animation slots"),
    ("PERSON", "Person", "Require standard person animation slots"),
    ("ANIMATED", "Animated", "Require at least one legacy ANIM sequence entry"),
)

ANIMATION_PRESET_SLOTS = {
    "DEPLOY_PAIR": [0, 1],
    "TURRET_PAIR": [0, 1],
    "WALKER_CORE": [2, 3, 4, 5, 6, 7],
    "PERSON_CORE": list(range(0, 12)),
}

LEGACY_TRANSFORM_DATA_PATHS = {
    "location",
    "rotation_euler",
    "rotation_quaternion",
    "rotation_axis_angle",
    "scale",
}

QUICK_VEHICLE_GEO_SPECS = (
    ("bod", 60, "Body", (0.0, 0.0, 0.0), 0.28),
    ("pov", 40, "POV", (0.0, 0.85, 0.45), 0.12),
    ("gc1", 71, "GC1", (-0.22, 0.7, 0.18), 0.10),
    ("gr1", 72, "GR1", (0.22, 0.7, 0.18), 0.10),
    ("gs1", 74, "GS1", (-0.22, 0.15, 0.18), 0.10),
    ("gm1", 73, "GM1", (0.22, 0.15, 0.18), 0.10),
    ("rot", 66, "Rotor", (-0.45, 0.0, 0.12), 0.12),
    ("nac", 67, "Nacelle", (0.45, 0.0, 0.12), 0.12),
)

EXPORT_KIND_IDNAMES = {
    "GEO": "export_scene.geo",
    "VDF": "export_scene.vdf",
    "SDF": "export_scene.sdf",
}

EXPORT_PRESET_PROPERTY_NAMES = {
    "GEO": (
        "face_plane_mode",
        "auto_port_ogre",
        "ogre_name",
        "ogre_suffix",
        "ogre_flat_colors",
        "ogre_bounds_mult",
        "ogre_act_path",
        "ogre_config_path",
        "ogre_only_once",
        "ogre_nowrite",
        "ogre_skip_unchanged",
        "ogre_profile_export",
        "ogre_dest_dir",
    ),
    "VDF": (
        "ExportAnimations",
        "ExportVDFOnly",
        "face_plane_mode",
        "auto_port_ogre",
        "ogre_name",
        "ogre_suffix",
        "ogre_flat_colors",
        "ogre_bounds_mult",
        "ogre_act_path",
        "ogre_config_path",
        "ogre_only_once",
        "ogre_nowrite",
        "ogre_skip_unchanged",
        "ogre_profile_export",
        "ogre_dest_dir",
        "ogre_headlights",
        "ogre_person_mode",
        "ogre_turret_mode",
        "ogre_cockpit_mode",
        "ogre_skeletalanims_mode",
        "ogre_scope_mode",
        "ogre_scope_type",
        "ogre_scope_nation",
        "ogre_scope_screen",
        "ogre_scope_gun",
        "ogre_scope_transform",
        "ogre_scope_texture",
        "ogre_no_pov_rots",
        "ogre_stabilize_walker_cockpit",
    ),
    "SDF": (
        "ExportAnimations",
        "ExportSDFOnly",
        "face_plane_mode",
        "auto_port_ogre",
        "ogre_name",
        "ogre_suffix",
        "ogre_flat_colors",
        "ogre_bounds_mult",
        "ogre_act_path",
        "ogre_config_path",
        "ogre_only_once",
        "ogre_nowrite",
        "ogre_skip_unchanged",
        "ogre_profile_export",
        "ogre_dest_dir",
    ),
}

BUILTIN_EXPORT_PRESETS = {
    "GEO": (
        (
            "classic_geo",
            "Legacy GEO Only",
            {
                "face_plane_mode": "CURRENT",
                "auto_port_ogre": False,
                "ogre_name": "",
                "ogre_suffix": "_port",
                "ogre_flat_colors": False,
                "ogre_bounds_mult": [1.0, 1.0, 1.0],
                "ogre_act_path": "",
                "ogre_config_path": "",
                "ogre_only_once": False,
                "ogre_nowrite": False,
                "ogre_skip_unchanged": True,
                "ogre_profile_export": False,
                "ogre_dest_dir": "",
            },
        ),
        (
            "geo_port",
            "Legacy GEO + Redux",
            {
                "face_plane_mode": "CURRENT",
                "auto_port_ogre": True,
                "ogre_name": "",
                "ogre_suffix": "_port",
                "ogre_flat_colors": False,
                "ogre_bounds_mult": [1.0, 1.0, 1.0],
                "ogre_act_path": "",
                "ogre_config_path": "",
                "ogre_only_once": False,
                "ogre_nowrite": False,
                "ogre_skip_unchanged": True,
                "ogre_profile_export": False,
                "ogre_dest_dir": "",
            },
        ),
    ),
    "VDF": (
        (
            "vehicle_vdf",
            "Legacy Vehicle Only",
            {
                "ExportAnimations": True,
                "ExportVDFOnly": False,
                "face_plane_mode": "CURRENT",
                "auto_port_ogre": False,
                "ogre_name": "",
                "ogre_suffix": "_port",
                "ogre_flat_colors": False,
                "ogre_bounds_mult": [1.0, 1.0, 1.0],
                "ogre_act_path": "",
                "ogre_config_path": "",
                "ogre_only_once": False,
                "ogre_nowrite": False,
                "ogre_skip_unchanged": True,
                "ogre_profile_export": False,
                "ogre_dest_dir": "",
                "ogre_headlights": False,
                "ogre_person_mode": "AUTO",
                "ogre_turret_mode": "AUTO",
                "ogre_cockpit_mode": "AUTO",
                "ogre_skeletalanims_mode": "AUTO",
                "ogre_scope_mode": "AUTO",
                "ogre_scope_type": "AUTO",
                "ogre_scope_nation": "",
                "ogre_scope_screen": [0.0, 0.0, 0.0, 1.0, 0.0],
                "ogre_scope_gun": "",
                "ogre_scope_transform": [
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                ],
                "ogre_scope_texture": "__scope",
                "ogre_no_pov_rots": False,
                "ogre_stabilize_walker_cockpit": False,
            },
        ),
        (
            "vehicle_vdf_port",
            "Legacy Vehicle + Redux",
            {
                "ExportAnimations": True,
                "ExportVDFOnly": False,
                "face_plane_mode": "CURRENT",
                "auto_port_ogre": True,
                "ogre_name": "",
                "ogre_suffix": "_port",
                "ogre_flat_colors": False,
                "ogre_bounds_mult": [1.0, 1.0, 1.0],
                "ogre_act_path": "",
                "ogre_config_path": "",
                "ogre_only_once": False,
                "ogre_nowrite": False,
                "ogre_skip_unchanged": True,
                "ogre_profile_export": False,
                "ogre_dest_dir": "",
                "ogre_headlights": False,
                "ogre_person_mode": "AUTO",
                "ogre_turret_mode": "AUTO",
                "ogre_cockpit_mode": "AUTO",
                "ogre_skeletalanims_mode": "AUTO",
                "ogre_scope_mode": "AUTO",
                "ogre_scope_type": "AUTO",
                "ogre_scope_nation": "",
                "ogre_scope_screen": [0.0, 0.0, 0.0, 1.0, 0.0],
                "ogre_scope_gun": "",
                "ogre_scope_transform": [
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                ],
                "ogre_scope_texture": "__scope",
                "ogre_no_pov_rots": False,
                "ogre_stabilize_walker_cockpit": False,
            },
        ),
    ),
    "SDF": (
        (
            "structure_sdf",
            "Legacy Structure Only",
            {
                "ExportAnimations": True,
                "ExportSDFOnly": False,
                "face_plane_mode": "CURRENT",
                "auto_port_ogre": False,
                "ogre_name": "",
                "ogre_suffix": "_port",
                "ogre_flat_colors": False,
                "ogre_bounds_mult": [1.0, 1.0, 1.0],
                "ogre_act_path": "",
                "ogre_config_path": "",
                "ogre_only_once": False,
                "ogre_nowrite": False,
                "ogre_skip_unchanged": True,
                "ogre_profile_export": False,
                "ogre_dest_dir": "",
            },
        ),
        (
            "structure_sdf_port",
            "Legacy Structure + Redux",
            {
                "ExportAnimations": True,
                "ExportSDFOnly": False,
                "face_plane_mode": "CURRENT",
                "auto_port_ogre": True,
                "ogre_name": "",
                "ogre_suffix": "_port",
                "ogre_flat_colors": False,
                "ogre_bounds_mult": [1.0, 1.0, 1.0],
                "ogre_act_path": "",
                "ogre_config_path": "",
                "ogre_only_once": False,
                "ogre_nowrite": False,
                "ogre_skip_unchanged": True,
                "ogre_profile_export": False,
                "ogre_dest_dir": "",
            },
        ),
    ),
}

"""
PROPERTY GROUP DEFINITION CLASSES
Used for custom properties and keeping track of them.
AnimationPropertyGroup - All the properties for an animation element that will be avaliable for editing/storing.
SDFPropertyGroup - The properties of a SDF that will be avaliable for editing/storing.
VDFPropertyGroup - The properties of a VDF that will be avaliable for editing/storing.
GEOPropertyGroup - The properties of a GEO that will be avaliable for editing/storing.
"""


class AnimationPropertyGroup(bpy.types.PropertyGroup):
    Index: Any = bpy.props.IntProperty(
        name="Index",
        description="Animation index that decides what the animation is for",
        default=0,
        min=0,
        max=1000,
    )

    Start: Any = bpy.props.IntProperty(
        name="Start",
        description="First frame of the animation",
        default=0,
        min=-999999,
        max=999999,
    )

    Length: Any = bpy.props.IntProperty(
        name="Length",
        description="Amount of frames we run and can be negative",
        default=0,
        min=-999999,
        max=999999,
    )

    Loop: Any = bpy.props.IntProperty(
        name="Loop Count",
        description="How many times will the animation loop?",
        default=1,
        min=0,
        max=100,
    )

    Speed: Any = bpy.props.FloatProperty(
        name="Speed",
        description="Speed the animation plays at",
        default=15.0,
        min=-999999.0,
        max=999999.0,
    )

    UseCustomUnknownGeoMask: Any = bpy.props.BoolProperty(
        name="Use Custom Mesh Slot Mask",
        description="Use a custom ANIM meshIndex[32] slot mask instead of automatic defaults",
        default=False,
    )

    UnknownGeoMask: Any = bpy.props.IntVectorProperty(
        name="Mesh Slot Mask",
        description="Raw ANIM element meshIndex[32] values; stock files use 0/1 to mark affected GEO mesh slots",
        size=32,
        default=(0,) * 32,
    )


class SDFVDFPropertyGroup(bpy.types.PropertyGroup):
    # Shared Properties.
    Name: Any = bpy.props.StringProperty(
        name="Name",
        description="The name inside the file. Kinda unimportant",
        default="New Thing",
        maxlen=16,
    )

    # VDF Properties
    VehicleSize: Any = bpy.props.IntProperty(
        name="Vehicle Size",
        description="Legacy VDF vehicle size/class field. Stock vehicles normally use 2; pilots, powerups, camera/repair helpers, and similar small objects commonly use 1.",
        default=2,
        min=0,
        max=10,
    )

    VehicleType: Any = bpy.props.IntProperty(
        name="Vehicle Type",
        description="Legacy VDF vehicle type field. Stock assets examined so far use 1; preserve imported values for compatibility.",
        default=1,
        min=0,
        max=10,
    )

    Mass: Any = bpy.props.FloatProperty(
        name="Mass",
        description="Set how heavy the object is(Default: 1750.0 on many VDFs)",
        default=1750.0,
        min=0.0,
        max=34028234.0,
    )

    CollMult: Any = bpy.props.FloatProperty(
        name="Collision Multiplier(?)",
        description="Unknown(Default: 1.0)",
        default=1.0,
        min=0.0,
        max=34028234.0,
    )

    DragCoefficient: Any = bpy.props.FloatProperty(
        name="Drag Coefficient",
        description="Aerodynamics(?)(Default: 0.0008)",
        default=0.0008,
        min=0.0,
        max=34028234.0,
    )

    StructureType: Any = bpy.props.IntProperty(
        name="Structure Type",
        description="Legacy SDF structure type field. Stock assets examined so far use 1; preserve imported values for compatibility.",
        default=1,
        min=0,
        max=10,
    )

    # SDF Properties here!
    Defensive: Any = bpy.props.IntProperty(
        name="SDFC DDR",
        description="Raw SDFC DDR integer after the LOD distances. Stock structures commonly use 500000; mine/powerup-like SDFs commonly use smaller values such as 200.",
        default=500000,
        min=0,
        max=2147483647,
    )

    DeathExplosion: Any = bpy.props.StringProperty(
        name="",
        description="The .xdf explosion type the building uses",
        default="xbldx1.xdf",
        maxlen=13,
    )

    DeathSound: Any = bpy.props.StringProperty(
        name="",
        description="The sound the building plays when destroyed",
        default="null",
        maxlen=13,
    )

    # LOD Properties
    LOD1: Any = bpy.props.FloatProperty(
        name="LOD1 Distance",
        description="The distance that this LOD(Level of Detail) will load",
        default=5.0,
        min=0.0,
        max=3.40282e38,
    )

    LOD2: Any = bpy.props.FloatProperty(
        name="LOD2 Distance",
        description="The distance that this LOD(Level of Detail) will load",
        default=3.40282e38,
        min=0.0,
        max=3.40282e38,
    )

    LOD3: Any = bpy.props.FloatProperty(
        name="LOD3 Distance",
        description="The distance that this LOD(Level of Detail) will load",
        default=3.40282e38,
        min=0.0,
        max=3.40282e38,
    )

    LOD4: Any = bpy.props.FloatProperty(
        name="LOD4 Distance",
        description="The distance that this LOD(Level of Detail) will load",
        default=3.40282e38,
        min=0.0,
        max=3.40282e38,
    )

    LOD5: Any = bpy.props.FloatProperty(
        name="LOD5 Distance",
        description="The distance that this LOD(Level of Detail) will load",
        default=3.40282e38,
        min=0.0,
        max=3.40282e38,
    )

    UseAdvancedAnimHeader: Any = bpy.props.BoolProperty(
        name="Use Advanced ANIM Header",
        description="Use custom raw ANIM pointer-placeholder fields; stock exported files normally leave these as zero",
        default=False,
    )

    AnimNull2: Any = bpy.props.IntProperty(
        name="ANIM animPtr Placeholder",
        description="Raw legacy ANIM header animPtr placeholder; stock disk files normally store 0",
        default=0,
        min=-2147483648,
        max=2147483647,
    )

    AnimUnknown2: Any = bpy.props.IntProperty(
        name="ANIM meshPtr Placeholder",
        description="Raw legacy ANIM header meshPtr placeholder; stock disk files normally store 0",
        default=0,
        min=-2147483648,
        max=2147483647,
    )

    AnimReserved: Any = bpy.props.IntVectorProperty(
        name="ANIM Key/Object Placeholders",
        description="Raw legacy ANIM rotKey/sclKey/posKey/object/entity pointer placeholders",
        size=5,
        default=(0, 0, 0, 0, 0),
    )

    UseTranslation2Track: Any = bpy.props.BoolProperty(
        name="Export Scale Keys to SCLKEY",
        description="Write object scale animation into the legacy ANIM SCLKEY/Translation2 slot used by a few stock assets; location keys remain in POSKEY",
        default=False,
    )

    UseCustomSCPS: Any = bpy.props.BoolProperty(
        name="Use Custom SPCS/SCPS",
        description="Write custom raw VDF SPCS/SCPS compatibility ints; most stock VDFs omit this chunk",
        default=False,
    )

    SCPSData: Any = bpy.props.IntVectorProperty(
        name="SPCS/SCPS Data",
        description="Three raw compatibility ints for the optional VDF shell/SCPS chunk path",
        size=3,
        default=(0, 0, 0),
    )


geotypes = []
geotype_lookup = {}


def insertgeotypedata(idx, label):
    item = (idx, f"{idx} - {label}", "")
    geotypes.append(item)
    geotype_lookup[idx] = item


# -------------------------------------------------------------------
# GEO CLASS IDs (from Battlezone’s CLASS_ID_* definitions)
# -------------------------------------------------------------------
insertgeotypedata(0, "NONE (Does nothing, part of main object)")
insertgeotypedata(1, "HELICOPTER (Crashes game as VDF/SDF – do not use)")
insertgeotypedata(2, "STRUCTURE1 (Wooden structures, unknown)")
insertgeotypedata(3, "POWERUP (Crashes game as VDF/SDF)")
insertgeotypedata(4, "PERSON (Unknown / untested)")
insertgeotypedata(5, "SIGN (Unknown / untested)")
insertgeotypedata(6, "VEHICLE (Unknown / untested)")
insertgeotypedata(7, "SCRAP (Scrap material)")
insertgeotypedata(8, "BRIDGE (Structure containing floor; likely no extra behavior)")
insertgeotypedata(9, "FLOOR (Bridge floor; likely no extra behavior)")
insertgeotypedata(10, "STRUCTURE2 (Metal structures)")
insertgeotypedata(11, "SCROUNGE (Faces GEO toward camera)")

# Old “LGT” guess – still unknown, keep for completeness
insertgeotypedata(33, "LGT (Vehicle light? – legacy guess)")

insertgeotypedata(15, "SPINNER (Spinner / rotating geo; usable on VDFs & buildings)")

insertgeotypedata(34, "RADAR (Unknown, likely no effect)")
insertgeotypedata(38, "HEADLIGHT_MASK (Redux headlight bone)")
insertgeotypedata(40, "EYEPOINT (POV / sniper dot / 1st-person origin)")
insertgeotypedata(42, "COM (Center of mass; unused)")

# Legacy geometry “role” IDs
insertgeotypedata(50, "WEAPON (Weapon geometry – legacy)")
insertgeotypedata(51, "ORDNANCE (Ordnance geometry – legacy)")
insertgeotypedata(52, "EXPLOSION (Explosion geometry)")
insertgeotypedata(53, "CHUNK (Chunk geometry)")
insertgeotypedata(54, "SORT_OBJECT (Sorting object)")
insertgeotypedata(55, "NONCOLLIDABLE (Non-collidable geometry)")

# Modern geometry role IDs
insertgeotypedata(60, "VEHICLE_GEOMETRY (Vehicle geometry / body)")
insertgeotypedata(61, "STRUCTURE_GEOMETRY (Structure geometry)")
insertgeotypedata(63, "WEAPON_GEOMETRY (Weapon geometry)")
insertgeotypedata(64, "ORDNANCE_GEOMETRY (Ordnance geometry)")
insertgeotypedata(65, "TURRET_GEOMETRY (X/Y turret rotators, gun towers)")
insertgeotypedata(66, "ROTOR_GEOMETRY (Rotates on A/D thrust)")
insertgeotypedata(67, "NACELLE_GEOMETRY (Thrust/steering nacelle; W/A/S/D + flame)")
insertgeotypedata(68, "FIN_GEOMETRY (Steering fin)")
insertgeotypedata(69, "COCKPIT_GEOMETRY (Cockpit geometry)")

# Hardpoints & emitters
insertgeotypedata(
    70,
    "WEAPON_HARDPOINT (* hardpoint, no default powerups) Also Prod Unit Smoke Emitter",
)
insertgeotypedata(71, "CANNON_HARDPOINT")
insertgeotypedata(72, "ROCKET_HARDPOINT")
insertgeotypedata(73, "MORTAR_HARDPOINT")
insertgeotypedata(74, "SPECIAL_HARDPOINT (where prod unit throws out a build")
insertgeotypedata(
    75,
    "FLAME_EMITTER (Visible on full forward thrust). Makes the geo geometry invisible.",
)
insertgeotypedata(76, "SMOKE_EMITTER")
insertgeotypedata(77, "DUST_EMITTER")

insertgeotypedata(81, "PARKING_LOT (Hangar / supply pad center of effect)")

GEO_TYPE_ENUM_ITEMS = [(f"GEO_{idx}", label, label, idx) for idx, label, _ in geotypes]

GEO_TYPE_UI_HINTS = {
    1: (
        "ERROR",
        "Type 1 is known to crash as a VDF/SDF GEO. Avoid using it in legacy vehicle/structure exports.",
    ),
    3: (
        "ERROR",
        "Type 3 is known to crash as a VDF/SDF GEO. Avoid using it in legacy vehicle/structure exports.",
    ),
    15: (
        "INFO",
        "Type 15 is used for spinner behavior. Spinner helpers normally export with this role.",
    ),
    38: ("INFO", "Type 38 is typically used as a Redux headlight mask helper."),
    40: (
        "INFO",
        "Type 40 is the POV / eyepoint. For Redux turret cockpits, parent POV under ty# yaw instead of tx# pitch.",
    ),
    65: (
        "INFO",
        "Type 65 is a turret rotator. Use ty# for yaw, tx# for pitch, and keep cockpit geometry off the POV bone.",
    ),
    70: ("INFO", "Type 70 marks a weapon hardpoint or production smoke emitter."),
    71: ("INFO", "Type 71 marks a cannon hardpoint. Use suffix gc1-gc5."),
    72: ("INFO", "Type 72 marks a rocket hardpoint. Use suffix gr1-gr5."),
    73: ("INFO", "Type 73 marks a mortar hardpoint. Use suffix gm1-gm5."),
    74: ("INFO", "Type 74 marks a special hardpoint. Use suffix gs1-gs5."),
    75: (
        "WARNING",
        "Type 75 acts as a flame emitter and usually makes the GEO geometry itself invisible.",
    ),
    76: ("INFO", "Type 76 marks a smoke emitter."),
    77: ("INFO", "Type 77 marks a dust emitter."),
}


DUAL_COMPONENT_INFO_LINES = (
    "Battlezone 98 Redux separates legacy gameplay data from rendered visuals.",
    "",
    "Physics / legacy component:",
    "VDF files define vehicle physics, collision, hardpoints, and legacy animation behavior.",
    "SDF files define structure physics, collision, effect points, and legacy animation behavior.",
    "GEO files are the individual movable or animated legacy parts inside VDF/SDF files.",
    "",
    "Visual / animation component:",
    "OGRE .mesh files define rendered vertices, faces, normals, UVs, skin weights, tangents, and colors.",
    "OGRE .skeleton files define the visual bone hierarchy and animations.",
    "Redux skeleton bones should use the same names, orientation, and pivots as matching GEO parts.",
    "Material files define texture and shader assignments for the visual mesh.",
)

ADVANCED_VDF_INFO_LINES = (
    "Advanced VDF editing support:",
    "",
    "Spinner helpers: create a dummy/helper GEO after the target, set it as a Spinner Helper, assign Spinner Target, Axis, and Speed. Export writes Type 15 behavior without manual hex editing.",
    "Axis vector direction controls spin axis; vector magnitude times speed is angular speed in radians per second.",
    "",
    "Raw transform scaling: enable Use Raw VDF Matrix in Experimental, then edit the 12 floats in right, up, front, position order.",
    "This exposes the same transform data normally edited in HxD for hardpoint or visual scaling experiments.",
    "",
    "Keep backups when experimenting with raw VDF transforms. Some scaled hardpoints or helpers can change gameplay behavior.",
)

PIVOT_DUMMYROOT_INFO_LINES = (
    "Pivot and dummyroot notes:",
    "",
    "In Blender, the object origin is the Battlezone GEO pivot. Rotation, explosion behavior, cockpit alignment, POV direction, and emitters all depend on that origin and its local axes.",
    "For rotators, gun barrels, wings, doors, and emitters, place the origin exactly on the intended hinge/emitter point and keep the object's local orientation deliberate.",
    "LOD2 cockpit GEOs should have a matching LOD1 counterpart, be linked/parented to it, and share the same origin and orientation.",
    "",
    "Legacy dummyroot was a makeobj authoring convention. The addon does not require a Blender object named dummyroot.",
    "Use one clean root model hierarchy per export scene. An unparented exportable root GEO writes as WORLD/root in the legacy hierarchy.",
    "Author hover/sink offsets by placing the root/object origins at the intended model reference point, then moving the mesh geometry relative to that origin as needed.",
    "Keep unrelated linked or exportable model hierarchies out of the scene before VDF/SDF export.",
)

VALIDATION_CHECK_LINES = (
    "Validation coverage:",
    "Active GEO export object is selected, is a mesh, and has vertices.",
    "Legacy VDF/SDF names have valid length and LOD markers.",
    "GEO first-character naming best practice is reported as info.",
    "Normalized export names do not collide.",
    "Parents resolve to exportable objects in the same LOD set.",
    "Multiple top-level export roots are warned because unrelated linked models can corrupt legacy output.",
    "LOD2 cockpit counterparts are checked for matching LOD1 part, parent, pivot, and orientation.",
    "LOD3 parts are checked for matching LOD1, low-detail intent, and unneeded Redux usage.",
    "Mesh face topology and material texture names are legacy-safe.",
    "GEO type and common hardpoint suffix combinations are checked.",
    "Armatures, skinning, shape keys, constraints, and unsupported animation channels are flagged.",
    "VDF vehicle essentials are checked: POV/eyepoint and inner_col/outer_col helpers, with structure/producer-style exceptions.",
    "Collision helper names and mesh presence are checked.",
    "Animation presets check required slots for turret, walker, person, and animated workflows.",
    "Animation guide checks report loop first/last pose mismatches, high animated-GEO counts, and preferred legacy frame ranges.",
    "Turret and cockpit conventions are checked, including cockpit rotator matching.",
)


def _draw_wrapped_label(layout, text, icon="NONE", width=64, scale_y=0.9):
    lines = textwrap.wrap(str(text), width=width) or [""]
    for index, line in enumerate(lines):
        row = layout.row()
        row.scale_y = scale_y
        if index == 0 and icon != "NONE":
            row.label(text=line, icon=icon)
        else:
            row.label(text=line)


def _draw_wrapped_lines(layout, lines, icon="NONE", width=64):
    for line in lines:
        if line == "":
            layout.separator()
        else:
            _draw_wrapped_label(
                layout, line, icon=icon if line == lines[0] else "NONE", width=width
            )


def _scene_has_headlight_geo(scene):
    for obj in getattr(scene, "objects", []):
        geo = getattr(obj, "GEOPropertyGroup", None)
        if geo is not None and int(getattr(geo, "GEOType", 0)) == 38:
            return True
    return False


def _fix_geo_export_name(name, lod):
    geofilename = list(name)
    if len(geofilename) > 8:
        geofilename = geofilename[0:8]
    if lod in (1, 2, 3):
        geofilename[3] = str(lod)
    else:
        geofilename[3] = "3"
    geofilename[4] = "1"
    return "".join(geofilename)


def _get_geotype_label(geo_type_value):
    item = geotype_lookup.get(int(geo_type_value))
    if item is not None:
        return item[1]
    return f"{int(geo_type_value)} - Unknown"


def _get_geotype_enum_value(self):
    value = int(getattr(self, "GEOType", 60))
    if value in geotype_lookup:
        return value
    return 60


def _set_geotype_enum_value(self, value):
    self["GEOType"] = int(value)


def _draw_geotype_hint(layout, geo_type_value):
    hint = GEO_TYPE_UI_HINTS.get(int(geo_type_value))
    if hint is None:
        return

    severity, message = hint
    box = layout.box()
    if severity == "ERROR":
        icon = "ERROR"
    elif severity == "WARNING":
        icon = "WARNING"
    else:
        icon = "INFO"
    _draw_wrapped_label(box, message, icon=icon, width=56)


def _get_object_lod_label(obj):
    name = getattr(obj, "name", "")
    if len(name) >= 4 and name[3] in {"1", "2", "3"}:
        if name[3] == "2":
            return "LOD 2 - cockpit GEO"
        if name[3] == "3":
            return "LOD 3 - low-poly legacy GEO"
        return "LOD 1 - primary GEO"
    return "LOD unknown"


def _draw_lod_context_notes(layout, obj):
    name = getattr(obj, "name", "")
    if len(name) < 4:
        return
    if name[3] == "2":
        _draw_wrapped_label(
            layout,
            "Cockpit GEO: Redux treats LOD 2 as cockpit/first-person geometry.",
            icon="INFO",
            width=56,
        )
    elif name[3] == "3":
        _draw_wrapped_label(
            layout,
            "LOD 3 GEOs are unneeded for Redux and optional for legacy BZ 1.5.",
            icon="WARNING",
            width=56,
        )


def _sanitize_quick_geo_prefix(value):
    source = (value or "").strip().lower()
    chars = [ch for ch in source if ch.isalnum()]
    if not chars:
        chars = list("veh")
    return ("".join(chars) + "veh")[:3]


def _quick_geo_prefix_from_scene(context):
    scene = getattr(context, "scene", None)
    props = getattr(scene, "SDFVDFPropertyGroup", None)
    return _sanitize_quick_geo_prefix(getattr(props, "Name", "veh"))


def _find_available_quick_geo_prefix(prefix):
    suffixes = [spec[0] for spec in QUICK_VEHICLE_GEO_SPECS]

    def is_available(candidate):
        return all(
            bpy.data.objects.get(f"{candidate}11{suffix}") is None
            for suffix in suffixes
        )

    prefix = _sanitize_quick_geo_prefix(prefix)
    if is_available(prefix):
        return prefix

    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    for first in digits:
        for second in digits:
            candidate = (prefix[0] + first + second)[:3]
            if is_available(candidate):
                return candidate
    return ""


def _create_box_mesh(name, size):
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


def _get_selected_face_indices(obj):
    if obj is None or getattr(obj, "type", None) != "MESH":
        return []

    mesh = getattr(obj, "data", None)
    if mesh is None:
        return []

    if getattr(obj, "mode", "OBJECT") == "EDIT":
        try:
            import bmesh

            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            return [face.index for face in bm.faces if face.select]
        except Exception:
            return []

    return [poly.index for poly in mesh.polygons if getattr(poly, "select", False)]


def _get_face_attr_int(mesh, attr_name, face_index, default_value=0):
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


def _get_face_attr_float(mesh, attr_name, face_index, default_value=0.0):
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


def _summarize_face_values(mesh, face_indices, attr_name, default_value=0):
    values = [
        _get_face_attr_int(mesh, attr_name, face_index, default_value)
        for face_index in face_indices
    ]
    if not values:
        return "None"
    unique_values = sorted(set(values))
    if len(unique_values) == 1:
        return str(unique_values[0])
    return f"Mixed ({len(unique_values)} values, {min(unique_values)}..{max(unique_values)})"


def _summarize_face_float_values(mesh, face_indices, attr_name, default_value=0.0):
    values = [
        round(_get_face_attr_float(mesh, attr_name, face_index, default_value), 6)
        for face_index in face_indices
    ]
    if not values:
        return "None"
    unique_values = sorted(set(values))
    if len(unique_values) == 1:
        return f"{unique_values[0]:.6g}"
    return f"Mixed ({len(unique_values)} values, {min(unique_values):.6g}..{max(unique_values):.6g})"


def _draw_legacy_geo_naming_note(layout):
    box = layout.box()
    box.label(text="Legacy Naming Notes", icon="INFO")
    _draw_wrapped_label(
        box,
        "Body/root GEOs export with parent WORLD when no exportable parent is found.",
        width=56,
    )
    _draw_wrapped_label(
        box, "Common suffixes: bd*, pov, gc*, gr*, gm*, gs*, tx*, ty*.", width=56
    )
    _draw_wrapped_label(
        box, "Vehicle exports should include a Type 40 POV/eyepoint.", width=56
    )


def _draw_geo_face_plane_export_box(layout, owner):
    experimental = layout.box()
    experimental.label(text="Experimental GEO Face Plane")
    experimental.prop(owner, "face_plane_mode")
    if owner.face_plane_mode == "DX_FIX":
        experimental.label(
            text="Best-effort legacy Max exporter workaround; verify in-game.",
            icon="WARNING",
        )
    elif owner.face_plane_mode == "PRESERVE":
        experimental.label(
            text="Uses imported bz_face_plane_x/y/z/d attributes when present.",
            icon="INFO",
        )


# -------------------------------
# Animation Index Reference Popup
# -------------------------------


def draw_anim_index_reference_popup(self, context):
    layout = self.layout
    col = layout.column(align=False)

    col.label(text="Battlezone Animation Index Reference")
    col.separator()
    col.label(text="Indexes are per-classLabel; same index can")
    col.label(text="mean different things on different units.")
    col.separator()

    # Recycler
    col.label(text="Recycler (classLabel = recycler)")
    col.label(text="  0: Deploy")
    col.label(text="  1: Undeploy")
    col.separator()

    # Factory
    col.label(text="Factory (classLabel = factory)")
    col.label(text="  0: Deploy")
    col.label(text="  1: Undeploy")
    col.label(text="  2: Idle")
    col.label(text="  3: Deployed Idle")
    col.label(text="  4: Deployed & Ready Idle")
    col.separator()

    # Armory
    col.label(text="Armory (classLabel = armory)")
    col.label(text="  0: Launch")
    col.label(text="  1: Launch (Reverse)")
    col.separator()

    # Construction Rig
    col.label(text="ConstructionRig (classLabel = constructionrig)")
    col.label(text="  0: Deploying / Start Construction")
    col.label(text="  1: Undeploying / Finished Construction")
    col.label(text="  4: Deployed & Currently Constructing")
    col.separator()

    # Person
    col.label(text="Person (classLabel = person)")
    col.label(text="  0: Stand → Snipe")
    col.label(text="  1: Snipe → Stand")
    col.label(text="  2: Standing / Idle")
    col.label(text="  3: Sniping / Idle")
    col.label(text="  4: Walk Forwards")
    col.label(text="  5: Walk Backwards")
    col.label(text="  6: Strafe Left")
    col.label(text="  7: Strafe Right")
    col.label(text="  8: Sniped (death)")
    col.label(text="  9: idleParachute (falling from sky)")
    col.label(text="  10: landParachute (as pilot hits ground)")
    col.label(text="  11: Jump")
    col.separator()

    # Scavenger
    col.label(text="Scavenger (classLabel = scavenger)")
    col.label(text="  0: Deploy")
    col.label(text="  1: Undeploy")
    col.label(text="  2: Idle")
    col.label(text="  3: Deployed Idle")
    col.label(text="  4: Undeploy (alt)")
    col.separator()

    # Tug
    col.label(text="Tug (classLabel = tug)")
    col.label(text="  0: Deploy")
    col.label(text="  1: Undeploy")
    col.separator()

    # Howitzer
    col.label(text="Howitzer (classLabel = howitzer)")
    col.label(text="  0: Deploy")
    col.label(text="  1: Undeploy")
    col.separator()

    # TurretTank
    col.label(text="TurretTank (classLabel = turrettank)")
    col.label(text="  0: Deploy")
    col.label(text="  1: Undeploy")
    col.separator()

    # Walker
    col.label(text="Walker (classLabel = walker)")
    col.label(text="  0: Vehicle Get Out")
    col.label(text="  1: Vehicle Get In")
    col.label(text="  2: Stand / Idle (pilot inside)")
    col.label(text="  3: No Pilot / Idle")
    col.label(text="  4: Walk Forwards")
    col.label(text="  5: Walk Backwards")
    col.label(text="  6: Strafe Left")
    col.label(text="  7: Strafe Right")


class BZ_OT_ShowAnimIndexReference(bpy.types.Operator):
    """Show a quick reference for Battlezone animation indices"""

    bl_idname = "bz.show_anim_index_reference"
    bl_label = "Animation Index Reference"

    def invoke(self, context, event):
        context.window_manager.popup_menu(
            draw_anim_index_reference_popup,
            title="Animation Index Reference",
            icon="INFO",
        )
        return {"FINISHED"}


class BZ98TOOLS_OT_show_model_system_info(bpy.types.Operator):
    """Show how legacy VDF/SDF/GEO data relates to Redux mesh output"""

    bl_idname = "bz.show_model_system_info"
    bl_label = "Redux Model System Info"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        _draw_wrapped_lines(layout, DUAL_COMPONENT_INFO_LINES, icon="INFO", width=76)

    def execute(self, context):
        return {"FINISHED"}


class BZ98TOOLS_OT_show_advanced_vdf_info(bpy.types.Operator):
    """Show advanced VDF spinner and raw transform notes"""

    bl_idname = "bz.show_advanced_vdf_info"
    bl_label = "Advanced VDF Notes"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        _draw_wrapped_lines(layout, ADVANCED_VDF_INFO_LINES, icon="INFO", width=76)

    def execute(self, context):
        return {"FINISHED"}


class BZ98TOOLS_OT_show_pivot_dummyroot_info(bpy.types.Operator):
    """Show Battlezone pivot/origin and dummyroot authoring notes"""

    bl_idname = "bz.show_pivot_dummyroot_info"
    bl_label = "Pivot / Dummyroot Info"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        _draw_wrapped_lines(layout, PIVOT_DUMMYROOT_INFO_LINES, icon="INFO", width=76)

    def execute(self, context):
        return {"FINISHED"}


class BZ98TOOLS_OT_show_validation_checks(bpy.types.Operator):
    """Show the checks performed by Battlezone validation"""

    bl_idname = "bz.show_validation_checks"
    bl_label = "Validation Checks"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        _draw_wrapped_lines(layout, VALIDATION_CHECK_LINES, icon="CHECKMARK", width=76)

    def execute(self, context):
        return {"FINISHED"}


class BZ_PT_GeoTypeListPopover(bpy.types.Panel):
    bl_idname = "BZ_PT_GeoTypeListPopover"
    bl_label = "GEO Type Reference"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_ui_units_x = 220  # width of popover, tweak as you like

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=False)

        obj = context.object
        current = -1
        if obj is not None and hasattr(obj, "GEOPropertyGroup"):
            current = getattr(obj.GEOPropertyGroup, "GEOType", -1)

        col.label(text="ID – Description")
        col.separator()

        # geotypes is the global list populated by insertgeotypedata
        for idx, label, _ in geotypes:
            row = col.row()
            # Highlight the currently selected GEOType a bit
            icon = "RADIOBUT_ON" if idx == current else "BLANK1"
            row.label(text=label, icon=icon)


class GEOPropertyGroup(bpy.types.PropertyGroup):
    GenerateCollision: Any = bpy.props.BoolProperty(
        name="Auto Generate SDF Collision Data",
        description="Generate GEO center, projectile box, and sphere radius for SDF/building collision data. This does not create VDF inner_col/outer_col vehicle collision helpers.",
        default=True,
    )

    GeoCenterX: Any = bpy.props.FloatProperty(
        name="",
        description="Sets where the center of the GEO is. Used by projectile collision box in SDF",
        default=0.0,
        min=-50000.0,
        max=50000.0,
    )

    GeoCenterY: Any = bpy.props.FloatProperty(
        name="",
        description="Sets where the center of the GEO is. Used by projectile collision box in SDF",
        default=0.0,
        min=-50000.0,
        max=50000.0,
    )

    GeoCenterZ: Any = bpy.props.FloatProperty(
        name="",
        description="Sets where the center of the GEO is. Used by projectile collision box in SDF",
        default=0.0,
        min=-50000.0,
        max=50000.0,
    )

    SphereRadius: Any = bpy.props.FloatProperty(
        name="GEO Sphere Radius",
        description="If set to 0.0 GEO gibs don't even appear. Used in structures as well for deciding what faces you run into. If a GEO face is inside the sphere radius you will likely collide with it",
        default=3.0,
        min=0.0,
        max=50000.0,
    )

    BoxHalfHeightX: Any = bpy.props.FloatProperty(
        name="",
        description="Sets the width/length of the projectile collision box for structures",
        default=0.0,
        min=-50000.0,
        max=50000.0,
    )

    BoxHalfHeightY: Any = bpy.props.FloatProperty(
        name="",
        description="Sets the height of the projectile collision box for structures",
        default=0.0,
        min=-50000.0,
        max=50000.0,
    )

    BoxHalfHeightZ: Any = bpy.props.FloatProperty(
        name="",
        description="Sets the width/length of the projectile collision box for structures",
        default=0.0,
        min=-50000.0,
        max=50000.0,
    )

    GEOType: Any = bpy.props.IntProperty(
        name="GEO Type",
        description="What kind of GEO is this? Very important to set to what you want",
        default=60,
        min=0,
        max=100,
    )

    GEOTypeEnum: Any = bpy.props.EnumProperty(
        name="GEO Type",
        description="Named GEO type selector for common Battlezone roles",
        items=GEO_TYPE_ENUM_ITEMS,
        get=_get_geotype_enum_value,
        set=_set_geotype_enum_value,
    )

    GEOFlags: Any = bpy.props.IntProperty(
        name="Object Flags",
        description="Raw VDF/SDF ObjectFlags field from the engine ObjectType/StructObjectType record; stock assets usually leave it at 0.",
        default=0,
        min=-2147483648,
        max=2147483647,
    )

    IsSpinnerHelper: Any = bpy.props.BoolProperty(
        name="Spinner Helper",
        description="Treat this GEO as a spinner controller helper for VDF export",
        default=False,
    )

    SpinnerTarget: Any = bpy.props.StringProperty(
        name="Spinner Target",
        description="Object name this spinner helper should be written after in VDF",
        default="",
        maxlen=64,
    )

    SpinnerAxis: Any = bpy.props.FloatVectorProperty(
        name="Spinner Axis",
        description="Spinner axis vector in VDF coordinates; direction = axis, magnitude = radians/sec when speed is 1",
        size=3,
        default=(1.0, 0.0, 0.0),
    )

    SpinnerSpeed: Any = bpy.props.FloatProperty(
        name="Spinner Speed",
        description="Multiplier applied to Spinner Axis when exporting",
        default=1.0,
        min=-100000.0,
        max=100000.0,
    )

    UseRawVDFMatrix: Any = bpy.props.BoolProperty(
        name="Use Raw VDF Matrix",
        description="Write the raw 12-float VDF transform matrix directly instead of using Blender transform decomposition",
        default=False,
    )

    RawVDFMatrix: Any = bpy.props.FloatVectorProperty(
        name="Raw VDF Matrix",
        description="12 floats in order: right_x right_y right_z, up_x up_y up_z, front_x front_y front_z, pos_x pos_y pos_z",
        size=12,
        default=(
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
        ),
    )

    SDFDDR: Any = bpy.props.IntProperty(
        name="DDR / Draw Distance",
        description="Raw StructObjectType ddr field. Stock structures commonly use 500000; some mine/powerup SDFs use smaller values such as 200.",
        default=50000,
        min=0,
        max=5000000,
    )

    SDFX: Any = bpy.props.FloatProperty(
        name="Target X",
        description="Raw StructObjectType Target vector X. Used by some special structure helpers.",
        default=0,
        min=-500000.0,
        max=500000.0,
    )

    SDFY: Any = bpy.props.FloatProperty(
        name="Target Y / Spin Speed",
        description="Raw StructObjectType Target vector Y. Stock type-15 structure spinners commonly use this as spin speed, such as 0.1 or 0.2.",
        default=0,
        min=-500000.0,
        max=500000.0,
    )

    SDFZ: Any = bpy.props.FloatProperty(
        name="Target Z",
        description="Raw StructObjectType Target vector Z. Used by some special structure helpers.",
        default=0,
        min=-500000.0,
        max=500000.0,
    )

    SDFTime: Any = bpy.props.FloatProperty(
        name="Target Time / Value",
        description="Raw StructObjectType Time field. Most stock structures leave this at 0; some mine/powerup-like SDFs use nonzero values.",
        default=0.0,
        min=-500000.0,
        max=500000.0,
    )

    GEOHeaderUnknown: Any = bpy.props.IntProperty(
        name="GEO Header Checksum",
        description="Raw second GEO header int. Stock values behave like a legacy checksum/signature; preserve imported values because generated meaning is still unconfirmed.",
        default=69,
        min=-2147483648,
        max=2147483647,
    )

    GEOHeaderUnknown2: Any = bpy.props.IntProperty(
        name="GEO Header Flags",
        description="Raw trailing GEO header int. Legacy editor references treat this as render flags; stock Redux assets are normally 0.",
        default=0,
        min=-2147483648,
        max=2147483647,
    )

    GEOFaceUnknownDefault: Any = bpy.props.IntProperty(
        name="Face Reserved Raw",
        description="Default raw face reserved int used when per-face attributes are missing; stock assets normally store 0",
        default=0,
        min=-2147483648,
        max=2147483647,
    )

    GEOFaceShadeTypeDefault: Any = bpy.props.IntProperty(
        name="Face Shade Type",
        description="Default face shade byte: 4 is flat shaded, 5 is Gouraud shaded. Gouraud opaque faces commonly use bytes 05 01 00.",
        default=4,
        min=0,
        max=255,
    )

    GEOFaceTextureTypeDefault: Any = bpy.props.IntProperty(
        name="Face Texture Type",
        description="Default face texture flags byte. Stock opaque perspective faces commonly use 1 as the middle byte.",
        default=0,
        min=0,
        max=255,
    )

    GEOFaceXluscentTypeDefault: Any = bpy.props.IntProperty(
        name="Face Xluscent Type",
        description="Default face translucency byte: 0 opaque, 1 one-third translucent, 2 two-thirds translucent.",
        default=0,
        min=0,
        max=255,
    )

    GEOFaceParentDefault: Any = bpy.props.IntProperty(
        name="Face Parent",
        description="Default face parent index when per-face attributes are missing",
        default=0,
        min=-2147483648,
        max=2147483647,
    )

    GEOFaceNodeDefault: Any = bpy.props.IntProperty(
        name="Face Node",
        description="Default face node/tree-branch int when per-face attributes are missing",
        default=0,
        min=-2147483648,
        max=2147483647,
    )


class MaterialPropertyGroup(bpy.types.PropertyGroup):
    MapTexture: Any = bpy.props.StringProperty(
        name="Texture",
        description="The name of the .map texture that this material will represent. Do not include .map extension name.",
        default="",
        maxlen=8,
    )


class ValidationIssuePropertyGroup(bpy.types.PropertyGroup):
    severity: Any = bpy.props.StringProperty(name="Severity")
    scope: Any = bpy.props.StringProperty(name="Scope")
    target: Any = bpy.props.StringProperty(name="Target")
    message: Any = bpy.props.StringProperty(name="Message")
    export_modes: Any = bpy.props.StringProperty(name="Export Modes")
    object_name: Any = bpy.props.StringProperty(name="Object Name")
    action: Any = bpy.props.StringProperty(name="Action")


class ImportDiagnosticPropertyGroup(bpy.types.PropertyGroup):
    severity: Any = bpy.props.StringProperty(name="Severity")
    scope: Any = bpy.props.StringProperty(name="Scope")
    target: Any = bpy.props.StringProperty(name="Target")
    message: Any = bpy.props.StringProperty(name="Message")


def _add_import_diagnostic(scene, severity, scope, target, message):
    diagnostics = getattr(scene, "bz_import_diagnostics", None)
    if diagnostics is None:
        return
    item = diagnostics.add()
    item.severity = severity
    item.scope = scope
    item.target = target
    item.message = message


def _store_validation_results(scene, issues):
    scene.bz_validation_issues.clear()
    for issue in issues:
        item = scene.bz_validation_issues.add()
        item.severity = issue["severity"]
        item.scope = issue.get("scope", "")
        item.target = issue.get("target", "")
        item.message = issue["message"]
        item.export_modes = ",".join(sorted(issue.get("export_modes", {"ALL"})))
        item.object_name = issue.get("object_name", "")
        item.action = issue.get("action", "")
    scene.bz_validation_signature = _compute_validation_signature(scene)


def _compute_validation_signature(scene):
    digest = hashlib.sha1()
    objects = sorted(getattr(scene, "objects", []), key=lambda obj: obj.name.lower())

    for obj in objects:
        parent_name = getattr(getattr(obj, "parent", None), "name", "")
        geo_props = getattr(obj, "GEOPropertyGroup", None)
        digest.update(
            (
                f"OBJ|{obj.name}|{getattr(obj, 'type', '')}|{parent_name}|"
                f"{int(getattr(geo_props, 'GEOType', 0))}|"
                f"{int(bool(getattr(geo_props, 'IsSpinnerHelper', False)))}|"
                f"{len(getattr(getattr(obj, 'data', None), 'vertices', []))}\n"
            ).encode("utf-8", errors="ignore")
        )

        mesh = getattr(obj, "data", None)
        modifier_types = ",".join(
            getattr(modifier, "type", "") for modifier in getattr(obj, "modifiers", [])
        )
        constraint_names = ",".join(
            getattr(constraint, "name", "")
            for constraint in getattr(obj, "constraints", [])
        )
        vertex_group_count = len(getattr(obj, "vertex_groups", []))
        shape_keys = getattr(mesh, "shape_keys", None)
        shape_key_count = (
            len(getattr(shape_keys, "key_blocks", [])) if shape_keys is not None else 0
        )
        action = getattr(getattr(obj, "animation_data", None), "action", None)
        action_name = getattr(action, "name", "")
        digest.update(
            (
                f"ANIM|{obj.name}|{modifier_types}|{constraint_names}|"
                f"{vertex_group_count}|{shape_key_count}|{action_name}\n"
            ).encode("utf-8", errors="ignore")
        )

        materials = getattr(mesh, "materials", None) or []
        for slot_index, material in enumerate(materials):
            if material is None:
                digest.update(f"MAT|{obj.name}|{slot_index}|<empty>\n".encode("utf-8"))
                continue

            mat_props = getattr(material, "MaterialPropertyGroup", None)
            texture_name = (
                (getattr(mat_props, "MapTexture", "") or "").strip()
                if mat_props
                else ""
            )
            digest.update(
                (
                    f"MAT|{obj.name}|{slot_index}|{material.name}|{texture_name}|"
                    f"{getattr(material, 'diffuse_color', ())}\n"
                ).encode("utf-8", errors="ignore")
            )

    return digest.hexdigest()


def _validation_results_are_stale(scene):
    stored_signature = getattr(scene, "bz_validation_signature", "") or ""
    if not stored_signature:
        return len(getattr(scene, "bz_validation_issues", [])) > 0
    return stored_signature != _compute_validation_signature(scene)


def _get_validation_counts(scene, export_mode="ALL"):
    counts = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for item in getattr(scene, "bz_validation_issues", []):
        export_modes = {
            part.strip()
            for part in (item.export_modes or "ALL").split(",")
            if part.strip()
        }
        if "ALL" not in export_modes and export_mode not in export_modes:
            continue
        counts[item.severity] = counts.get(item.severity, 0) + 1
    return counts


def _draw_validation_summary_box(layout, scene, export_mode="ALL"):
    box = layout.box()
    box.label(text="Validation", icon="CHECKMARK")
    row = box.row(align=True)
    row.operator("bz.validate_scene", text="Validate Battlezone Scene", icon="VIEWZOOM")
    row.operator_context = "INVOKE_DEFAULT"
    row.operator("bz.show_validation_checks", text="", icon="INFO")

    issues = getattr(scene, "bz_validation_issues", None)
    if issues is None or len(issues) == 0:
        box.label(text="No validation results yet.", icon="INFO")
        return box

    counts = _get_validation_counts(scene, export_mode=export_mode)
    row = box.row(align=True)
    row.label(text=f"Errors: {counts['ERROR']}")
    row.label(text=f"Warnings: {counts['WARNING']}")
    row.label(text=f"Info: {counts['INFO']}")
    if _validation_results_are_stale(scene):
        box.label(text="Results may be stale after scene changes.", icon="ERROR")
    return box


def _draw_validation_issue_report(layout, scene, export_mode="ALL", max_items=6):
    issues = getattr(scene, "bz_validation_issues", None)
    if issues is None or len(issues) == 0:
        return

    shown = 0
    for item in issues:
        export_modes = {
            part.strip()
            for part in (item.export_modes or "ALL").split(",")
            if part.strip()
        }
        if "ALL" not in export_modes and export_mode not in export_modes:
            continue
        if shown >= max_items:
            remaining = len(issues) - shown
            layout.label(
                text=f"{remaining} more result(s) in Scene > Battlezone Export Validation."
            )
            break

        icon = "INFO"
        if item.severity == "ERROR":
            icon = "ERROR"
        elif item.severity == "WARNING":
            icon = "WARNING"

        row = layout.row(align=True)
        target = item.scope
        if item.target:
            target += f": {item.target}"
        row.label(text=f"{item.severity} {target}", icon=icon)
        if item.object_name:
            op = row.operator(
                "bz.select_validation_target", text="", icon="RESTRICT_SELECT_OFF"
            )
            op.object_name = item.object_name
        if item.action == "fix_name" and item.object_name:
            op = row.operator("bz.fix_validation_name", text="", icon="SORTALPHA")
            op.object_name = item.object_name
        _draw_wrapped_label(layout, item.message, width=58, scale_y=0.8)
        shown += 1


def _draw_legacy_export_mode_box(layout, auto_port_enabled):
    box = layout.box()
    box.label(text="Output Mode", icon="EXPORT")
    if auto_port_enabled:
        box.label(text="Mode: Legacy + Redux", icon="CHECKMARK")
        box.label(
            text="Writes legacy files first, then generates Redux mesh-side files."
        )
    else:
        box.label(text="Mode: Legacy only", icon="FILE")
        box.label(text="Writes only the selected legacy format.")
    return box


def _draw_redux_only_export_mode_box(layout):
    box = layout.box()
    box.label(text="Output Mode", icon="EXPORT")
    box.label(text="Mode: Redux only", icon="MESH_DATA")
    box.label(
        text="Writes Redux mesh-side files without creating GEO, VDF, or SDF files."
    )
    return box


def _draw_legacy_animation_limits_box(layout):
    box = layout.box()
    box.label(text="Legacy Animation Limits", icon="INFO")
    _draw_wrapped_label(
        box, "VDF/SDF ANIM exports object rotation and location only.", width=62
    )
    _draw_wrapped_label(
        box,
        "Skeletal animation, skin weights, shape keys, and mesh bending are not written.",
        width=62,
    )
    _draw_wrapped_label(
        box, "Each moving legacy part must be a separate GEO object.", width=62
    )
    return box


def _draw_orientation_reference_box(layout, mode="LEGACY", auto_port_enabled=False):
    box = layout.box()
    box.label(text="Orientation Reference", icon="EMPTY_ARROWS")
    if mode == "REDUX":
        box.label(text=REDUX_MESH_FORWARD_LABEL, icon="MESH_DATA")
        box.label(text="Use this for direct Redux mesh imports and exports.")
    else:
        box.label(text=LEGACY_VEHICLE_FORWARD_LABEL, icon="OBJECT_DATA")
        box.label(text="Use this when authoring vehicles for legacy VDF export.")
        if auto_port_enabled:
            box.label(text=AUTO_PORT_FORWARD_NOTE, icon="INFO")
    return box


def _draw_shared_autoport_options(layout, operator):
    layout.prop(operator, "ogre_name")
    layout.prop(operator, "ogre_suffix")
    layout.prop(operator, "ogre_flat_colors")
    layout.prop(operator, "ogre_normal_mode")
    layout.prop(operator, "ogre_bounds_mult")
    layout.prop(operator, "ogre_act_path")
    layout.prop(operator, "ogre_config_path")
    layout.prop(operator, "ogre_dest_dir")

    adv = layout.box()
    adv.label(text="Advanced")
    adv.prop(operator, "ogre_only_once")
    adv.prop(operator, "ogre_skip_unchanged")
    adv.prop(operator, "ogre_profile_export")
    adv.prop(operator, "ogre_nowrite")


def _draw_vdf_autoport_options(layout, operator, scene=None):
    box = layout.box()
    box.label(text="VDF-Specific Redux Options")
    box.prop(operator, "ogre_headlights")
    if (
        operator.ogre_headlights
        and scene is not None
        and not _scene_has_headlight_geo(scene)
    ):
        _draw_wrapped_label(
            box,
            "Headlights are enabled, but no GEO Type 38 HEADLIGHT_MASK object was found.",
            icon="WARNING",
            width=62,
        )
    box.prop(operator, "ogre_person_mode")
    box.prop(operator, "ogre_turret_mode")
    box.prop(operator, "ogre_cockpit_mode")
    box.prop(operator, "ogre_skeletalanims_mode")
    box.prop(operator, "ogre_scope_mode")
    box.prop(operator, "ogre_scope_type")
    box.prop(operator, "ogre_scope_nation")
    box.prop(operator, "ogre_scope_screen")
    box.prop(operator, "ogre_scope_gun")
    box.prop(operator, "ogre_scope_transform")
    box.prop(operator, "ogre_scope_texture")
    box.prop(operator, "ogre_no_pov_rots")
    box.prop(operator, "ogre_stabilize_walker_cockpit")


def _draw_xyz_row(layout, prop_group, prop_names, labels):
    row = layout.row(align=True)
    for prop_name, label in zip(prop_names, labels):
        row.prop(prop_group, prop_name, text=label)


def _derive_legacy_texture_name(name):
    derived = os.path.splitext((name or "").strip())[0].replace(" ", "_").lower()
    return derived[:8]


def _get_material_image_name(material):
    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return ""

    image_nodes = [
        node
        for node in getattr(node_tree, "nodes", [])
        if getattr(node, "type", "") == "TEX_IMAGE"
        and getattr(node, "image", None) is not None
    ]
    if not image_nodes:
        return ""

    active_node = next(
        (node for node in image_nodes if getattr(node, "select", False)), None
    )
    if active_node is None:
        active_node = image_nodes[0]

    image = active_node.image
    return _derive_legacy_texture_name(
        getattr(image, "name", "") or getattr(image, "filepath", "")
    )


def _get_material_texture_preview(material):
    material_props = getattr(material, "MaterialPropertyGroup", None)
    explicit_name = ""
    if material_props is not None:
        explicit_name = (getattr(material_props, "MapTexture", "") or "").strip()

    image_name = _get_material_image_name(material)
    derived_name = _derive_legacy_texture_name(getattr(material, "name", ""))
    resolved_name = explicit_name or image_name or derived_name

    if explicit_name:
        source = "explicit"
    elif image_name:
        source = "image"
    elif derived_name:
        source = "derived"
    else:
        source = "missing"

    return explicit_name, image_name, derived_name, resolved_name, source


def _get_animation_index_hint(index_value):
    index_value = int(index_value)
    if index_value == 0:
        return "Slot 0 is commonly the primary action: deploy, launch, get-out, or stance change depending on classLabel."
    if index_value == 1:
        return "Slot 1 is commonly the reverse action: undeploy, return, get-in, or reverse stance change."
    if index_value == 2:
        return "Slot 2 is often an idle or standing idle state on units that support multiple animation slots."
    if index_value == 3:
        return "Slot 3 is often a deployed idle or alternate idle state."
    if index_value == 4:
        return "Slot 4 is often an active deployed state or forward movement on walkers/persons."
    if index_value in (5, 6, 7):
        return "Slots 5-7 are commonly movement directions on walkers and person units."
    if index_value in (8, 9, 10, 11):
        return "Slots 8-11 are commonly situational character animations such as death, parachute, or jump states."
    return "Animation index meaning is classLabel-dependent. Use the reference popup to confirm the slot for this unit type."


def _copy_animation_item(source_item, dest_item):
    dest_item.Index = source_item.Index
    dest_item.Start = source_item.Start
    dest_item.Length = source_item.Length
    dest_item.Loop = source_item.Loop
    dest_item.Speed = source_item.Speed
    dest_item.UseCustomUnknownGeoMask = source_item.UseCustomUnknownGeoMask
    dest_item.UnknownGeoMask = tuple(source_item.UnknownGeoMask)


def _axis_mirror_matrix(axis):
    values = [1.0, 1.0, 1.0, 1.0]
    axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 0)
    values[axis_index] = -1.0
    return mathutils.Matrix.Diagonal(values)


def _iter_action_fcurve_collections(action):
    if action is None:
        return

    fcurves = getattr(action, "fcurves", None)
    if fcurves is not None:
        yield fcurves
        return

    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for channelbag in getattr(strip, "channelbags", []):
                yield getattr(channelbag, "fcurves", [])


def _legacy_transform_fcurves(action, include_scale=False):
    curves = []

    allowed_paths = set(LEGACY_TRANSFORM_DATA_PATHS)
    if not include_scale:
        allowed_paths.discard("scale")

    for fcurves in _iter_action_fcurve_collections(action):
        curves.extend(
            curve
            for curve in fcurves
            if getattr(curve, "data_path", "") in allowed_paths
        )
    return curves


def _legacy_action_keyframes(
    action, frame_start=None, frame_end=None, include_scale=False
):
    frames = set()
    for curve in _legacy_transform_fcurves(action, include_scale=include_scale):
        for keyframe in curve.keyframe_points:
            frame = float(keyframe.co.x)
            if frame_start is not None and frame < frame_start:
                continue
            if frame_end is not None and frame > frame_end:
                continue
            frames.add(frame)
    return sorted(frames)


def _replace_name_token(name, source_token, target_token):
    if not source_token:
        return ""

    if source_token in name:
        return name.replace(source_token, target_token, 1)

    lower_name = name.lower()
    lower_source = source_token.lower()
    index = lower_name.find(lower_source)
    if index < 0:
        return ""
    return name[:index] + target_token + name[index + len(source_token) :]


def _expand_object_descendants(objects):
    expanded = []
    seen = set()

    def add_object(obj):
        if obj is None or obj.name in seen:
            return
        seen.add(obj.name)
        expanded.append(obj)
        for child in getattr(obj, "children", []):
            add_object(child)

    for obj in objects:
        add_object(obj)
    return expanded


def _delete_action_keys_in_range(action, data_paths, frame_start, frame_end):
    if action is None:
        return

    for fcurves in _iter_action_fcurve_collections(action):
        for curve in list(fcurves):
            if curve.data_path not in data_paths:
                continue
            for keyframe in reversed(curve.keyframe_points):
                frame = float(keyframe.co.x)
                if frame_start <= frame <= frame_end:
                    curve.keyframe_points.remove(keyframe, fast=True)
            curve.update()
            if len(curve.keyframe_points) == 0:
                fcurves.remove(curve)


def _source_key_interpolation(action, frame, include_scale=False):
    for curve in _legacy_transform_fcurves(action, include_scale=include_scale):
        for keyframe in curve.keyframe_points:
            if abs(float(keyframe.co.x) - float(frame)) < 0.0001:
                return keyframe.interpolation
    return None


def _set_inserted_key_interpolation(action, data_paths, frame, interpolation):
    if action is None or interpolation is None:
        return

    for fcurves in _iter_action_fcurve_collections(action):
        for curve in fcurves:
            if curve.data_path not in data_paths:
                continue
            for keyframe in curve.keyframe_points:
                if abs(float(keyframe.co.x) - float(frame)) < 0.0001:
                    keyframe.interpolation = interpolation
            curve.update()


def _ensure_object_action(obj, suffix="_mirrored"):
    obj.animation_data_create()
    if obj.animation_data.action is None:
        obj.animation_data.action = bpy.data.actions.new(f"{obj.name}{suffix}")
    return obj.animation_data.action


def _legacy_mirror_similarity_warning(source, target):
    if source is None or target is None:
        return ""
    if (
        getattr(source, "type", None) != "MESH"
        or getattr(target, "type", None) != "MESH"
    ):
        return "Mirror selections are not both mesh GEO objects; transform keys can still be copied."

    source_mesh = getattr(source, "data", None)
    target_mesh = getattr(target, "data", None)
    source_vertices = len(getattr(source_mesh, "vertices", []))
    target_vertices = len(getattr(target_mesh, "vertices", []))
    source_edges = len(getattr(source_mesh, "edges", []))
    target_edges = len(getattr(target_mesh, "edges", []))
    source_faces = len(getattr(source_mesh, "polygons", []))
    target_faces = len(getattr(target_mesh, "polygons", []))

    if (
        source_vertices == target_vertices
        and source_edges == target_edges
        and source_faces == target_faces
    ):
        return ""

    source_dims = getattr(source, "dimensions", mathutils.Vector((0.0, 0.0, 0.0)))
    target_dims = getattr(target, "dimensions", mathutils.Vector((0.0, 0.0, 0.0)))
    max_dim = max(max(source_dims), max(target_dims), 0.000001)
    dim_delta = (source_dims - target_dims).length / max_dim
    vertex_delta = abs(source_vertices - target_vertices) / max(
        source_vertices, target_vertices, 1
    )
    if dim_delta <= 0.1 and vertex_delta <= 0.1:
        return (
            "Mirror selections are similar but not identical "
            f"({source_vertices}/{target_vertices} vertices, {source_faces}/{target_faces} faces)."
        )

    return (
        "Mirror selections do not look like matching GEO parts "
        f"({source_vertices}/{target_vertices} vertices, {source_faces}/{target_faces} faces)."
    )


def _set_scene_frame_float(scene, frame):
    frame_value = float(frame)
    frame_base = math.floor(frame_value)
    scene.frame_set(frame_base, subframe=frame_value - frame_base)


def _get_active_export_operator(context, export_kind):
    expected_idname = EXPORT_KIND_IDNAMES.get(export_kind)
    space = getattr(context, "space_data", None)
    operator = getattr(space, "active_operator", None) if space is not None else None
    if operator is None:
        return None
    if getattr(operator, "bl_idname", "") != expected_idname:
        return None
    return operator


def _normalize_preset_filename(name):
    cleaned = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (name or "").strip()
    )
    cleaned = cleaned.strip("._")
    return cleaned or "preset"


def _serialize_export_operator(operator, export_kind):
    values = {}
    for prop_name in EXPORT_PRESET_PROPERTY_NAMES.get(export_kind, ()):
        if not hasattr(operator, prop_name):
            continue
        value = getattr(operator, prop_name)
        if isinstance(value, (list, tuple)):
            values[prop_name] = list(value)
        else:
            values[prop_name] = value
    return values


def _apply_export_preset_values(operator, export_kind, values):
    for prop_name in EXPORT_PRESET_PROPERTY_NAMES.get(export_kind, ()):
        if prop_name not in values or not hasattr(operator, prop_name):
            continue
        value = values[prop_name]
        current = getattr(operator, prop_name)
        if isinstance(current, (list, tuple)):
            setattr(operator, prop_name, tuple(value))
        else:
            setattr(operator, prop_name, value)


def _get_builtin_export_preset(export_kind, preset_key):
    for key, _label, values in BUILTIN_EXPORT_PRESETS.get(export_kind, ()):
        if key == preset_key:
            return values
    return None


def _list_custom_export_presets(export_kind):
    preset_dir = get_export_preset_dir(export_kind)
    if not os.path.isdir(preset_dir):
        return []

    results = []
    for entry in sorted(os.listdir(preset_dir)):
        if not entry.lower().endswith(".json"):
            continue
        path = os.path.join(preset_dir, entry)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        label = (payload.get("label") or os.path.splitext(entry)[0]).strip()
        results.append(
            {
                "label": label,
                "path": path,
            }
        )
    return results


def _load_custom_export_preset(export_kind, preset_label):
    for entry in _list_custom_export_presets(export_kind):
        if entry["label"] != preset_label:
            continue
        try:
            with open(entry["path"], "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return payload.get("values", {})
    return None


def _draw_export_preset_box(layout, export_kind):
    box = layout.box()
    box.label(text="Presets")
    if export_kind == "GEO":
        box.menu("BZ98TOOLS_MT_geo_export_presets", text="Apply Preset")
        box.menu("BZ98TOOLS_MT_geo_delete_export_preset", text="Delete Saved Preset")
    elif export_kind == "VDF":
        box.menu("BZ98TOOLS_MT_vdf_export_presets", text="Apply Preset")
        box.menu("BZ98TOOLS_MT_vdf_delete_export_preset", text="Delete Saved Preset")
    else:
        box.menu("BZ98TOOLS_MT_sdf_export_presets", text="Apply Preset")
        box.menu("BZ98TOOLS_MT_sdf_delete_export_preset", text="Delete Saved Preset")

    save_op = box.operator("bz.save_export_preset", text="Save Current Settings")
    save_op.export_kind = export_kind
    return box


def _draw_export_preset_menu_entries(layout, export_kind, delete_mode=False):
    if not delete_mode:
        for preset_key, label, _values in BUILTIN_EXPORT_PRESETS.get(export_kind, ()):
            op = layout.operator("bz.apply_export_preset", text=label, icon="IMPORT")
            op.export_kind = export_kind
            op.preset_key = preset_key
            op.custom_label = ""

        custom_presets = _list_custom_export_presets(export_kind)
        if custom_presets:
            layout.separator()
            layout.label(text="Saved Presets")
            for entry in custom_presets:
                op = layout.operator(
                    "bz.apply_export_preset", text=entry["label"], icon="FILE"
                )
                op.export_kind = export_kind
                op.preset_key = ""
                op.custom_label = entry["label"]
        else:
            layout.separator()
            layout.label(text="No saved presets yet.", icon="INFO")
        return

    custom_presets = _list_custom_export_presets(export_kind)
    if not custom_presets:
        layout.label(text="No saved presets yet.", icon="INFO")
        return

    for entry in custom_presets:
        op = layout.operator(
            "bz.delete_export_preset", text=entry["label"], icon="TRASH"
        )
        op.export_kind = export_kind
        op.custom_label = entry["label"]


"""
PANEL DEFINITIONS
BattlezoneSDFVDFProperties - Stores all the properties of the current SDF/VDF in the scene tab.
BattlezoneGEOProperties - Stores all the properties of a GEO object in the object tab of objects.
"""


class BattlezoneSDFVDFProperties(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_SDFVDF"
    bl_label = "Battlezone"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        SDFVDFPropertyGroup = scene.SDFVDFPropertyGroup

        layout.prop(SDFVDFPropertyGroup, "Name", text="Internal Name")
        row = layout.row(align=True)
        row.label(text=f"Animations: {len(scene.AnimationCollection)}", icon="ANIM")
        row.label(
            text=f"Validation: {len(getattr(scene, 'bz_validation_issues', []))}",
            icon="CHECKMARK",
        )
        info_row = layout.row()
        info_row.operator_context = "INVOKE_DEFAULT"
        info_row.operator(
            "bz.show_model_system_info", text="Redux Model System Info", icon="INFO"
        )


class BZ98TOOLS_PT_scene_asset_properties(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_ASSET_PROPERTIES"
    bl_label = "Asset Properties"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"

    def draw(self, context):
        layout = self.layout
        props = context.scene.SDFVDFPropertyGroup

        vdf_box = layout.box()
        vdf_box.label(text="VDF Settings")
        vdf_box.prop(props, "VehicleSize")
        vdf_box.prop(props, "VehicleType")
        vdf_box.prop(props, "Mass")
        vdf_box.prop(props, "CollMult", text="Collision Multiplier")
        vdf_box.prop(props, "DragCoefficient")

        sdf_box = layout.box()
        sdf_box.label(text="SDF Settings")
        sdf_box.prop(props, "StructureType")
        sdf_box.prop(props, "Defensive")
        sdf_box.prop(props, "DeathExplosion", text="Death Explosion")
        sdf_box.prop(props, "DeathSound", text="Death Sound")

        lod_box = layout.box()
        lod_box.label(text="Level of Detail")
        lod_box.prop(props, "LOD1")
        lod_box.prop(props, "LOD2")
        lod_box.prop(props, "LOD3")
        lod_box.prop(props, "LOD4")
        lod_box.prop(props, "LOD5")


class BZ98TOOLS_PT_scene_orientation_reference(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_ORIENTATION_REFERENCE"
    bl_label = "Orientation Reference"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"

    def draw(self, context):
        layout = self.layout
        _draw_orientation_reference_box(layout, mode="LEGACY")
        info_row = layout.row()
        info_row.operator_context = "INVOKE_DEFAULT"
        info_row.operator(
            "bz.show_pivot_dummyroot_info", text="Pivot / Dummyroot Info", icon="INFO"
        )

        redux = layout.box()
        redux.label(text="Redux Direct Mesh", icon="MESH_DATA")
        redux.label(text=REDUX_MESH_FORWARD_LABEL)
        redux.label(
            text="Legacy + Redux auto-port handles this conversion for VDF/SDF/GEO exports."
        )


class BZ98TOOLS_PT_scene_collision_helpers(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_COLLISION_HELPERS"
    bl_label = "Collision Helpers"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="VDF COL Helpers")
        box.label(text="Vehicles only.")
        box.label(text="Uses selected meshes.")
        box.operator(
            "bz.generate_vdf_collision_meshes",
            text="Generate COL Boxes",
        )


class BZ98TOOLS_PT_scene_advanced(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_ADVANCED"
    bl_label = "Animation Advanced"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.SDFVDFPropertyGroup

        layout.label(text="ANIM Header / Track Overrides")
        layout.prop(props, "UseAdvancedAnimHeader")
        if props.UseAdvancedAnimHeader:
            layout.prop(props, "AnimNull2")
            layout.prop(props, "AnimUnknown2")
            layout.prop(props, "AnimReserved")
        layout.prop(props, "UseTranslation2Track")


class BZ98TOOLS_PT_scene_vdf_raw(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_VDF_RAW"
    bl_label = "VDF Raw Data"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.SDFVDFPropertyGroup

        layout.label(text="SPCS/SCPS Raw Override")
        layout.prop(props, "UseCustomSCPS")
        if props.UseCustomSCPS:
            layout.prop(props, "SCPSData")


class BZ98TOOLS_OT_validate_scene(bpy.types.Operator):
    """Validate scene objects for legacy Battlezone export"""

    bl_idname = "bz.validate_scene"
    bl_label = "Validate Battlezone Scene"

    preset: Any = EnumProperty(
        name="Preset",
        description="Validation focus for animation and vehicle workflow checks",
        items=VALIDATION_PRESET_ITEMS,
        default="AUTO",
    )

    def execute(self, context):
        issues = bz_validation.collect_legacy_validation_issues(
            context,
            export_mode="ALL",
            validation_preset=self.preset,
        )
        _store_validation_results(context.scene, issues)

        counts = _get_validation_counts(context.scene, export_mode="ALL")
        if counts["ERROR"] > 0:
            self.report(
                {"WARNING"},
                f"Validation found {counts['ERROR']} errors, {counts['WARNING']} warnings, and {counts['INFO']} info items.",
            )
        elif counts["WARNING"] > 0 or counts["INFO"] > 0:
            self.report(
                {"INFO"},
                f"Validation found {counts['WARNING']} warnings and {counts['INFO']} info items.",
            )
        else:
            self.report({"INFO"}, "Validation found no legacy export issues.")
        return {"FINISHED"}


class BZ98TOOLS_OT_select_validation_target(bpy.types.Operator):
    bl_idname = "bz.select_validation_target"
    bl_label = "Select Validation Target"
    bl_description = (
        "Select and activate the object referenced by this validation issue"
    )

    object_name: Any = StringProperty(name="Object Name", default="")

    def execute(self, context):
        if not self.object_name:
            self.report({"ERROR"}, "Validation issue has no selectable object.")
            return {"CANCELLED"}

        obj = context.scene.objects.get(self.object_name)
        if obj is None:
            self.report({"ERROR"}, f"Could not find object '{self.object_name}'.")
            return {"CANCELLED"}

        for selected in list(context.selected_objects):
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        self.report({"INFO"}, f"Selected '{obj.name}'.")
        return {"FINISHED"}


class BZ98TOOLS_OT_fix_validation_name(bpy.types.Operator):
    bl_idname = "bz.fix_validation_name"
    bl_label = "Fix Legacy Name"
    bl_description = "Rename this object to a valid legacy Battlezone export name"

    object_name: Any = StringProperty(name="Object Name", default="")

    def execute(self, context):
        if not self.object_name:
            self.report({"ERROR"}, "Validation issue has no rename target.")
            return {"CANCELLED"}

        obj = context.scene.objects.get(self.object_name)
        if obj is None:
            self.report({"ERROR"}, f"Could not find object '{self.object_name}'.")
            return {"CANCELLED"}

        original_name = obj.name
        source_name = "".join(
            ch
            for ch in original_name.lower().replace(" ", "_")
            if ch.isalnum() or ch == "_"
        )
        if not source_name:
            source_name = "geo"

        lod = 3
        if len(original_name) >= 4 and original_name[3] in {"1", "2", "3"}:
            lod = int(original_name[3])

        min_len = max(5, min(8, len(source_name)))
        chars = list(source_name[:8].ljust(min_len, "x"))
        chars[3] = str(lod)
        chars[4] = "1"
        candidate = "".join(chars[:8])

        existing = {
            scene_obj.name.lower()
            for scene_obj in context.scene.objects
            if scene_obj != obj
        }
        if candidate.lower() in existing:
            base = candidate[:7]
            for suffix in "0123456789abcdefghijklmnopqrstuvwxyz":
                alt = (base + suffix)[:8]
                if alt.lower() not in existing:
                    candidate = alt
                    break

        obj.name = candidate
        self.report({"INFO"}, f"Renamed '{original_name}' to '{candidate}'.")
        return {"FINISHED"}


class BZ98TOOLS_OT_select_by_object_type(bpy.types.Operator):
    bl_idname = "bz.select_by_object_type"
    bl_label = "Select Battlezone Objects"
    bl_description = "Select all mesh or non-mesh objects in the active scene"
    bl_options = {"REGISTER", "UNDO"}

    selection_mode: Any = EnumProperty(
        name="Selection Mode",
        items=(
            ("MESH", "Mesh Objects", "Select only mesh objects"),
            (
                "NON_MESH",
                "Non-Mesh Objects",
                "Select empties, armatures, lights, cameras, and other non-mesh objects",
            ),
        ),
        default="MESH",
    )

    def execute(self, context):
        try:
            if getattr(context, "mode", "OBJECT") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

        for obj in list(context.selected_objects):
            obj.select_set(False)

        selected = []
        for obj in context.scene.objects:
            is_mesh = getattr(obj, "type", None) == "MESH"
            if (self.selection_mode == "MESH" and is_mesh) or (
                self.selection_mode == "NON_MESH" and not is_mesh
            ):
                try:
                    obj.select_set(True)
                    selected.append(obj)
                except Exception:
                    continue

        if selected:
            context.view_layer.objects.active = selected[0]

        label = "mesh" if self.selection_mode == "MESH" else "non-mesh"
        self.report({"INFO"}, f"Selected {len(selected)} {label} object(s).")
        return {"FINISHED"}


class BZ98TOOLS_OT_quick_normals_fix(bpy.types.Operator):
    bl_idname = "bz.quick_normals_fix"
    bl_label = "Quick Normals Fix"
    bl_description = "Recalculate selected mesh normals for common Ogre import cleanup"
    bl_options = {"REGISTER", "UNDO"}

    normal_mode: Any = EnumProperty(
        name="Mode",
        items=(
            (
                "OUTSIDE",
                "Recalculate Outside",
                "Recalculate selected mesh face winding/normals outward",
            ),
            (
                "FACE_NORMALS",
                "Generate From Faces",
                "Recalculate outward and switch polygons to flat face normals",
            ),
        ),
        default="OUTSIDE",
    )

    @classmethod
    def poll(cls, context):
        return any(
            getattr(obj, "type", None) == "MESH"
            for obj in getattr(context, "selected_objects", [])
        )

    def execute(self, context):
        import bmesh

        active = context.view_layer.objects.active
        previous_mode = getattr(context, "mode", "OBJECT")
        meshes = [
            obj
            for obj in context.selected_objects
            if getattr(obj, "type", None) == "MESH"
        ]
        if not meshes:
            self.report({"ERROR"}, "No mesh objects selected.")
            return {"CANCELLED"}

        try:
            if previous_mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

        fixed = 0
        for obj in meshes:
            mesh = getattr(obj, "data", None)
            if mesh is None:
                continue

            bm = bmesh.new()
            bm.from_mesh(mesh)
            bm.faces.ensure_lookup_table()
            if bm.faces:
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            bm.to_mesh(mesh)
            bm.free()

            if self.normal_mode == "FACE_NORMALS":
                for poly in mesh.polygons:
                    poly.use_smooth = False

            try:
                mesh.normals_split_custom_set([(0.0, 0.0, 0.0)] * len(mesh.loops))
            except Exception:
                pass
            mesh.update()
            fixed += 1

        if active is not None:
            context.view_layer.objects.active = active
            if previous_mode != "OBJECT" and getattr(active, "type", None) == "MESH":
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except Exception:
                    pass

        mode_label = "from faces" if self.normal_mode == "FACE_NORMALS" else "outside"
        self.report({"INFO"}, f"Fixed normals {mode_label} on {fixed} mesh object(s).")
        return {"FINISHED"}


class BZ98TOOLS_OT_create_organic_redux_skin(bpy.types.Operator):
    bl_idname = "bz.create_organic_redux_skin"
    bl_label = "Create Organic Redux Skin From VDF Hierarchy"
    bl_description = "Create a Redux armature and starter mesh weights from legacy GEO control pivots"
    bl_options = {"REGISTER", "UNDO"}

    control_source: Any = EnumProperty(
        name="Control Source",
        items=(
            (
                "SELECTED",
                "Selected GEO Controls",
                "Use selected valid GEO controls other than the active target mesh",
            ),
            (
                "HIERARCHY",
                "Active GEO Hierarchy",
                "Use the active mesh's nearest valid GEO ancestor and its valid GEO descendants",
            ),
        ),
        default="SELECTED",
    )

    keep_controls_visible: Any = BoolProperty(
        name="Keep GEO Controls Visible",
        description="Leave legacy GEO control objects visible after creating the Redux rig",
        default=False,
    )

    weight_mode: Any = EnumProperty(
        name="Weight Mode",
        items=(
            (
                "BLENDS",
                "Nearest Controls With Joint Blends",
                "Create starter blended weights near parent/child control joints",
            ),
            (
                "NEAREST",
                "Nearest Single Control",
                "Assign each vertex fully to its nearest control pivot",
            ),
        ),
        default="BLENDS",
    )

    blend_radius: Any = FloatProperty(
        name="Blend Radius",
        description="Distance around parent/child control segments where starter blend weights are added",
        default=0.35,
        min=0.0,
        soft_max=10.0,
    )

    max_influences: Any = IntProperty(
        name="Max Influences",
        description="Maximum weighted bones per vertex; Redux native export keeps at most three",
        default=3,
        min=1,
        max=3,
    )

    replace_existing: Any = BoolProperty(
        name="Replace Existing Rig Data",
        description="Clear matching generated vertex groups and the generated armature modifier before rebinding",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return getattr(obj, "type", None) == "MESH"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        target_obj = context.view_layer.objects.active
        if getattr(target_obj, "type", None) != "MESH":
            self.report(
                {"ERROR"}, "Active object must be the continuous Redux mesh target."
            )
            return {"CANCELLED"}

        try:
            if getattr(context, "mode", "OBJECT") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

        try:
            from . import organic_redux_skin

            armature_obj, controls = organic_redux_skin.create_organic_redux_skin(
                context,
                target_obj,
                control_source=self.control_source,
                keep_controls_visible=self.keep_controls_visible,
                weight_mode=self.weight_mode,
                blend_radius=self.blend_radius,
                max_influences=self.max_influences,
                replace_existing=self.replace_existing,
            )
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        for obj in list(context.selected_objects):
            obj.select_set(False)
        target_obj.select_set(True)
        armature_obj.select_set(True)
        context.view_layer.objects.active = armature_obj

        self.report(
            {"INFO"},
            f"Created '{armature_obj.name}' with {len(controls)} control bone(s) for '{target_obj.name}'.",
        )
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "control_source")
        layout.prop(self, "keep_controls_visible")
        layout.separator()
        layout.prop(self, "weight_mode")
        if self.weight_mode == "BLENDS":
            layout.prop(self, "blend_radius")
        layout.prop(self, "max_influences")
        layout.prop(self, "replace_existing")


class BZ98TOOLS_OT_create_vehicle_geo_set(bpy.types.Operator):
    bl_idname = "bz.create_vehicle_geo_set"
    bl_label = "Create Vehicle GEO Set"
    bl_description = "Create a movable starter set of tiny standard vehicle GEO cubes"
    bl_options = {"REGISTER", "UNDO"}

    base_prefix: Any = StringProperty(
        name="3-Character Prefix",
        description="First three characters for generated legacy GEO names",
        default="",
        maxlen=3,
    )

    parent_to_body: Any = BoolProperty(
        name="Parent To Body",
        description="Parent generated helper GEOs to the body cube",
        default=True,
    )

    select_created: Any = BoolProperty(
        name="Select Created",
        description="Select all generated GEO cubes when complete",
        default=True,
    )

    def execute(self, context):
        prefix = _find_available_quick_geo_prefix(
            self.base_prefix or _quick_geo_prefix_from_scene(context)
        )
        if not prefix:
            self.report({"ERROR"}, "Could not find an unused 3-character GEO prefix.")
            return {"CANCELLED"}

        created = []
        body = None
        for suffix, geo_type, _label, location, size in QUICK_VEHICLE_GEO_SPECS:
            name = f"{prefix}11{suffix}"
            mesh = _create_box_mesh(name, size)
            obj = bpy.data.objects.new(name, mesh)
            context.collection.objects.link(obj)
            obj.location = location
            obj.display_type = "WIRE"

            geo = getattr(obj, "GEOPropertyGroup", None)
            if geo is not None:
                geo.GEOType = geo_type
                geo.GenerateCollision = geo_type == 60

            if suffix == "bod":
                body = obj
            elif self.parent_to_body and body is not None:
                obj.parent = body

            created.append(obj)

        if self.select_created:
            for obj in list(context.selected_objects):
                obj.select_set(False)
            for obj in created:
                obj.select_set(True)
            context.view_layer.objects.active = body or created[0]

        self.report(
            {"INFO"},
            f"Created {len(created)} starter vehicle GEOs with prefix '{prefix}'.",
        )
        return {"FINISHED"}


class BZ98TOOLS_PT_validation(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_VALIDATION"
    bl_label = "Battlezone Export Validation"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        _draw_validation_summary_box(layout, scene, export_mode="ALL")

        issues = getattr(scene, "bz_validation_issues", None)
        if issues is None or len(issues) == 0:
            return

        list_box = layout.box()
        list_box.label(text="Latest Results")
        if _validation_results_are_stale(scene):
            list_box.label(
                text="Scene data changed after the last validation run.", icon="ERROR"
            )
        shown = 0
        for item in issues:
            if shown >= 20:
                list_box.label(
                    text="More results are available after the first 20 entries."
                )
                break

            if item.severity == "ERROR":
                icon = "ERROR"
            elif item.severity == "WARNING":
                icon = "WARNING"
            else:
                icon = "INFO"

            row = list_box.row()
            label = f"{item.scope}"
            if item.target:
                label += f": {item.target}"
            row.label(text=label, icon=icon)
            if item.object_name:
                op = row.operator(
                    "bz.select_validation_target",
                    text="Select",
                    icon="RESTRICT_SELECT_OFF",
                )
                op.object_name = item.object_name
            if item.action == "fix_name" and item.object_name:
                op = row.operator(
                    "bz.fix_validation_name", text="Fix Name", icon="SORTALPHA"
                )
                op.object_name = item.object_name

            _draw_wrapped_label(list_box, item.message, width=72, scale_y=0.85)
            shown += 1


class BZ98TOOLS_PT_import_diagnostics(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_IMPORT_DIAGNOSTICS"
    bl_label = "File Diagnostics"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        diagnostics = getattr(context.scene, "bz_import_diagnostics", None)
        if diagnostics is None or len(diagnostics) == 0:
            layout.label(text="No import diagnostics yet.", icon="INFO")
            return

        shown = 0
        for item in diagnostics:
            if shown >= 30:
                layout.label(text="More diagnostics are available after the first 30.")
                break

            if item.severity == "ERROR":
                icon = "ERROR"
            elif item.severity == "WARNING":
                icon = "WARNING"
            else:
                icon = "INFO"

            box = layout.box()
            title = item.scope
            if item.target:
                title += f": {item.target}"
            box.label(text=title, icon=icon)
            box.label(text=item.message)
            shown += 1


class BattlezoneGEOProperties(bpy.types.Panel):
    bl_idname = "OBJECT_PT_BZ_GEO"
    bl_label = "Battlezone GEO"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    def draw(self, context):
        layout = self.layout
        obj = context.object

        geo = getattr(obj, "GEOPropertyGroup", None)
        if geo is None:
            layout.label(text="No GEO data on active object.")
            return

        layout.prop(geo, "GEOTypeEnum", text="GEO Type")
        layout.prop(geo, "GEOFlags")

        row = layout.row()
        row.popover(
            panel="BZ_PT_GeoTypeListPopover",
            text="Show GEO Types",
            icon="INFO",
        )
        _draw_wrapped_label(
            layout, f"Selected Role: {_get_geotype_label(geo.GEOType)}", width=58
        )
        _draw_lod_context_notes(layout, obj)
        _draw_geotype_hint(layout, geo.GEOType)
        _draw_legacy_geo_naming_note(layout)
        if getattr(obj, "type", None) == "MESH":
            layout.label(text=f"Vertices: {len(obj.data.vertices)}", icon="MESH_DATA")
        else:
            layout.label(
                text="Non-mesh object. Advanced helper workflow only.", icon="INFO"
            )


class BZ98TOOLS_PT_view3d_selected_geo(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_SELECTED_GEO"
    bl_label = "Selected GEO"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"

    @classmethod
    def poll(cls, context):
        return getattr(context, "object", None) is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object
        geo = getattr(obj, "GEOPropertyGroup", None)
        if geo is None:
            layout.label(text="No GEO data on active object.", icon="INFO")
            return

        header = layout.box()
        header.label(text=obj.name, icon="OBJECT_DATA")
        row = header.row(align=True)
        row.scale_y = 1.4
        row.label(text=f"GEO Type {int(geo.GEOType)}", icon="INFO")
        _draw_wrapped_label(header, _get_geotype_label(geo.GEOType), width=54)
        _draw_geotype_hint(header, geo.GEOType)

        layout.prop(geo, "GEOTypeEnum", text="Type")
        layout.prop(geo, "GEOFlags", text="Flags")

        info = layout.box()
        parent_name = obj.parent.name if obj.parent is not None else "WORLD"
        info.label(text=f"Parent: {parent_name}")
        info.label(text=_get_object_lod_label(obj))
        _draw_lod_context_notes(info, obj)
        _draw_legacy_geo_naming_note(layout)
        if getattr(obj, "type", None) == "MESH":
            info.label(text=f"Vertices: {len(obj.data.vertices)}", icon="MESH_DATA")
        elif getattr(geo, "IsSpinnerHelper", False):
            info.label(text="Spinner helper", icon="EMPTY_DATA")


class BZ98TOOLS_PT_view3d_quick_tools(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_QUICK_TOOLS"
    bl_label = "Quick Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        select_box = layout.box()
        select_box.label(text="Selection", icon="RESTRICT_SELECT_OFF")
        row = select_box.row(align=True)
        mesh_op = row.operator("bz.select_by_object_type", text="Meshes", icon="MESH_DATA")
        mesh_op.selection_mode = "MESH"
        non_mesh_op = row.operator("bz.select_by_object_type", text="Non-Mesh", icon="EMPTY_DATA")
        non_mesh_op.selection_mode = "NON_MESH"

        col_box = layout.box()
        col_box.label(text="VDF COL", icon="CUBE")
        col_row = col_box.row(align=True)
        col_row.operator_context = "EXEC_DEFAULT"
        shaped_op = col_row.operator("bz.generate_vdf_collision_meshes", text="Shaped", icon="MESH_UVSPHERE")
        shaped_op.profile = "SHAPED"
        square_op = col_row.operator("bz.generate_vdf_collision_meshes", text="Square", icon="MESH_CUBE")
        square_op.profile = "SQUARE"

        geo_box = layout.box()
        geo_box.label(text="Vehicle GEO Starter", icon="MESH_CUBE")
        geo_box.operator("bz.create_vehicle_geo_set", text="Create Standard Set", icon="ADD")

        normal_box = layout.box()
        normal_box.label(text="Normals", icon="NORMALS_FACE")
        normal_row = normal_box.row(align=True)
        outside_op = normal_row.operator("bz.quick_normals_fix", text="Outside", icon="NORMALS_VERTEX_OUTER")
        outside_op.normal_mode = "OUTSIDE"
        faces_op = normal_row.operator("bz.quick_normals_fix", text="From Faces", icon="NORMALS_FACE")
        faces_op.normal_mode = "FACE_NORMALS"

        validation_box = layout.box()
        validation_box.label(text="Validation", icon="CHECKMARK")
        validate_op = validation_box.operator(
            "bz.validate_scene", text="Validate Vehicle"
        )
        validate_op.preset = "VEHICLE"
        validation_box.operator_menu_enum(
            "bz.validate_scene", "preset", text="Validate Preset"
        )
        checks_row = validation_box.row()
        checks_row.operator_context = "INVOKE_DEFAULT"
        checks_row.operator(
            "bz.show_validation_checks", text="What Gets Checked", icon="INFO"
        )
        if len(getattr(context.scene, "bz_validation_issues", [])) > 0:
            counts = _get_validation_counts(context.scene, export_mode="ALL")
            row = validation_box.row(align=True)
            row.label(text=f"E {counts['ERROR']}")
            row.label(text=f"W {counts['WARNING']}")
            row.label(text=f"I {counts['INFO']}")
            if _validation_results_are_stale(context.scene):
                validation_box.label(text="Results may be stale.", icon="ERROR")
            _draw_validation_issue_report(
                validation_box, context.scene, export_mode="ALL", max_items=5
            )


class BZ98TOOLS_PT_view3d_organic_redux_skin(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_ORGANIC_REDUX_SKIN"
    bl_label = "Organic Redux Skin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "object", None) is not None

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Redux Mesh Skinning", icon="ARMATURE_DATA")
        box.operator(
            "bz.create_organic_redux_skin",
            text="Create Organic Redux Skin",
            icon="MOD_ARMATURE",
        )
        obj = context.object
        if getattr(obj, "type", None) == "MESH":
            selected_controls = [
                item
                for item in context.selected_objects
                if item != obj
                and getattr(item, "GEOPropertyGroup", None) is not None
                and bz_validation.parse_legacy_geo_name(getattr(item, "name", "")).get(
                    "valid"
                )
                and not bz_validation.is_collision_helper_name(
                    getattr(item, "name", "")
                )
            ]
            box.label(
                text=f"Selected GEO controls: {len(selected_controls)}",
                icon="OBJECT_DATA",
            )
        else:
            box.label(text="Select the continuous mesh target first.", icon="INFO")


class BZ98TOOLS_PT_view3d_animation_tools(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_ANIMATION_TOOLS"
    bl_label = "Animation Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Legacy Object Animation", icon="ANIM_DATA")
        _draw_wrapped_label(
            box, "Copies mirrored transform keys between matched GEO parts.", width=58
        )
        _draw_wrapped_label(
            box,
            "Default pairing: .L -> .R. Click to set Mirror From and Mirror To explicitly.",
            width=58,
        )
        box.operator_context = "INVOKE_DEFAULT"
        box.operator(
            "bz.mirror_legacy_animation_keys",
            text="Mirror Legacy Keys",
            icon="MOD_MIRROR",
        )


class BZ98TOOLS_PT_view3d_cockpit_tools(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_BZ_COCKPIT_TOOLS"
    bl_label = "Cockpit Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Cockpit Geometry Generator", icon="MESH_DATA")
        box.label(
            text="Duplicates selected mesh faces into matching LOD2 cockpit GEOs."
        )
        box.label(
            text="Example: ara11bda -> ara21bda, preserving origin and materials."
        )
        box.operator("bz.generate_cockpit_geometry", text="Generate Cockpit GEOs")


class BZ98TOOLS_PT_geo_collision(bpy.types.Panel):
    bl_idname = "OBJECT_PT_BZ_GEO_COLLISION"
    bl_label = "Collision"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_parent_id = "OBJECT_PT_BZ_GEO"

    def draw(self, context):
        layout = self.layout
        geo = getattr(context.object, "GEOPropertyGroup", None)
        if geo is None:
            layout.label(text="No GEO data on active object.")
            return

        layout.prop(geo, "GenerateCollision")
        if geo.GenerateCollision:
            _draw_wrapped_label(
                layout,
                "Applies to SDF/building GEO collision data only. Use VDF COL helpers for vehicle inner_col/outer_col.",
                icon="INFO",
                width=56,
            )
            return

        layout.label(text="GEO Center")
        _draw_xyz_row(
            layout, geo, ("GeoCenterX", "GeoCenterY", "GeoCenterZ"), ("X", "Y", "Z")
        )
        layout.label(text="Projectile Box Half-Extents")
        _draw_xyz_row(
            layout,
            geo,
            ("BoxHalfHeightX", "BoxHalfHeightY", "BoxHalfHeightZ"),
            ("X", "Y", "Z"),
        )
        layout.prop(geo, "SphereRadius")
        layout.operator("bz.generatecollision", text="Generate Collision Settings")


class BZ98TOOLS_PT_geo_sdf(bpy.types.Panel):
    bl_idname = "OBJECT_PT_BZ_GEO_SDF"
    bl_label = "SDF Extras"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_parent_id = "OBJECT_PT_BZ_GEO"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        geo = getattr(context.object, "GEOPropertyGroup", None)
        if geo is None:
            layout.label(text="No GEO data on active object.")
            return

        if int(getattr(geo, "GEOType", 0)) == 15:
            hint = layout.box()
            hint.label(text="Type 15 SDF spinner helper", icon="INFO")
            hint.label(text="Stock structures commonly use Target Y as spin speed.")
        layout.prop(geo, "SDFDDR")
        _draw_xyz_row(layout, geo, ("SDFX", "SDFY", "SDFZ"), ("X", "Y", "Z"))
        layout.prop(geo, "SDFTime")


class BZ98TOOLS_PT_geo_vdf(bpy.types.Panel):
    bl_idname = "OBJECT_PT_BZ_GEO_VDF"
    bl_label = "VDF Extras"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_parent_id = "OBJECT_PT_BZ_GEO"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        geo = getattr(context.object, "GEOPropertyGroup", None)
        if geo is None:
            layout.label(text="No GEO data on active object.")
            return

        layout.operator("bz.create_spinner_helper", text="Create Spinner Helper")
        layout.prop(geo, "IsSpinnerHelper")
        if geo.IsSpinnerHelper:
            layout.prop(geo, "SpinnerTarget")
            layout.prop(geo, "SpinnerAxis")
            layout.prop(geo, "SpinnerSpeed")
            _draw_wrapped_label(
                layout,
                "Spinner helpers export as GEO Type 15 and are written after their target slot.",
                icon="INFO",
                width=56,
            )
        info_row = layout.row()
        info_row.operator_context = "INVOKE_DEFAULT"
        info_row.operator(
            "bz.show_advanced_vdf_info", text="Advanced VDF Notes", icon="INFO"
        )


class BZ98TOOLS_PT_geo_advanced(bpy.types.Panel):
    bl_idname = "OBJECT_PT_BZ_GEO_ADVANCED"
    bl_label = "Experimental"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_parent_id = "OBJECT_PT_BZ_GEO"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        geo = getattr(context.object, "GEOPropertyGroup", None)
        if geo is None:
            layout.label(text="No GEO data on active object.")
            return

        raw_box = layout.box()
        raw_box.label(text="VDF Transform Override")
        raw_box.prop(geo, "UseRawVDFMatrix")
        raw_box.operator(
            "bz.capture_raw_vdf_matrix", text="Capture From Current Transform"
        )
        if geo.UseRawVDFMatrix:
            _draw_wrapped_label(
                raw_box,
                "Order: right, up, front, position. Use for VDF transform scaling experiments.",
                width=56,
            )
            raw_box.prop(geo, "RawVDFMatrix")

        face_box = layout.box()
        face_box.label(text="GEO Header / Face Raw")
        face_box.prop(geo, "GEOHeaderUnknown")
        face_box.prop(geo, "GEOHeaderUnknown2")
        face_box.prop(geo, "GEOFaceUnknownDefault")
        face_box.prop(geo, "GEOFaceShadeTypeDefault")
        face_box.prop(geo, "GEOFaceTextureTypeDefault")
        face_box.prop(geo, "GEOFaceXluscentTypeDefault")
        face_box.prop(geo, "GEOFaceParentDefault")
        face_box.prop(geo, "GEOFaceNodeDefault")


class BZ98TOOLS_PT_geo_face_data(bpy.types.Panel):
    bl_idname = "OBJECT_PT_BZ_GEO_FACE_DATA"
    bl_label = "Selected Face Raw Data"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_parent_id = "OBJECT_PT_BZ_GEO"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        obj = context.object
        geo = getattr(obj, "GEOPropertyGroup", None)
        if geo is None or getattr(obj, "type", None) != "MESH":
            layout.label(text="Select a GEO mesh object.", icon="INFO")
            return

        face_indices = _get_selected_face_indices(obj)
        if not face_indices:
            layout.label(text="Select one or more faces in Edit Mode.", icon="INFO")
            layout.label(text="Object Mode face selection also works when present.")
            return

        mesh = obj.data
        header = layout.box()
        if len(face_indices) == 1:
            header.label(text=f"Face {face_indices[0]}", icon="FACESEL")
        else:
            header.label(text=f"{len(face_indices)} selected faces", icon="FACESEL")

        data_box = layout.box()
        rows = (
            ("Shade Byte", "bz_face_shade_type", geo.GEOFaceShadeTypeDefault),
            ("Texture Byte", "bz_face_texture_type", geo.GEOFaceTextureTypeDefault),
            (
                "Translucent Byte",
                "bz_face_xluscent_type",
                geo.GEOFaceXluscentTypeDefault,
            ),
            ("Face Parent", "bz_face_parent", geo.GEOFaceParentDefault),
            ("Face Node", "bz_face_node", geo.GEOFaceNodeDefault),
            ("Reserved Raw", "bz_face_unknown_raw", geo.GEOFaceUnknownDefault),
        )
        for label, attr_name, default_value in rows:
            row = data_box.row()
            row.label(text=label)
            row.label(
                text=_summarize_face_values(
                    mesh, face_indices, attr_name, default_value
                )
            )

        plane_box = layout.box()
        plane_box.label(text="Face Plane Floats", icon="NORMALS_FACE")
        plane_rows = (
            ("Plane X", "bz_face_plane_x"),
            ("Plane Y", "bz_face_plane_y"),
            ("Plane Z", "bz_face_plane_z"),
            ("Plane D", "bz_face_plane_d"),
        )
        for label, attr_name in plane_rows:
            row = plane_box.row()
            row.label(text=label)
            row.label(
                text=_summarize_face_float_values(mesh, face_indices, attr_name, 0.0)
            )

        if len(face_indices) == 1:
            face_index = face_indices[0]
            raw = (
                _get_face_attr_int(
                    mesh, "bz_face_shade_type", face_index, geo.GEOFaceShadeTypeDefault
                )
                & 0xFF,
                _get_face_attr_int(
                    mesh,
                    "bz_face_texture_type",
                    face_index,
                    geo.GEOFaceTextureTypeDefault,
                )
                & 0xFF,
                _get_face_attr_int(
                    mesh,
                    "bz_face_xluscent_type",
                    face_index,
                    geo.GEOFaceXluscentTypeDefault,
                )
                & 0xFF,
            )
            layout.label(
                text=f"String header bytes: {raw[0]:02X} {raw[1]:02X} {raw[2]:02X}"
            )


class BZ98TOOLS_OT_fill_material_texture_name(bpy.types.Operator):
    bl_idname = "bz.fill_material_texture_name"
    bl_label = "Use Material Name"
    bl_description = (
        "Fill the Battlezone texture name from the current Blender material name"
    )

    def execute(self, context):
        material = context.material
        if material is None:
            self.report({"ERROR"}, "No active material.")
            return {"CANCELLED"}

        material_props = getattr(material, "MaterialPropertyGroup", None)
        if material_props is None:
            self.report(
                {"ERROR"}, "Active material has no Battlezone material properties."
            )
            return {"CANCELLED"}

        derived_name = _derive_legacy_texture_name(material.name)
        if not derived_name:
            self.report(
                {"ERROR"},
                "Material name cannot be converted into a Battlezone texture name.",
            )
            return {"CANCELLED"}

        material_props.MapTexture = derived_name
        self.report({"INFO"}, f"Battlezone texture name set to '{derived_name}'.")
        return {"FINISHED"}


class BZ98TOOLS_OT_fill_material_texture_from_image(bpy.types.Operator):
    bl_idname = "bz.fill_material_texture_from_image"
    bl_label = "Use Image Name"
    bl_description = "Fill the Battlezone texture name from the linked image texture"

    def execute(self, context):
        material = context.material
        if material is None:
            self.report({"ERROR"}, "No active material.")
            return {"CANCELLED"}

        material_props = getattr(material, "MaterialPropertyGroup", None)
        if material_props is None:
            self.report(
                {"ERROR"}, "Active material has no Battlezone material properties."
            )
            return {"CANCELLED"}

        derived_name = _get_material_image_name(material)
        if not derived_name:
            self.report(
                {"ERROR"}, "No linked image texture was found on this material."
            )
            return {"CANCELLED"}

        material_props.MapTexture = derived_name
        self.report(
            {"INFO"},
            f"Battlezone texture name set to '{derived_name}' from the linked image.",
        )
        return {"FINISHED"}


class BattlezoneMaterialProperties(bpy.types.Panel):
    bl_idname = "MATERIAL_PT_BZ_GEO"
    bl_label = "Battlezone Material"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    def draw(self, context):
        layout = self.layout
        material = context.material
        MaterialPropertyGroup = material.MaterialPropertyGroup

        explicit_name, image_name, derived_name, resolved_name, source = (
            _get_material_texture_preview(material)
        )

        box = layout.box()
        box.label(text="Legacy Texture")
        box.prop(MaterialPropertyGroup, "MapTexture", text="Texture Name")

        row = box.row(align=True)
        row.operator("bz.fill_material_texture_name", text="Use Material Name")
        image_row = row.row(align=True)
        image_row.enabled = bool(image_name)
        image_row.operator("bz.fill_material_texture_from_image", text="Use Image Name")

        preview_box = layout.box()
        preview_box.label(text="Export Preview")
        preview_box.label(text=f"Resolved Name: {resolved_name or '(none)'}")
        preview_box.label(text=f"Source: {source}")
        if explicit_name and len(explicit_name) > 8:
            preview_box.label(
                text="Explicit Battlezone texture names should stay within 8 characters.",
                icon="WARNING",
            )
        elif image_name and not explicit_name:
            preview_box.label(
                text=f"If left blank, export will derive '{image_name}' from the linked image.",
                icon="INFO",
            )
        elif not explicit_name and derived_name:
            preview_box.label(
                text=f"If left blank, export will derive '{derived_name}'.", icon="INFO"
            )
        elif not resolved_name:
            preview_box.label(text="No texture name is available yet.", icon="ERROR")

        color_box = layout.box()
        color_box.label(text="Color")
        color_box.prop(material, "diffuse_color")


"""
Operators
Used for doing actions.
In this case used for creating a new animation element and removing one.
"""


class OPCreateNewElement(bpy.types.Operator):
    bl_idname = "bz.createanimelement"
    bl_label = "Create Element"
    bl_description = "Create a new animation element"

    def execute(self, context):
        item = context.scene.AnimationCollection.add()
        item.Index = len(context.scene.AnimationCollection) - 1
        context.scene.CurAnimation = len(context.scene.AnimationCollection) - 1
        return {"FINISHED"}


class OPDeleteElement(bpy.types.Operator):
    bl_idname = "bz.deleteanimelement"
    bl_label = "Delete Element"
    bl_description = "Delete the currently selected animation element"

    def execute(self, context):
        scene = context.scene
        if len(scene.AnimationCollection) == 0 or scene.CurAnimation >= len(
            scene.AnimationCollection
        ):
            self.report({"ERROR"}, "No animation element selected.")
            return {"CANCELLED"}
        scene.AnimationCollection.remove(scene.CurAnimation)
        scene.CurAnimation = min(scene.CurAnimation, len(scene.AnimationCollection) - 1)
        if len(scene.AnimationCollection) == 0:
            scene.CurAnimation = 0
        return {"FINISHED"}


class OPDuplicateElement(bpy.types.Operator):
    bl_idname = "bz.duplicateanimelement"
    bl_label = "Duplicate Element"
    bl_description = "Duplicate the currently selected animation element"

    def execute(self, context):
        scene = context.scene
        if len(scene.AnimationCollection) == 0 or scene.CurAnimation >= len(
            scene.AnimationCollection
        ):
            self.report({"ERROR"}, "No animation element selected.")
            return {"CANCELLED"}

        source_index = scene.CurAnimation
        source_item = scene.AnimationCollection[source_index]
        new_item = scene.AnimationCollection.add()
        _copy_animation_item(source_item, new_item)
        new_index = len(scene.AnimationCollection) - 1
        target_index = source_index + 1
        scene.AnimationCollection.move(new_index, target_index)
        scene.CurAnimation = target_index
        return {"FINISHED"}


class OPMoveElement(bpy.types.Operator):
    bl_idname = "bz.moveanimelement"
    bl_label = "Move Element"
    bl_description = "Move the selected animation element up or down"

    direction: Any = EnumProperty(
        name="Direction",
        items=(
            ("UP", "Up", "Move the animation element up"),
            ("DOWN", "Down", "Move the animation element down"),
        ),
        default="UP",
    )

    def execute(self, context):
        scene = context.scene
        index = scene.CurAnimation
        count = len(scene.AnimationCollection)
        if count == 0 or index >= count:
            self.report({"ERROR"}, "No animation element selected.")
            return {"CANCELLED"}

        if self.direction == "UP":
            if index <= 0:
                return {"CANCELLED"}
            scene.AnimationCollection.move(index, index - 1)
            scene.CurAnimation = index - 1
        else:
            if index >= count - 1:
                return {"CANCELLED"}
            scene.AnimationCollection.move(index, index + 1)
            scene.CurAnimation = index + 1
        return {"FINISHED"}


class OPApplyAnimationPreset(bpy.types.Operator):
    bl_idname = "bz.apply_animation_preset"
    bl_label = "Add Animation Preset"
    bl_description = "Append a preset set of animation slots as a starting point"

    preset: Any = EnumProperty(
        name="Preset",
        items=ANIMATION_PRESET_ITEMS,
        default="DEPLOY_PAIR",
    )

    def execute(self, context):
        scene = context.scene
        indices = ANIMATION_PRESET_SLOTS.get(self.preset, [])
        if not indices:
            self.report({"ERROR"}, "Unknown animation preset.")
            return {"CANCELLED"}

        for index_value in indices:
            item = scene.AnimationCollection.add()
            item.Index = index_value
            item.Start = 0
            item.Length = 0
            item.Loop = 1
            item.Speed = 15.0

        scene.CurAnimation = len(scene.AnimationCollection) - 1
        self.report({"INFO"}, f"Added {len(indices)} slots from the preset.")
        return {"FINISHED"}


class BZ98TOOLS_OT_mirror_legacy_animation_keys(bpy.types.Operator):
    bl_idname = "bz.mirror_legacy_animation_keys"
    bl_label = "Mirror Legacy Animation Keys"
    bl_description = (
        "Copy mirrored object transform keyframes between matched legacy GEO parts"
    )
    bl_options = {"REGISTER", "UNDO"}

    mirror_from: Any = bpy.props.PointerProperty(
        name="Mirror From",
        description="Optional explicit source GEO object. When both selectors are set, name-token matching is skipped.",
        type=bpy.types.Object,
    )

    mirror_to: Any = bpy.props.PointerProperty(
        name="Mirror To",
        description="Optional explicit target GEO object. When both selectors are set, name-token matching is skipped.",
        type=bpy.types.Object,
    )

    source_token: Any = StringProperty(
        name="Source Token",
        description="Text in source object names to replace when finding the target side",
        default=".L",
    )

    target_token: Any = StringProperty(
        name="Target Token",
        description="Replacement text used to find matching target object names",
        default=".R",
    )

    source_scope: Any = EnumProperty(
        name="Scope",
        description="Objects to scan for source-side animation",
        items=(
            ("SELECTED", "Selected", "Use selected source objects"),
            ("SCENE", "Scene", "Scan all scene objects"),
        ),
        default="SELECTED",
    )

    mirror_axis: Any = EnumProperty(
        name="Mirror Axis",
        description="Local legacy axis to mirror across",
        items=(
            ("X", "X", "Mirror left/right across local X"),
            ("Y", "Y", "Mirror across local Y"),
            ("Z", "Z", "Mirror across local Z"),
        ),
        default="X",
    )

    use_scene_range: Any = BoolProperty(
        name="Use Scene Frame Range",
        description="Limit copied keys to the current scene frame range",
        default=True,
    )

    frame_start: Any = IntProperty(
        name="Start",
        description="First frame to copy when scene range is disabled",
        default=0,
        min=-999999,
        max=999999,
    )

    frame_end: Any = IntProperty(
        name="End",
        description="Last frame to copy when scene range is disabled",
        default=9999,
        min=-999999,
        max=999999,
    )

    rest_frame: Any = IntProperty(
        name="Rest Frame",
        description="Frame used as the source and target rest pose before mirrored motion is applied",
        default=0,
        min=-999999,
        max=999999,
    )

    include_descendants: Any = BoolProperty(
        name="Include Descendants",
        description="When using selected sources, also scan their child objects",
        default=True,
    )

    include_location: Any = BoolProperty(
        name="Location",
        description="Copy mirrored location keys",
        default=True,
    )

    include_rotation: Any = BoolProperty(
        name="Rotation",
        description="Copy mirrored rotation keys",
        default=True,
    )

    include_scale: Any = BoolProperty(
        name="Scale",
        description="Copy scale keys too; legacy VDF/SDF export normally ignores object scale animation",
        default=False,
    )

    overwrite_target_keys: Any = BoolProperty(
        name="Overwrite Target Keys",
        description="Remove existing target transform keys in the copied frame range before inserting mirrored keys",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return getattr(context, "scene", None) is not None

    def invoke(self, context, event):
        scene = context.scene
        self.frame_start = int(getattr(scene, "frame_start", self.frame_start))
        self.frame_end = int(getattr(scene, "frame_end", self.frame_end))
        self.rest_frame = int(getattr(scene, "frame_start", self.rest_frame))
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        explicit = layout.box()
        explicit.label(text="Explicit Pair")
        explicit.prop(self, "mirror_from")
        explicit.prop(self, "mirror_to")
        if self.mirror_from is not None and self.mirror_to is not None:
            warning = _legacy_mirror_similarity_warning(
                self.mirror_from, self.mirror_to
            )
            if warning:
                _draw_wrapped_label(explicit, warning, icon="WARNING", width=62)
            else:
                _draw_wrapped_label(
                    explicit,
                    "Selected objects have matching mesh counts.",
                    icon="CHECKMARK",
                    width=62,
                )

        naming = layout.box()
        naming.label(text="Matching")
        naming.enabled = not (
            self.mirror_from is not None and self.mirror_to is not None
        )
        row = naming.row(align=True)
        row.prop(self, "source_token")
        row.prop(self, "target_token")
        naming.prop(self, "source_scope")
        naming.prop(self, "include_descendants")

        timing = layout.box()
        timing.label(text="Timing")
        timing.prop(self, "use_scene_range")
        if not self.use_scene_range:
            row = timing.row(align=True)
            row.prop(self, "frame_start")
            row.prop(self, "frame_end")
        timing.prop(self, "rest_frame")

        mirror = layout.box()
        mirror.label(text="Mirror")
        mirror.prop(self, "mirror_axis")
        row = mirror.row(align=True)
        row.prop(self, "include_location")
        row.prop(self, "include_rotation")
        row.prop(self, "include_scale")
        mirror.prop(self, "overwrite_target_keys")

    def execute(self, context):
        explicit_pair = self.mirror_from is not None or self.mirror_to is not None
        if explicit_pair and (self.mirror_from is None or self.mirror_to is None):
            self.report(
                {"ERROR"},
                "Set both Mirror From and Mirror To, or leave both blank for token matching.",
            )
            return {"CANCELLED"}
        if not explicit_pair and not self.source_token:
            self.report({"ERROR"}, "Source token cannot be blank.")
            return {"CANCELLED"}
        if not (self.include_location or self.include_rotation or self.include_scale):
            self.report({"ERROR"}, "Enable at least one transform channel.")
            return {"CANCELLED"}

        scene = context.scene
        if self.use_scene_range:
            frame_start = int(scene.frame_start)
            frame_end = int(scene.frame_end)
        else:
            frame_start = int(self.frame_start)
            frame_end = int(self.frame_end)
        if frame_start > frame_end:
            self.report({"ERROR"}, "Start frame must be before or equal to end frame.")
            return {"CANCELLED"}

        if explicit_pair:
            candidates = [self.mirror_from]
        elif self.source_scope == "SCENE":
            candidates = list(scene.objects)
        else:
            candidates = list(getattr(context, "selected_objects", []))
            if self.include_descendants:
                candidates = _expand_object_descendants(candidates)

        if not candidates:
            self.report({"ERROR"}, "No source objects found.")
            return {"CANCELLED"}

        scene_objects = {obj.name: obj for obj in scene.objects}
        pairs = []
        missing_targets = 0
        for source in candidates:
            if explicit_pair:
                target = self.mirror_to
            else:
                target_name = _replace_name_token(
                    source.name, self.source_token, self.target_token
                )
                if not target_name or target_name == source.name:
                    continue
                target = scene_objects.get(target_name)
                if target is None:
                    missing_targets += 1
                    continue

            action = getattr(getattr(source, "animation_data", None), "action", None)
            source_paths = {
                curve.data_path
                for curve in _legacy_transform_fcurves(
                    action, include_scale=self.include_scale
                )
            }
            source_rotation_paths = source_paths.intersection(
                {"rotation_euler", "rotation_quaternion", "rotation_axis_angle"}
            )
            if not (
                (self.include_location and "location" in source_paths)
                or (self.include_rotation and source_rotation_paths)
                or (self.include_scale and "scale" in source_paths)
            ):
                continue

            frames = _legacy_action_keyframes(
                action,
                frame_start=frame_start,
                frame_end=frame_end,
                include_scale=self.include_scale,
            )
            if not frames:
                continue
            pairs.append((source, target, action, frames, source_paths))

        if not pairs:
            detail = " Matching targets were missing." if missing_targets else ""
            self.report(
                {"ERROR"},
                "No matching animated source/target pairs were found." + detail,
            )
            return {"CANCELLED"}

        for source, target, _action, _frames, _source_paths in pairs:
            warning = _legacy_mirror_similarity_warning(source, target)
            if warning:
                self.report({"WARNING"}, warning)
                break

        keyed_paths = set()
        if self.include_location:
            keyed_paths.add("location")
        if self.include_rotation:
            keyed_paths.update(
                {"rotation_euler", "rotation_quaternion", "rotation_axis_angle"}
            )
        if self.include_scale:
            keyed_paths.add("scale")

        previous_frame = scene.frame_current
        previous_subframe = getattr(scene, "frame_subframe", 0.0)
        active_obj = context.view_layer.objects.active
        selected_objects = list(getattr(context, "selected_objects", []))
        mirror_matrix = _axis_mirror_matrix(self.mirror_axis)

        try:
            scene.frame_set(int(self.rest_frame))
            rest_matrices = {
                (source.name, target.name): (
                    source.matrix_basis.copy(),
                    target.matrix_basis.copy(),
                )
                for source, target, _action, _frames, _source_paths in pairs
            }

            for _source, target, _action, _frames, _source_paths in pairs:
                target_action = _ensure_object_action(target)
                if self.overwrite_target_keys:
                    _delete_action_keys_in_range(
                        target_action, keyed_paths, frame_start, frame_end
                    )

            inserted_frames = 0
            for source, target, source_action, frames, source_paths in pairs:
                source_rest, target_rest = rest_matrices[(source.name, target.name)]
                mirrored_source_rest = mirror_matrix @ source_rest @ mirror_matrix
                rest_inverse = mirrored_source_rest.inverted_safe()
                copy_location = self.include_location and "location" in source_paths
                copy_rotation = self.include_rotation and bool(
                    source_paths.intersection(
                        {"rotation_euler", "rotation_quaternion", "rotation_axis_angle"}
                    )
                )
                copy_scale = self.include_scale and "scale" in source_paths

                for frame in frames:
                    _set_scene_frame_float(scene, frame)
                    mirrored_source_basis = (
                        mirror_matrix @ source.matrix_basis.copy() @ mirror_matrix
                    )
                    target_basis = target_rest @ (rest_inverse @ mirrored_source_basis)
                    loc, quat, scale = target_basis.decompose()

                    if copy_location:
                        target.location = loc
                        target.keyframe_insert(data_path="location", frame=frame)

                    if copy_rotation:
                        if target.rotation_mode == "QUATERNION":
                            target.rotation_quaternion = quat
                            target.keyframe_insert(
                                data_path="rotation_quaternion", frame=frame
                            )
                        elif target.rotation_mode == "AXIS_ANGLE":
                            axis = quat.axis
                            if axis.length < 0.000001:
                                axis = mathutils.Vector((0.0, 0.0, 1.0))
                            target.rotation_axis_angle = (
                                quat.angle,
                                axis.x,
                                axis.y,
                                axis.z,
                            )
                            target.keyframe_insert(
                                data_path="rotation_axis_angle", frame=frame
                            )
                        else:
                            target.rotation_euler = quat.to_euler(target.rotation_mode)
                            target.keyframe_insert(
                                data_path="rotation_euler", frame=frame
                            )

                    if copy_scale:
                        target.scale = scale
                        target.keyframe_insert(data_path="scale", frame=frame)

                    interpolation = _source_key_interpolation(
                        source_action,
                        frame,
                        include_scale=self.include_scale,
                    )
                    _set_inserted_key_interpolation(
                        target.animation_data.action,
                        keyed_paths,
                        frame,
                        interpolation,
                    )
                    inserted_frames += 1
        finally:
            scene.frame_set(previous_frame, subframe=previous_subframe)
            for obj in list(context.selected_objects):
                obj.select_set(False)
            for obj in selected_objects:
                if bpy.data.objects.get(obj.name) is not None:
                    obj.select_set(True)
            if (
                active_obj is not None
                and bpy.data.objects.get(active_obj.name) is not None
            ):
                context.view_layer.objects.active = active_obj

        self.report(
            {"INFO"},
            f"Mirrored {inserted_frames} keyed frame(s) across {len(pairs)} pair(s).",
        )
        return {"FINISHED"}


"""
Operators
Used for doing actions.
In this case used for creating a new animation element and removing one.
"""


class OPGenerateVDFCollisionMeshes(bpy.types.Operator):
    """Generate VDF-style inner_col / outer_col collision meshes from selected mesh bounds"""

    bl_idname = "bz.generate_vdf_collision_meshes"
    bl_label = "Generate VDF COL Boxes"
    bl_description = (
        "Create stock-profiled inner_col and outer_col meshes from the combined bounding "
        "box of the selected mesh objects for use as VDF collision data "
        "(SDF collision is calculated differently and does not use these)"
    )

    inner_scale: Any = bpy.props.FloatProperty(
        name="Inner Scale",
        description="Overall multiplier for the stock inner collision profile; 1.0 matches stock VDF side insets",
        default=1.0,
        min=0.05,
        max=1.0,
    )

    outer_margin: Any = bpy.props.FloatProperty(
        name="Outer Box Margin",
        description="Extra padding added to the outer collision box (fraction of box size)",
        default=0.0,
        min=0.0,
        max=1.0,
    )

    profile: Any = bpy.props.EnumProperty(
        name="COL Profile",
        description="Shape used for generated VDF inner/outer collision helpers",
        items=(
            ("SHAPED", "Shaped", "Use stock-like inner insets inside the outer box"),
            (
                "SQUARE",
                "Square Only",
                "Use simple axis-aligned box helpers with no stock inner inset",
            ),
        ),
        default="SHAPED",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "profile")
        layout.prop(self, "inner_scale")
        layout.prop(self, "outer_margin")
        if self.profile == "SHAPED":
            layout.label(text="Stock inner insets:")
            layout.label(text="X 20%, Y 25%")
            layout.label(text="Z 30% low, 20% high")
        else:
            layout.label(
                text="Square Only makes inner_col match the simple box profile."
            )

    def execute(self, context):
        import mathutils

        # -----------------------------------------
        # Collect source objects
        # -----------------------------------------
        collision_helper_names = {
            "inner_col",
            "innercol",
            "inner_collision",
            "innercollision",
            "outer_col",
            "outercol",
            "outer_collision",
            "outercollision",
        }
        selected_meshes = [
            o
            for o in context.selected_objects
            if o.type == "MESH" and o.name.lower() not in collision_helper_names
        ]

        if not selected_meshes:
            self.report({"ERROR"}, "No mesh objects selected")
            return {"CANCELLED"}

        # If somehow multiple selected but active is not mesh, we still proceed with selected list
        # Active is only relevant if you later want to inherit orientation, etc.

        # -----------------------------------------
        # Build a combined WORLD-space bounding box
        # -----------------------------------------
        first = True
        min_w = max_w = None

        for obj in selected_meshes:
            if not obj.bound_box:
                continue

            for corner in obj.bound_box:
                v_world = obj.matrix_world @ mathutils.Vector(corner)

                if first:
                    min_w = v_world.copy()
                    max_w = v_world.copy()
                    first = False
                else:
                    min_w.x = min(min_w.x, v_world.x)
                    min_w.y = min(min_w.y, v_world.y)
                    min_w.z = min(min_w.z, v_world.z)

                    max_w.x = max(max_w.x, v_world.x)
                    max_w.y = max(max_w.y, v_world.y)
                    max_w.z = max(max_w.z, v_world.z)

        if first or min_w is None or max_w is None:
            self.report(
                {"ERROR"}, "Unable to compute bounding box from selected meshes"
            )
            return {"CANCELLED"}

        center_w = (min_w + max_w) * 0.5
        half = (max_w - min_w) * 0.5

        # Avoid degenerate boxes (flat planes)
        eps = 0.01
        for attr in ("x", "y", "z"):
            if abs(getattr(half, attr)) < eps:
                setattr(half, attr, eps)

        outer_half = half * (1.0 + self.outer_margin)
        outer_min = center_w - outer_half
        outer_max = center_w + outer_half

        if self.profile == "SQUARE":
            inner_min = outer_min.copy()
            inner_max = outer_max.copy()
        else:
            stock_inner_insets = {
                "x_min": 0.198,
                "x_max": 0.197,
                "y_min": 0.256,
                "y_max": 0.245,
                "z_min": 0.303,
                "z_max": 0.197,
            }
            outer_size = outer_max - outer_min
            inner_min = mathutils.Vector(
                (
                    outer_min.x + (outer_size.x * stock_inner_insets["x_min"]),
                    outer_min.y + (outer_size.y * stock_inner_insets["y_min"]),
                    outer_min.z + (outer_size.z * stock_inner_insets["z_min"]),
                )
            )
            inner_max = mathutils.Vector(
                (
                    outer_max.x - (outer_size.x * stock_inner_insets["x_max"]),
                    outer_max.y - (outer_size.y * stock_inner_insets["y_max"]),
                    outer_max.z - (outer_size.z * stock_inner_insets["z_max"]),
                )
            )

        if self.inner_scale < 1.0:
            inner_center = (inner_min + inner_max) * 0.5
            inner_half = (inner_max - inner_min) * (0.5 * self.inner_scale)
            inner_min = inner_center - inner_half
            inner_max = inner_center + inner_half

        def make_box(name: str, min_vec: mathutils.Vector, max_vec: mathutils.Vector):
            # Reuse an existing object with that name if it's a mesh, otherwise create new
            existing = bpy.data.objects.get(name)
            if existing is not None and existing.type != "MESH":
                existing = None

            mesh = bpy.data.meshes.new(name)

            if existing is None:
                obj_box = bpy.data.objects.new(name, mesh)
                context.collection.objects.link(obj_box)
            else:
                obj_box = existing
                obj_box.data = mesh

            box_center = (min_vec + max_vec) * 0.5
            local_min = min_vec - box_center
            local_max = max_vec - box_center
            verts = [
                (local_max.x, local_max.y, local_min.z),
                (local_max.x, local_min.y, local_min.z),
                (local_min.x, local_min.y, local_min.z),
                (local_min.x, local_max.y, local_min.z),
                (local_max.x, local_max.y, local_max.z),
                (local_max.x, local_min.y, local_max.z),
                (local_min.x, local_min.y, local_max.z),
                (local_min.x, local_max.y, local_max.z),
            ]
            faces = [
                (0, 1, 2, 3),
                (4, 7, 6, 5),
                (0, 4, 5, 1),
                (1, 5, 6, 2),
                (2, 6, 7, 3),
                (3, 7, 4, 0),
            ]
            mesh.from_pydata(verts, [], faces)
            mesh.update()

            # Place cube at the combined center in WORLD space, axis-aligned
            obj_box.matrix_world = mathutils.Matrix.Translation(box_center)

            # Helper-ish but exportable:
            obj_box.display_type = "WIRE"
            obj_box.hide_render = True  # fine for export
            obj_box.hide_viewport = False  # must be visible so user sees them

            # No parenting – keep them in world
            obj_box.parent = None

            return obj_box

        outer_obj = make_box("outer_col", outer_min, outer_max)
        inner_obj = make_box("inner_col", inner_min, inner_max)

        self.report(
            {"INFO"},
            "Generated stock-profiled VDF inner_col and outer_col from selected mesh bounds",
        )
        return {"FINISHED"}


class OPGenerateCollision(bpy.types.Operator):
    bl_idname = "bz.generatecollision"
    bl_label = "Generate Collisions"
    bl_description = "Generate Collisions, useful for a manual user"

    def execute(self, context):
        # Get the active object.
        obj = context.view_layer.objects.active
        # Get ready...
        minx, miny, minz, maxx, maxy, maxz, maxoverall = [0.0] * 7
        for vert in obj.data.vertices:
            if vert.co.x < minx:
                minx = vert.co.x
            if vert.co.x > maxx:
                maxx = vert.co.x
            if vert.co.y < minz:
                minz = vert.co.y
            if vert.co.y > maxz:
                maxz = vert.co.y
            if vert.co.z < miny:
                miny = vert.co.z
            if vert.co.z > maxy:
                maxy = vert.co.z
            # Get maximum vertice distance to generate sphere radius.
            for value in vert.co:
                if abs(value) > maxoverall:
                    maxoverall = abs(value)

        obj.GEOPropertyGroup.SphereRadius = maxoverall

        obj.GEOPropertyGroup.GeoCenterX = (minx + maxx) / 2
        obj.GEOPropertyGroup.GeoCenterY = (miny + maxy) / 2
        obj.GEOPropertyGroup.GeoCenterZ = (minz + maxz) / 2

        if abs(minx - obj.GEOPropertyGroup.GeoCenterX) >= abs(
            maxx - obj.GEOPropertyGroup.GeoCenterX
        ):
            obj.GEOPropertyGroup.BoxHalfHeightX = abs(
                minx - obj.GEOPropertyGroup.GeoCenterX
            )
        else:
            obj.GEOPropertyGroup.BoxHalfHeightX = abs(
                maxx - obj.GEOPropertyGroup.GeoCenterX
            )

        if abs(miny - obj.GEOPropertyGroup.GeoCenterY) >= abs(
            maxy - obj.GEOPropertyGroup.GeoCenterY
        ):
            obj.GEOPropertyGroup.BoxHalfHeightY = abs(
                miny - obj.GEOPropertyGroup.GeoCenterY
            )
        else:
            obj.GEOPropertyGroup.BoxHalfHeightY = abs(
                maxy - obj.GEOPropertyGroup.GeoCenterY
            )

        if abs(minz - obj.GEOPropertyGroup.GeoCenterZ) >= abs(
            maxz - obj.GEOPropertyGroup.GeoCenterZ
        ):
            obj.GEOPropertyGroup.BoxHalfHeightZ = abs(
                minz - obj.GEOPropertyGroup.GeoCenterZ
            )
        else:
            obj.GEOPropertyGroup.BoxHalfHeightZ = abs(
                maxz - obj.GEOPropertyGroup.GeoCenterZ
            )
        return {"FINISHED"}


class OPCreateSpinnerHelper(bpy.types.Operator):
    bl_idname = "bz.create_spinner_helper"
    bl_label = "Create Spinner Helper"
    bl_description = (
        "Create a spinner helper object linked to the active GEO for VDF spinner export"
    )

    def execute(self, context):
        src = context.view_layer.objects.active
        if src is None:
            self.report({"ERROR"}, "No active object selected")
            return {"CANCELLED"}

        if len(src.name) < 5:
            self.report({"ERROR"}, "Active object name must be at least 5 characters")
            return {"CANCELLED"}

        src_name = src.name.lower()
        lod_char = src_name[3] if len(src_name) >= 4 else "1"
        lod = 1 if lod_char not in ["1", "2", "3"] else int(lod_char)
        fixed_src_name = _fix_geo_export_name(src.name, lod).lower()

        helper_base = (fixed_src_name[:7] + "t")[:8]
        helper_name = helper_base
        if bpy.data.objects.get(helper_name) is not None:
            for i in range(10):
                candidate = (helper_base[:7] + str(i))[:8]
                if bpy.data.objects.get(candidate) is None:
                    helper_name = candidate
                    break
            else:
                self.report({"ERROR"}, "Unable to find a unique spinner helper name")
                return {"CANCELLED"}

        helper = bpy.data.objects.new(helper_name, None)
        helper.empty_display_type = "ARROWS"
        helper.empty_display_size = 0.25
        context.collection.objects.link(helper)

        helper.parent = src
        helper.matrix_parent_inverse = src.matrix_world.inverted()
        helper.location = (0.0, 0.0, 0.0)
        helper.rotation_euler = (0.0, 0.0, 0.0)
        helper.scale = (1.0, 1.0, 1.0)

        geo = helper.GEOPropertyGroup
        geo.GEOType = 15
        geo.IsSpinnerHelper = True
        geo.SpinnerTarget = fixed_src_name
        geo.SpinnerAxis = (1.0, 0.0, 0.0)
        geo.SpinnerSpeed = 1.0
        geo.GenerateCollision = False

        helper.hide_render = True

        for obj in context.selected_objects:
            obj.select_set(False)
        helper.select_set(True)
        context.view_layer.objects.active = helper

        self.report(
            {"INFO"}, f"Created spinner helper '{helper.name}' for '{fixed_src_name}'"
        )
        return {"FINISHED"}


class OPGenerateCockpitGeometry(bpy.types.Operator):
    bl_idname = "bz.generate_cockpit_geometry"
    bl_label = "Generate Cockpit GEOs"
    bl_description = (
        "Clone selected edit-mode mesh geometry into matching cockpit LOD objects "
        "(for example ara11bda becomes ara21bda) while preserving transforms, origins, and materials"
    )
    bl_options = {"REGISTER", "UNDO"}

    source_lod: Any = bpy.props.EnumProperty(
        name="Source LOD",
        description="Redux uses LOD1 for primary geometry and LOD2 for cockpit geometry",
        items=(
            ("1", "LOD1 Primary", "Create cockpit pieces from LOD1 primary GEOs"),
            ("2", "LOD2 Cockpit", "Create cockpit pieces from LOD2 cockpit GEOs"),
        ),
        default="1",
    )

    target_lod: Any = bpy.props.EnumProperty(
        name="Target LOD",
        description="Redux uses LOD1 for primary geometry and LOD2 for cockpit geometry",
        items=(
            ("1", "LOD1 Primary", "Generate LOD1 primary names"),
            ("2", "LOD2 Cockpit", "Generate LOD2 cockpit names"),
        ),
        default="2",
    )

    replace_existing: Any = bpy.props.BoolProperty(
        name="Replace Existing Targets",
        description="Replace existing generated target objects with the same names",
        default=True,
    )

    select_created: Any = bpy.props.BoolProperty(
        name="Select Created Objects",
        description="Select the generated cockpit objects when done",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return (
            getattr(context, "mode", "") in {"EDIT_MESH", "OBJECT"}
            and getattr(context, "scene", None) is not None
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        import bmesh

        if self.source_lod == self.target_lod:
            self.report({"ERROR"}, "Source LOD and target LOD must be different")
            return {"CANCELLED"}

        previous_mode = getattr(context, "mode", "OBJECT")
        active_obj = context.view_layer.objects.active

        source_objects = self._collect_source_objects(context)
        if not source_objects:
            self.report(
                {"ERROR"},
                "Select mesh geometry in Edit Mode, or selected faces in Object Mode",
            )
            return {"CANCELLED"}

        selections = []
        for source in source_objects:
            selected_faces = self._selected_face_indices(source, bmesh)
            if not selected_faces:
                continue

            target_name = self._target_name_for_source(source.name)
            if not target_name:
                self.report(
                    {"WARNING"},
                    f"Skipped '{source.name}': name is not a valid legacy GEO name",
                )
                continue
            if target_name.lower() == source.name.lower():
                self.report(
                    {"WARNING"},
                    f"Skipped '{source.name}': generated name matches the source",
                )
                continue

            selections.append((source, selected_faces, target_name))

        if not selections:
            self.report(
                {"ERROR"}, "No selected mesh faces found on valid source GEO objects"
            )
            return {"CANCELLED"}

        existing_conflicts = [
            target_name
            for _source, _selected_faces, target_name in selections
            if bpy.data.objects.get(target_name) is not None
        ]
        if existing_conflicts and not self.replace_existing:
            self.report(
                {"ERROR"},
                "Target object already exists: "
                + ", ".join(sorted(existing_conflicts)),
            )
            return {"CANCELLED"}

        if previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        created_by_source = {}
        created_objects = []

        for source, selected_faces, target_name in selections:
            existing = bpy.data.objects.get(target_name)
            if existing is not None:
                bpy.data.objects.remove(existing, do_unlink=True)

            mesh_copy = self._copy_selected_faces_to_mesh(source, selected_faces, bmesh)
            if mesh_copy is None or len(mesh_copy.polygons) == 0:
                if mesh_copy is not None:
                    bpy.data.meshes.remove(mesh_copy)
                self.report(
                    {"WARNING"},
                    f"Skipped '{source.name}': selected geometry produced an empty mesh",
                )
                continue

            mesh_copy.name = target_name
            cockpit_obj = bpy.data.objects.new(target_name, mesh_copy)
            self._link_to_source_collection(context, source, cockpit_obj)
            cockpit_obj.matrix_world = source.matrix_world.copy()
            cockpit_obj.hide_render = source.hide_render
            cockpit_obj.display_type = source.display_type
            self._copy_geo_properties(source, cockpit_obj)

            created_by_source[source] = cockpit_obj
            created_objects.append(cockpit_obj)

        for source, cockpit_obj in created_by_source.items():
            parent = getattr(source, "parent", None)
            target_parent = None
            if parent is not None:
                target_parent = created_by_source.get(parent)
                if target_parent is None:
                    parent_target_name = self._target_name_for_source(parent.name)
                    if parent_target_name:
                        target_parent = bpy.data.objects.get(parent_target_name)

            if target_parent is not None:
                world = cockpit_obj.matrix_world.copy()
                cockpit_obj.parent = target_parent
                cockpit_obj.matrix_world = world

        if self.select_created:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in created_objects:
                obj.select_set(True)
            if created_objects:
                context.view_layer.objects.active = created_objects[0]
        elif active_obj is not None:
            context.view_layer.objects.active = active_obj

        self.report({"INFO"}, f"Generated {len(created_objects)} cockpit GEO object(s)")
        return {"FINISHED"}

    def _collect_source_objects(self, context):
        if getattr(context, "mode", "") == "EDIT_MESH":
            objects = getattr(context, "objects_in_mode_unique_data", None)
            if objects:
                return [obj for obj in objects if getattr(obj, "type", None) == "MESH"]
            edit_obj = getattr(context, "edit_object", None)
            return [edit_obj] if getattr(edit_obj, "type", None) == "MESH" else []
        return [
            obj
            for obj in context.selected_objects
            if getattr(obj, "type", None) == "MESH"
        ]

    def _selected_face_indices(self, obj, bmesh_module):
        if getattr(obj, "mode", "") == "EDIT":
            bm = bmesh_module.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            selected = set()
            for face in bm.faces:
                if (
                    face.select
                    or any(vert.select for vert in face.verts)
                    or any(edge.select for edge in face.edges)
                ):
                    selected.add(face.index)
            return selected
        return {
            poly.index for poly in obj.data.polygons if getattr(poly, "select", False)
        }

    def _target_name_for_source(self, name):
        base_name = self._legacy_base_name(name)
        if len(base_name) < 5 or len(base_name) > 8:
            return ""
        if base_name[3] != self.source_lod:
            return ""
        chars = list(base_name)
        chars[3] = self.target_lod
        return _fix_geo_export_name("".join(chars), int(self.target_lod)).lower()

    def _legacy_base_name(self, name):
        raw = (name or "").strip()
        if len(raw) > 8:
            prefix, dot, suffix = raw.rpartition(".")
            if dot and len(suffix) == 3 and suffix.isdigit():
                raw = prefix
        return raw

    def _copy_selected_faces_to_mesh(self, source, selected_faces, bmesh_module):
        mesh_copy = source.data.copy()
        bm = bmesh_module.new()
        bm.from_mesh(mesh_copy)
        bm.faces.ensure_lookup_table()
        faces_to_delete = [
            face for face in bm.faces if face.index not in selected_faces
        ]
        if faces_to_delete:
            bmesh_module.ops.delete(bm, geom=faces_to_delete, context="FACES")
        loose_verts = [vert for vert in bm.verts if not vert.link_faces]
        if loose_verts:
            bmesh_module.ops.delete(bm, geom=loose_verts, context="VERTS")
        bm.to_mesh(mesh_copy)
        bm.free()
        mesh_copy.update()
        return mesh_copy

    def _link_to_source_collection(self, context, source, obj):
        target_collection = (
            source.users_collection[0]
            if source.users_collection
            else context.collection
        )
        target_collection.objects.link(obj)

    def _copy_geo_properties(self, source, target):
        source_geo = getattr(source, "GEOPropertyGroup", None)
        target_geo = getattr(target, "GEOPropertyGroup", None)
        if source_geo is None or target_geo is None:
            return

        for prop in source_geo.bl_rna.properties:
            identifier = prop.identifier
            if identifier == "rna_type" or getattr(prop, "is_readonly", False):
                continue
            try:
                setattr(target_geo, identifier, getattr(source_geo, identifier))
            except Exception:
                pass


class OPCaptureRawVDFMatrix(bpy.types.Operator):
    bl_idname = "bz.capture_raw_vdf_matrix"
    bl_label = "Capture Raw VDF Matrix"
    bl_description = "Capture the active object's current transform into Raw VDF Matrix using export axis conventions"

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None:
            self.report({"ERROR"}, "No active object selected")
            return {"CANCELLED"}

        geo = getattr(obj, "GEOPropertyGroup", None)
        if geo is None:
            self.report({"ERROR"}, "Active object has no GEO properties")
            return {"CANCELLED"}

        euler = mathutils.Euler((0.0, math.radians(45.0), 0.0), "YZX")
        euler[:] = obj.rotation_euler.x, obj.rotation_euler.z, obj.rotation_euler.y
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
        raw = (
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
        )

        geo.RawVDFMatrix = raw
        geo.UseRawVDFMatrix = True

        self.report({"INFO"}, "Captured current transform into Raw VDF Matrix")
        return {"FINISHED"}


"""
Animation UIList - Used to make a list of all the animation elements, allowing you to click them to select them.
Animation Panel - Used to hold the UIList, buttons, and all the good stuff including the "animation editor"
"""


class AnimationUIList(bpy.types.UIList):
    bl_idname = "SCENE_UL_Battlezone_ANIM_Element"

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        row = layout.row(align=True)
        row.label(text=f"Slot {item.Index}", icon="ANIM")
        row.separator()
        row.label(text=f"Frames: {item.Start}..{item.Start + item.Length}")
        row.label(text=f"Loops: {item.Loop}")
        row.label(text=f"Speed: {item.Speed:g}")


# And now we can use this list everywhere in Blender. Here is a small example panel.
class AnimationPanel(bpy.types.Panel):
    bl_label = "Battlezone Animations"
    bl_idname = "SCENE_PT_Battlezone_ANIM"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"

    def draw(self, context):
        layout = self.layout

        scene = context.scene

        box = layout.box()
        box.label(text="Animation Elements")
        _draw_legacy_animation_limits_box(box)

        row = box.row()
        col = row.column()
        col.template_list(
            "SCENE_UL_Battlezone_ANIM_Element",
            "",
            scene,
            "AnimationCollection",
            scene,
            "CurAnimation",
        )

        col = row.column(align=True)
        col.operator("bz.createanimelement", icon="ADD", text="")
        col.operator("bz.deleteanimelement", icon="REMOVE", text="")
        col.separator()
        op = col.operator("bz.moveanimelement", icon="TRIA_UP", text="")
        op.direction = "UP"
        op = col.operator("bz.moveanimelement", icon="TRIA_DOWN", text="")
        op.direction = "DOWN"

        actions = layout.box()
        actions.label(text="Actions")
        row = actions.row(align=True)
        row.operator("bz.createanimelement", text="New Element", icon="ADD")
        row.operator("bz.duplicateanimelement", text="Duplicate", icon="DUPLICATE")
        row.operator("bz.deleteanimelement", text="Delete Element", icon="REMOVE")
        actions.operator_menu_enum(
            "bz.apply_animation_preset", "preset", text="Add Preset Slots", icon="PRESET"
        )

        editor = layout.box()
        editor.label(text="Selected Element")
        if len(scene.AnimationCollection) > 0 and scene.CurAnimation < len(
            scene.AnimationCollection
        ):
            item = scene.AnimationCollection[scene.CurAnimation]
            editor.prop(item, "Index")
            _draw_wrapped_label(
                editor, _get_animation_index_hint(item.Index), icon="INFO", width=68
            )
            timing = editor.box()
            timing.label(text="Timing")
            timing.prop(item, "Start")
            timing.prop(item, "Length")
            timing.prop(item, "Loop")
            timing.prop(item, "Speed")

            advanced = editor.box()
            advanced.label(text="Affected Mesh/GEO Slots")
            advanced.prop(item, "UseCustomUnknownGeoMask")
            if item.UseCustomUnknownGeoMask:
                advanced.label(
                    text="One value per legacy GEO slot. Leave off for automatic defaults.",
                    icon="INFO",
                )
                for i in range(0, 32, 8):
                    row = advanced.row(align=True)
                    row.prop(item, "UnknownGeoMask", index=i + 0, text=str(i + 0))
                    row.prop(item, "UnknownGeoMask", index=i + 1, text=str(i + 1))
                    row.prop(item, "UnknownGeoMask", index=i + 2, text=str(i + 2))
                    row.prop(item, "UnknownGeoMask", index=i + 3, text=str(i + 3))
                    row.prop(item, "UnknownGeoMask", index=i + 4, text=str(i + 4))
                    row.prop(item, "UnknownGeoMask", index=i + 5, text=str(i + 5))
                    row.prop(item, "UnknownGeoMask", index=i + 6, text=str(i + 6))
                    row.prop(item, "UnknownGeoMask", index=i + 7, text=str(i + 7))
        else:
            editor.label(text="No animation element selected.", icon="INFO")

        # Animation index reference helper
        ref_box = layout.box()
        ref_box.label(text="Animation Index Reference")
        _draw_wrapped_label(
            ref_box,
            "Index meaning depends on classLabel. Use this as a quick lookup.",
            width=68,
        )
        ref_box.operator(
            "bz.show_anim_index_reference",
            text="Show Animation Index Reference",
            icon="INFO",
        )


class BZ98TOOLS_OT_apply_export_preset(bpy.types.Operator):
    bl_idname = "bz.apply_export_preset"
    bl_label = "Apply Export Preset"
    bl_description = (
        "Apply a built-in or saved export preset to the active export dialog"
    )

    export_kind: Any = StringProperty(name="Export Kind", default="")
    preset_key: Any = StringProperty(name="Preset Key", default="")
    custom_label: Any = StringProperty(name="Custom Label", default="")

    def execute(self, context):
        operator = _get_active_export_operator(context, self.export_kind)
        if operator is None:
            self.report({"ERROR"}, "No matching export dialog is active.")
            return {"CANCELLED"}

        if self.custom_label:
            values = _load_custom_export_preset(self.export_kind, self.custom_label)
            preset_label = self.custom_label
        else:
            values = _get_builtin_export_preset(self.export_kind, self.preset_key)
            preset_label = self.preset_key

        if values is None:
            self.report({"ERROR"}, "Preset could not be loaded.")
            return {"CANCELLED"}

        _apply_export_preset_values(operator, self.export_kind, values)
        self.report({"INFO"}, f"Applied preset '{preset_label}'.")
        return {"FINISHED"}


class BZ98TOOLS_OT_save_export_preset(bpy.types.Operator):
    bl_idname = "bz.save_export_preset"
    bl_label = "Save Export Preset"
    bl_description = "Save the current export settings as a reusable preset"

    export_kind: Any = StringProperty(name="Export Kind", default="")
    preset_name: Any = StringProperty(name="Preset Name", default="")

    def invoke(self, context, event):
        if not self.preset_name:
            self.preset_name = f"{self.export_kind.lower()}_preset"
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "preset_name")

    def execute(self, context):
        operator = _get_active_export_operator(context, self.export_kind)
        if operator is None:
            self.report({"ERROR"}, "No matching export dialog is active.")
            return {"CANCELLED"}

        preset_name = (self.preset_name or "").strip()
        if not preset_name:
            self.report({"ERROR"}, "Preset name cannot be empty.")
            return {"CANCELLED"}

        preset_dir = get_export_preset_dir(self.export_kind)
        os.makedirs(preset_dir, exist_ok=True)
        filename = _normalize_preset_filename(preset_name) + ".json"
        path = os.path.join(preset_dir, filename)
        payload = {
            "label": preset_name,
            "export_kind": self.export_kind,
            "values": _serialize_export_operator(operator, self.export_kind),
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

        self.report({"INFO"}, f"Saved preset '{preset_name}'.")
        return {"FINISHED"}


class BZ98TOOLS_OT_delete_export_preset(bpy.types.Operator):
    bl_idname = "bz.delete_export_preset"
    bl_label = "Delete Export Preset"
    bl_description = "Delete a saved export preset"

    export_kind: Any = StringProperty(name="Export Kind", default="")
    custom_label: Any = StringProperty(name="Custom Label", default="")

    def execute(self, context):
        custom_label = (self.custom_label or "").strip()
        if not custom_label:
            self.report({"ERROR"}, "No preset name was provided.")
            return {"CANCELLED"}

        for entry in _list_custom_export_presets(self.export_kind):
            if entry["label"] != custom_label:
                continue
            try:
                os.remove(entry["path"])
            except OSError as exc:
                self.report({"ERROR"}, f"Could not delete preset: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, f"Deleted preset '{custom_label}'.")
            return {"FINISHED"}

        self.report({"ERROR"}, f"Could not find preset '{custom_label}'.")
        return {"CANCELLED"}


class BZ98TOOLS_MT_geo_export_presets(bpy.types.Menu):
    bl_idname = "BZ98TOOLS_MT_geo_export_presets"
    bl_label = "GEO Presets"

    def draw(self, context):
        _draw_export_preset_menu_entries(self.layout, "GEO")


class BZ98TOOLS_MT_vdf_export_presets(bpy.types.Menu):
    bl_idname = "BZ98TOOLS_MT_vdf_export_presets"
    bl_label = "VDF Presets"

    def draw(self, context):
        _draw_export_preset_menu_entries(self.layout, "VDF")


class BZ98TOOLS_MT_sdf_export_presets(bpy.types.Menu):
    bl_idname = "BZ98TOOLS_MT_sdf_export_presets"
    bl_label = "SDF Presets"

    def draw(self, context):
        _draw_export_preset_menu_entries(self.layout, "SDF")


class BZ98TOOLS_MT_geo_delete_export_preset(bpy.types.Menu):
    bl_idname = "BZ98TOOLS_MT_geo_delete_export_preset"
    bl_label = "Delete GEO Preset"

    def draw(self, context):
        _draw_export_preset_menu_entries(self.layout, "GEO", delete_mode=True)


class BZ98TOOLS_MT_vdf_delete_export_preset(bpy.types.Menu):
    bl_idname = "BZ98TOOLS_MT_vdf_delete_export_preset"
    bl_label = "Delete VDF Preset"

    def draw(self, context):
        _draw_export_preset_menu_entries(self.layout, "VDF", delete_mode=True)


class BZ98TOOLS_MT_sdf_delete_export_preset(bpy.types.Menu):
    bl_idname = "BZ98TOOLS_MT_sdf_delete_export_preset"
    bl_label = "Delete SDF Preset"

    def draw(self, context):
        _draw_export_preset_menu_entries(self.layout, "SDF", delete_mode=True)


"""
Import/Export MENUs/UIs for GEO/VDF
Used for importing/exporting
"""


def _draw_legacy_import_options(layout, operator):
    if hasattr(operator, "ImportAnimations"):
        layout.prop(operator, "ImportAnimations")
    if hasattr(operator, "PreserveFaceColors"):
        layout.prop(operator, "PreserveFaceColors")
    if hasattr(operator, "ImportMapTextures"):
        layout.prop(operator, "ImportMapTextures")
        texture_box = layout.box()
        texture_box.enabled = bool(operator.ImportMapTextures)
        texture_box.prop(operator, "MapTextureDirectory")
        texture_box.prop(operator, "MapTextureZFS")
        _draw_wrapped_label(
            texture_box,
            "Paste paths here. Blender cannot open a second folder/file picker while the import file browser is already open. Texture lookup checks the import folder first, then this folder, then extracts missing .map files from the selected ZFS.",
            icon="INFO",
            width=62,
        )


class ImportGEO(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.geo"
    bl_label = "Import GEO"
    bl_description = "Import a Battlezone .GEO file"
    bl_options = {"PRESET", "UNDO"}
    filename_ext = ".geo"
    filter_glob: Any = StringProperty(
        default="*.geo",
        options={"HIDDEN"},
    )

    PreserveFaceColors: Any = BoolProperty(
        name="Preserve Face Colors",
        description="Preserves all colors from the GEOs by making a material for every single face! Each face material will preserve the original GEO color.",
        default=True,
    )

    ImportMapTextures: Any = BoolProperty(
        name="Auto-load .map textures",
        description="Automatically load matching .map files for materials and hook them up as image textures.",
        default=True,
    )

    MapTextureDirectory: Any = StringProperty(
        name=".MAP Texture Folder",
        description="Optional folder path to search for referenced .map textures when importing. Paste the path here; Blender cannot open a nested folder picker inside the import dialog.",
        default="",
    )

    MapTextureZFS: Any = StringProperty(
        name=".MAP Texture ZFS",
        description="Optional stock ZFS archive path to extract missing referenced .map textures from during import. Paste the path here; Blender cannot open a nested file picker inside the import dialog.",
        default="",
    )

    def draw(self, context):
        _draw_legacy_import_options(self.layout, self)

    def execute(self, context):
        from . import import_geo

        # Don't pass a ton of stupid stuff to our load function. Who even cares!
        keywords = self.as_keywords(
            ignore=(
                "axis_forward",
                "axis_up",
                "filter_glob",
                "split_mode",
            )
        )

        return import_geo.load(context, **keywords)


class ImportVDF(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.vdf"
    bl_label = "Import VDF"
    bl_description = "Import a Battlezone .VDF file"
    bl_options = {"PRESET", "UNDO"}
    filename_ext = ".vdf"
    filter_glob: Any = StringProperty(
        default="*.vdf",
        options={"HIDDEN"},
    )

    ImportAnimations: Any = BoolProperty(
        name="Import Animations",
        description="Import Animations for the VDF",
        default=True,
    )

    PreserveFaceColors: Any = BoolProperty(
        name="Preserve Face Colors",
        description="Preserves all colors from the GEOs by making a material for every single face! Each face material will preserve the original GEO color",
        default=True,
    )

    ImportMapTextures: Any = BoolProperty(
        name="Auto-load .map textures",
        description="Automatically load matching .map files for materials and hook them up as image textures.",
        default=True,
    )

    MapTextureDirectory: Any = StringProperty(
        name=".MAP Texture Folder",
        description="Optional folder path to search for referenced .map textures when importing. Paste the path here; Blender cannot open a nested folder picker inside the import dialog.",
        default="",
    )

    MapTextureZFS: Any = StringProperty(
        name=".MAP Texture ZFS",
        description="Optional stock ZFS archive path to extract missing referenced .map textures from during import. Paste the path here; Blender cannot open a nested file picker inside the import dialog.",
        default="",
    )

    def draw(self, context):
        _draw_legacy_import_options(self.layout, self)

    def execute(self, context):
        from . import import_vdf

        # Don't pass a ton of stupid stuff to our load function. Who even cares!
        keywords = self.as_keywords(
            ignore=(
                "axis_forward",
                "axis_up",
                "filter_glob",
                "split_mode",
            )
        )
        return import_vdf.load(context, **keywords)


class ImportSDF(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.sdf"
    bl_label = "Import SDF"
    bl_description = "Import a Battlezone .SDF file"
    bl_options = {"PRESET", "UNDO"}
    filename_ext = ".sdf"
    filter_glob: Any = StringProperty(
        default="*.sdf",
        options={"HIDDEN"},
    )

    ImportAnimations: Any = BoolProperty(
        name="Import Animations",
        description="Import Animations for the VDF",
        default=True,
    )

    PreserveFaceColors: Any = BoolProperty(
        name="Preserve Face Colors",
        description="Preserves all colors from the GEOs by making a material for every single face! Each face material will preserve the original GEO color",
        default=True,
    )

    ImportMapTextures: Any = BoolProperty(
        name="Auto-load .map textures",
        description="Automatically load matching .map files for materials and hook them up as image textures.",
        default=True,
    )

    MapTextureDirectory: Any = StringProperty(
        name=".MAP Texture Folder",
        description="Optional folder path to search for referenced .map textures when importing. Paste the path here; Blender cannot open a nested folder picker inside the import dialog.",
        default="",
    )

    MapTextureZFS: Any = StringProperty(
        name=".MAP Texture ZFS",
        description="Optional stock ZFS archive path to extract missing referenced .map textures from during import. Paste the path here; Blender cannot open a nested file picker inside the import dialog.",
        default="",
    )

    def draw(self, context):
        _draw_legacy_import_options(self.layout, self)

    def execute(self, context):
        from . import import_sdf

        # Don't pass a ton of stupid stuff to our load function. Who even cares!
        keywords = self.as_keywords(
            ignore=(
                "axis_forward",
                "axis_up",
                "filter_glob",
                "split_mode",
            )
        )
        return import_sdf.load(context, **keywords)


class ExportGEO(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.geo"
    bl_label = "Export GEO"
    bl_description = "Export a Battlezone .GEO file"
    bl_options = {"PRESET", "UNDO"}

    filename_ext = ".geo"

    filter_glob: Any = StringProperty(
        default="*.geo",
        options={"HIDDEN"},
    )

    face_plane_mode: Any = EnumProperty(
        name="Face Plane Export",
        description="Experimental handling for the four per-face GEO plane floats",
        items=GEO_FACE_PLANE_MODE_ITEMS,
        default="CURRENT",
    )

    # Ogre auto-port toggle
    auto_port_ogre: Any = BoolProperty(
        name="Also Create Redux Files",
        description=(
            "After exporting legacy files, also generate Redux .mesh/.skeleton/.material/.dds files."
        ),
        default=False,
    )

    # ---------- OGRE shared options ----------

    ogre_name: Any = StringProperty(
        name="Model Name",
        description="Porter --name. Name to give the final Redux model files; leave blank to use the source name.",
        default="",
    )

    ogre_suffix: Any = StringProperty(
        name="Material Suffix",
        description="Porter --suffix. Suffix appended to material file names.",
        default="_port",
    )

    ogre_flat_colors: Any = BoolProperty(
        name="Flat Colors",
        description="Porter --flatcolors. Force flat per-face color texturing.",
        default=False,
    )

    ogre_normal_mode: Any = EnumProperty(
        name="Normal Porter Mode",
        description="Porter --normalmode. Specifies how to handle normals.",
        items=NORMAL_MODE_ITEMS,
        default="CORRECT",
    )

    ogre_bounds_mult: Any = FloatVectorProperty(
        name="Bounds Scale",
        description="Porter --boundsmult. Scale factors for the mesh bounds (X, Y, Z).",
        size=3,
        default=(1.0, 1.0, 1.0),
        subtype="XYZ",
    )

    ogre_act_path: Any = StringProperty(
        name="ACT Palette",
        description="Porter --act. Optional .act palette file for indexed .map textures.",
        default="",
        # subtype='FILE_PATH',
    )

    ogre_config_path: Any = StringProperty(
        name="Config File",
        description="Porter --config. Optional config file that defines default paths for the porter.",
        default="",
        # subtype='FILE_PATH',
    )

    ogre_only_once: Any = BoolProperty(
        name="Skip Existing",
        description="Porter --onlyonce. Do not port files when the mesh is already in the target directory.",
        default=False,
    )

    ogre_nowrite: Any = BoolProperty(
        name="Dry Run",
        description="Porter --nowrite. Suppress file writing for testing.",
        default=False,
    )

    ogre_skip_unchanged: Any = BoolProperty(
        name="Skip Unchanged Outputs",
        description=(
            "Compare generated Redux .dds/.material/.mesh/.skeleton data with existing files "
            "and leave matching files untouched."
        ),
        default=True,
    )

    ogre_profile_export: Any = BoolProperty(
        name="Profile Redux Export",
        description="Print Redux export timing, resource cache, and output write statistics to the console.",
        default=False,
    )

    ogre_dest_dir: Any = StringProperty(
        name="Output Folder",
        description="Porter --dest. Destination directory for Redux files; leave blank to use export folder.",
        default="",
        # subtype='DIR_PATH',
    )

    def execute(self, context):
        from . import export_geo
        from . import ogre_autoport

        issues = bz_validation.collect_legacy_validation_issues(
            context, export_mode="GEO"
        )
        _store_validation_results(context.scene, issues)
        counts = _get_validation_counts(context.scene, export_mode="GEO")
        if counts["ERROR"] > 0:
            self.report(
                {"ERROR"},
                f"GEO export blocked by {counts['ERROR']} validation errors. Run 'Validate Battlezone Scene' for details.",
            )
            return {"CANCELLED"}
        if counts["WARNING"] > 0:
            self.report(
                {"WARNING"},
                f"GEO export validation found {counts['WARNING']} warnings.",
            )

        keywords = self.as_keywords(
            ignore=(
                "axis_forward",
                "axis_up",
                "filter_glob",
                "split_mode",
                "check_existing",
                "relpath",
                "auto_port_ogre",
                "ogre_name",
                "ogre_suffix",
                "ogre_flat_colors",
                "ogre_bounds_mult",
                "ogre_act_path",
                "ogre_config_path",
                "ogre_only_once",
                "ogre_nowrite",
                "ogre_skip_unchanged",
                "ogre_profile_export",
                "ogre_dest_dir",
            )
        )

        result = export_geo.export(context, **keywords)

        if result == {"FINISHED"} and self.auto_port_ogre:
            opts = {
                "name": self.ogre_name.strip() or None,
                "suffix": self.ogre_suffix.strip() or "_port",
                "flat_colors": self.ogre_flat_colors,
                "normal_mode": self.ogre_normal_mode,
                "bounds_mult": list(self.ogre_bounds_mult),
                "act_path": self.ogre_act_path.strip() or None,
                "config_path": self.ogre_config_path.strip() or None,
                "only_once": self.ogre_only_once,
                "nowrite": self.ogre_nowrite,
                "skip_unchanged": self.ogre_skip_unchanged,
                "profile_export": self.ogre_profile_export,
                "dest_dir": self.ogre_dest_dir.strip() or None,
            }
            ogre_autoport.auto_port_bz98_to_ogre(self.filepath, opts)

        return result

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        _draw_legacy_export_mode_box(layout, self.auto_port_ogre)
        _draw_export_preset_box(layout, "GEO")

        core = layout.box()
        core.label(text="Core Export")
        active_obj = getattr(
            getattr(context.view_layer, "objects", None), "active", None
        )
        if active_obj is None:
            core.label(text="No active object selected.", icon="ERROR")
        else:
            core.label(text=f"Active Object: {active_obj.name}", icon="OBJECT_DATA")
            core.label(text="Only the active mesh object is exported.", icon="INFO")

        _draw_geo_face_plane_export_box(layout, self)

        _draw_orientation_reference_box(
            layout, mode="LEGACY", auto_port_enabled=self.auto_port_ogre
        )

        _draw_validation_summary_box(layout, scene, export_mode="GEO")

        port = layout.box()
        port.label(text="Redux Output")
        port.prop(self, "auto_port_ogre")
        if self.auto_port_ogre:
            _draw_shared_autoport_options(port, self)


class ExportVDF(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.vdf"
    bl_label = "Export VDF"
    bl_description = "Export a Battlezone .VDF file"
    bl_options = {"PRESET", "UNDO"}

    filename_ext = ".vdf"

    filter_glob: Any = StringProperty(
        default="*.vdf",
        options={"HIDDEN"},
    )

    ExportAnimations: Any = BoolProperty(
        name="Export Animations",
        description="Export legacy VDF object rotation/location keys only; armatures, skin weights, shape keys, and mesh deformation are not supported",
        default=True,
    )

    ExportVDFOnly: Any = BoolProperty(
        name="Skip GEO File Export",
        description="Only write the VDF container and keep existing referenced GEO files.",
        default=False,
    )

    face_plane_mode: Any = EnumProperty(
        name="Face Plane Export",
        description="Experimental handling for referenced GEO files written by this VDF export",
        items=GEO_FACE_PLANE_MODE_ITEMS,
        default="CURRENT",
    )

    # NEW: Ogre auto-port checkbox
    auto_port_ogre: Any = BoolProperty(
        name="Also Create Redux Files",
        description=(
            "After exporting legacy files, also generate Redux .mesh/.skeleton/.material/.dds files."
        ),
        default=False,
    )

    ogre_name: Any = StringProperty(
        name="Model Name",
        description="Porter --name. Name to give the final Redux model files; leave blank to use the source name.",
        default="",
    )

    ogre_suffix: Any = StringProperty(
        name="Material Suffix",
        description="Porter --suffix. Suffix appended to material file names.",
        default="_port",
    )

    ogre_flat_colors: Any = BoolProperty(
        name="Flat Colors",
        description="Porter --flatcolors. Force flat per-face color texturing.",
        default=False,
    )

    ogre_bounds_mult: Any = FloatVectorProperty(
        name="Bounds Scale",
        description="Porter --boundsmult. Scale factors for the mesh bounds (X, Y, Z).",
        size=3,
        default=(1.0, 1.0, 1.0),
        subtype="XYZ",
    )

    ogre_act_path: Any = StringProperty(
        name="ACT Palette",
        description="Porter --act. Optional .act palette file for indexed .map textures.",
        default="",
        # subtype='FILE_PATH',
    )

    ogre_config_path: Any = StringProperty(
        name="Config File",
        description="Porter --config. Optional config file that defines default paths for the porter.",
        default="",
        # subtype='FILE_PATH',
    )

    ogre_only_once: Any = BoolProperty(
        name="Skip Existing",
        description="Porter --onlyonce. Do not port files when the mesh is already in the target directory.",
        default=False,
    )

    ogre_nowrite: Any = BoolProperty(
        name="Dry Run",
        description="Porter --nowrite. Suppress file writing for testing.",
        default=False,
    )

    ogre_skip_unchanged: Any = BoolProperty(
        name="Skip Unchanged Outputs",
        description=(
            "Compare generated Redux .dds/.material/.mesh/.skeleton data with existing files "
            "and leave matching files untouched."
        ),
        default=True,
    )

    ogre_profile_export: Any = BoolProperty(
        name="Profile Redux Export",
        description="Print Redux export timing, resource cache, and output write statistics to the console.",
        default=False,
    )

    ogre_dest_dir: Any = StringProperty(
        name="Output Folder",
        description="Porter --dest. Destination directory for Redux files; leave blank to use export folder.",
        default="",
        # subtype='DIR_PATH',
    )

    # ---------- VDF-only OGRE options ----------

    ogre_headlights: Any = BoolProperty(
        name="Headlights",
        description="Porter --headlights. Enable automatic creation of headlights.",
        default=False,
    )

    ogre_person_mode: Any = EnumProperty(
        name="Person Mode",
        description="Porter --person. Force this object to be flagged as a person.",
        items=TERNARY_ITEMS,
        default="AUTO",
    )

    ogre_turret_mode: Any = EnumProperty(
        name="Turret Mode",
        description="Porter --turret. Force this object to be flagged as a turret.",
        items=TERNARY_ITEMS,
        default="AUTO",
    )

    ogre_cockpit_mode: Any = EnumProperty(
        name="Cockpit Mode",
        description="Porter --cockpit. Force creation or suppression of separate cockpit model files.",
        items=TERNARY_ITEMS,
        default="AUTO",
    )

    ogre_skeletalanims_mode: Any = EnumProperty(
        name="Skeletal Animations",
        description="Porter --skeletalanims. Force creation or suppression of skeletal person animations.",
        items=TERNARY_ITEMS,
        default="AUTO",
    )

    ogre_scope_mode: Any = EnumProperty(
        name="Scope",
        description="Porter --scope. Force creation or suppression of a person sniper scope.",
        items=TERNARY_ITEMS,
        default="AUTO",
    )

    ogre_scope_type: Any = EnumProperty(
        name="Scope Type",
        description="Porter --scopetype. Sniper scope type.",
        items=[
            ("AUTO", "Auto", "Auto choose FIXED/GEOMETRY based on scope texture"),
            ("FIXED", "Fixed", "Square fixed on screen (classic)"),
            ("ATTACHED", "Attached", "Square attached to the gun model"),
            ("GEOMETRY", "Geometry", "Scope textured directly on geometry"),
        ],
        default="AUTO",
    )

    ogre_scope_nation: Any = StringProperty(
        name="Scope Nation",
        description="Porter --scopenation. Nation string for fixed scope placement, such as 'soviet'.",
        default="",
    )

    ogre_scope_screen: Any = FloatVectorProperty(
        name="Scope Screen",
        description="Porter --scopescreen. Camera-relative X, Y, Z, size, and behind-distance for fixed scope.",
        size=5,
        default=(0.0, 0.0, 0.0, 1.0, 0.0),
    )

    ogre_scope_gun: Any = StringProperty(
        name="Scope Gun",
        description="Porter --scopegun. Gun GEO name to attach scope to when using attached scope type.",
        default="",
    )

    ogre_scope_transform: Any = FloatVectorProperty(
        name="Scope Transform",
        description="Porter --scopetransform. Gun-relative transform: right, up, front, position.",
        size=12,
        default=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0),
    )

    ogre_scope_texture: Any = StringProperty(
        name="Scope Texture",
        description="Porter --scopetexture. Texture name to replace with scope material when using geometry scope.",
        default="__scope",
    )

    ogre_no_pov_rots: Any = BoolProperty(
        name="No POV Rotations",
        description="Porter --nopovrots. Remove POV rotation keys from directional movement animations.",
        default=False,
    )

    ogre_stabilize_walker_cockpit: Any = BoolProperty(
        name="Experimental Walker Cockpit Stabilizer",
        description=(
            "Porter --stabilizewalkercockpit. In separate cockpit Redux output, "
            "emit cockpit and POV bones as model-space roots so walker body animation "
            "does not shake the first-person cockpit."
        ),
        default=False,
    )

    def execute(self, context):
        from . import export_vdf
        from . import ogre_autoport

        issues = bz_validation.collect_legacy_validation_issues(
            context, export_mode="VDF"
        )
        _store_validation_results(context.scene, issues)
        counts = _get_validation_counts(context.scene, export_mode="VDF")
        if counts["ERROR"] > 0:
            self.report(
                {"ERROR"},
                f"VDF export blocked by {counts['ERROR']} validation errors. Run 'Validate Battlezone Scene' for details.",
            )
            return {"CANCELLED"}
        if counts["WARNING"] > 0:
            self.report(
                {"WARNING"},
                f"VDF export validation found {counts['WARNING']} warnings.",
            )
        if (
            self.auto_port_ogre
            and self.ogre_headlights
            and not _scene_has_headlight_geo(context.scene)
        ):
            self.report(
                {"WARNING"},
                "Headlights are enabled for Redux export, but no GEO Type 38 HEADLIGHT_MASK object was found.",
            )

        # Don't pass OGRE UI options to the VDF exporter
        keywords = self.as_keywords(
            ignore=(
                "axis_forward",
                "axis_up",
                "filter_glob",
                "split_mode",
                "check_existing",
                "relpath",
                "auto_port_ogre",
                "ogre_name",
                "ogre_suffix",
                "ogre_flat_colors",
                "ogre_bounds_mult",
                "ogre_act_path",
                "ogre_config_path",
                "ogre_only_once",
                "ogre_nowrite",
                "ogre_skip_unchanged",
                "ogre_profile_export",
                "ogre_dest_dir",
                "ogre_headlights",
                "ogre_person_mode",
                "ogre_turret_mode",
                "ogre_cockpit_mode",
                "ogre_skeletalanims_mode",
                "ogre_scope_mode",
                "ogre_scope_type",
                "ogre_scope_nation",
                "ogre_scope_screen",
                "ogre_scope_gun",
                "ogre_scope_transform",
                "ogre_scope_texture",
                "ogre_no_pov_rots",
                "ogre_stabilize_walker_cockpit",
            )
        )

        result = export_vdf.export(context, **keywords)

        if result == {"FINISHED"} and self.auto_port_ogre:
            opts = {
                # shared
                "name": self.ogre_name.strip() or None,
                "suffix": self.ogre_suffix.strip() or "_port",
                "flat_colors": self.ogre_flat_colors,
                "normal_mode": self.ogre_normal_mode,
                "bounds_mult": list(self.ogre_bounds_mult),
                "act_path": self.ogre_act_path.strip() or None,
                "config_path": self.ogre_config_path.strip() or None,
                "only_once": self.ogre_only_once,
                "nowrite": self.ogre_nowrite,
                "skip_unchanged": self.ogre_skip_unchanged,
                "profile_export": self.ogre_profile_export,
                "dest_dir": self.ogre_dest_dir.strip() or None,
                # VDF-only
                "headlights": self.ogre_headlights,
                "person_mode": self.ogre_person_mode,
                "turret_mode": self.ogre_turret_mode,
                "cockpit_mode": self.ogre_cockpit_mode,
                "skeletalanims_mode": self.ogre_skeletalanims_mode,
                "scope_mode": self.ogre_scope_mode,
                "scope_type": self.ogre_scope_type,
                "scope_nation": self.ogre_scope_nation.strip() or None,
                "scope_screen": list(self.ogre_scope_screen),
                "scope_gun": self.ogre_scope_gun.strip() or None,
                "scope_transform": list(self.ogre_scope_transform),
                "scope_texture": self.ogre_scope_texture.strip() or None,
                "no_pov_rots": self.ogre_no_pov_rots,
                "stabilize_walker_cockpit": self.ogre_stabilize_walker_cockpit,
            }

            ogre_autoport.auto_port_bz98_to_ogre(self.filepath, opts)

        return result

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        _draw_legacy_export_mode_box(layout, self.auto_port_ogre)
        _draw_export_preset_box(layout, "VDF")

        core = layout.box()
        core.label(text="Core Export")
        core.label(text="Exports scene GEO slots plus VDF container data.", icon="INFO")

        _draw_orientation_reference_box(
            layout, mode="LEGACY", auto_port_enabled=self.auto_port_ogre
        )

        anim_box = layout.box()
        anim_box.label(text="Animations")
        anim_box.prop(self, "ExportAnimations")
        anim_count = len(getattr(scene, "AnimationCollection", []))
        anim_box.label(text=f"Scene animation elements: {anim_count}")
        _draw_legacy_animation_limits_box(anim_box)

        legacy = layout.box()
        legacy.label(text="Legacy Output")
        legacy.prop(self, "ExportVDFOnly")
        if not self.ExportVDFOnly:
            legacy.label(
                text="Referenced GEO files in the export folder may be overwritten.",
                icon="ERROR",
            )
            _draw_geo_face_plane_export_box(layout, self)

        _draw_validation_summary_box(layout, scene, export_mode="VDF")

        port = layout.box()
        port.label(text="Redux Output")
        port.prop(self, "auto_port_ogre")
        if self.auto_port_ogre:
            _draw_shared_autoport_options(port, self)
            _draw_vdf_autoport_options(port, self, scene=scene)


class ExportSDF(bpy.types.Operator, ExportHelper):
    """Exports a Battlezone SDF file"""

    bl_idname = "export_scene.sdf"
    bl_label = "Export SDF"
    bl_description = (
        "Export a Battlezone SDF structure file (.sdf). "
        "Note: Must have at least one animation defined in the Scene's Animation Collection "
        "for animation data to be included."
    )

    filename_ext = ".sdf"

    filter_glob: Any = StringProperty(
        default="*.sdf",
        options={"HIDDEN"},
        maxlen=255,
    )

    ExportAnimations: Any = BoolProperty(
        name="Export Animations",
        description="Export legacy SDF object rotation/location keys only; armatures, skin weights, shape keys, and mesh deformation are not supported",
        default=True,
    )

    ExportSDFOnly: Any = BoolProperty(
        name="Skip GEO File Export",
        description="Only write the SDF container and keep existing referenced GEO files.",
        default=False,
    )

    face_plane_mode: Any = EnumProperty(
        name="Face Plane Export",
        description="Experimental handling for referenced GEO files written by this SDF export",
        items=GEO_FACE_PLANE_MODE_ITEMS,
        default="CURRENT",
    )

    # NEW: Ogre auto-port toggle
    auto_port_ogre: Any = BoolProperty(
        name="Also Create Redux Files",
        description=(
            "After exporting legacy files, also generate Redux .mesh/.skeleton/.material/.dds files."
        ),
        default=False,
    )

    ogre_name: Any = StringProperty(
        name="Model Name",
        description="Porter --name. Name to give the final Redux model files; leave blank to use the source name.",
        default="",
    )

    ogre_suffix: Any = StringProperty(
        name="Material Suffix",
        description="Porter --suffix. Suffix appended to material file names.",
        default="_port",
    )

    ogre_flat_colors: Any = BoolProperty(
        name="Flat Colors",
        description="Porter --flatcolors. Force flat per-face color texturing.",
        default=False,
    )

    ogre_bounds_mult: Any = FloatVectorProperty(
        name="Bounds Scale",
        description="Porter --boundsmult. Scale factors for the mesh bounds (X, Y, Z).",
        size=3,
        default=(1.0, 1.0, 1.0),
        subtype="XYZ",
    )

    ogre_act_path: Any = StringProperty(
        name="ACT Palette",
        description="Porter --act. Optional .act palette file for indexed .map textures.",
        default="",
        # subtype='FILE_PATH',
    )

    ogre_config_path: Any = StringProperty(
        name="Config File",
        description="Porter --config. Optional config file that defines default paths for the porter.",
        default="",
        # subtype='FILE_PATH',
    )

    ogre_only_once: Any = BoolProperty(
        name="Skip Existing",
        description="Porter --onlyonce. Do not port files when the mesh is already in the target directory.",
        default=False,
    )

    ogre_nowrite: Any = BoolProperty(
        name="Dry Run",
        description="Porter --nowrite. Suppress file writing for testing.",
        default=False,
    )

    ogre_skip_unchanged: Any = BoolProperty(
        name="Skip Unchanged Outputs",
        description=(
            "Compare generated Redux .dds/.material/.mesh/.skeleton data with existing files "
            "and leave matching files untouched."
        ),
        default=True,
    )

    ogre_profile_export: Any = BoolProperty(
        name="Profile Redux Export",
        description="Print Redux export timing, resource cache, and output write statistics to the console.",
        default=False,
    )

    ogre_dest_dir: Any = StringProperty(
        name="Output Folder",
        description="Porter --dest. Destination directory for Redux files; leave blank to use export folder.",
        default="",
        # subtype='DIR_PATH',
    )

    def execute(self, context):
        from . import export_sdf
        from . import ogre_autoport

        issues = bz_validation.collect_legacy_validation_issues(
            context, export_mode="SDF"
        )
        _store_validation_results(context.scene, issues)
        counts = _get_validation_counts(context.scene, export_mode="SDF")
        if counts["ERROR"] > 0:
            self.report(
                {"ERROR"},
                f"SDF export blocked by {counts['ERROR']} validation errors. Run 'Validate Battlezone Scene' for details.",
            )
            return {"CANCELLED"}
        if counts["WARNING"] > 0:
            self.report(
                {"WARNING"},
                f"SDF export validation found {counts['WARNING']} warnings.",
            )

        # Warn if there are no animation definitions in the Scene
        anims = getattr(context.scene, "AnimationCollection", None)
        if self.ExportAnimations and (not anims or len(anims) == 0):
            self.report(
                {"WARNING"},
                "No animations defined in Scene.AnimationCollection — "
                "exported SDF will contain no ANIM data.",
            )

        keywords = self.as_keywords(
            ignore=(
                "axis_forward",
                "axis_up",
                "filter_glob",
                "split_mode",
                "check_existing",
                "relpath",
                "auto_port_ogre",
                "ogre_name",
                "ogre_suffix",
                "ogre_flat_colors",
                "ogre_bounds_mult",
                "ogre_act_path",
                "ogre_config_path",
                "ogre_only_once",
                "ogre_nowrite",
                "ogre_skip_unchanged",
                "ogre_profile_export",
                "ogre_dest_dir",
            )
        )

        result = export_sdf.export(context, **keywords)

        if result == {"FINISHED"} and self.auto_port_ogre:
            opts = {
                "name": self.ogre_name.strip() or None,
                "suffix": self.ogre_suffix.strip() or "_port",
                "flat_colors": self.ogre_flat_colors,
                "normal_mode": self.ogre_normal_mode,
                "bounds_mult": list(self.ogre_bounds_mult),
                "act_path": self.ogre_act_path.strip() or None,
                "config_path": self.ogre_config_path.strip() or None,
                "only_once": self.ogre_only_once,
                "nowrite": self.ogre_nowrite,
                "skip_unchanged": self.ogre_skip_unchanged,
                "profile_export": self.ogre_profile_export,
                "dest_dir": self.ogre_dest_dir.strip() or None,
            }
            ogre_autoport.auto_port_bz98_to_ogre(self.filepath, opts)

        return result

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        _draw_legacy_export_mode_box(layout, self.auto_port_ogre)
        _draw_export_preset_box(layout, "SDF")

        core = layout.box()
        core.label(text="Core Export")
        core.label(text="Exports scene GEO slots plus SDF container data.", icon="INFO")

        _draw_orientation_reference_box(
            layout, mode="LEGACY", auto_port_enabled=self.auto_port_ogre
        )

        anim_box = layout.box()
        anim_box.label(text="Animations")
        anim_box.prop(self, "ExportAnimations")
        anim_count = len(getattr(scene, "AnimationCollection", []))
        anim_box.label(text=f"Scene animation elements: {anim_count}")
        _draw_legacy_animation_limits_box(anim_box)

        legacy = layout.box()
        legacy.label(text="Legacy Output")
        legacy.prop(self, "ExportSDFOnly")
        if not self.ExportSDFOnly:
            legacy.label(
                text="Referenced GEO files in the export folder may be overwritten.",
                icon="ERROR",
            )
            _draw_geo_face_plane_export_box(layout, self)

        _draw_validation_summary_box(layout, scene, export_mode="SDF")

        port = layout.box()
        port.label(text="Redux Output")
        port.prop(self, "auto_port_ogre")
        if self.auto_port_ogre:
            _draw_shared_autoport_options(port, self)


class BZ98TOOLS_OT_import_bzr_mesh(bpy.types.Operator, ImportHelper):
    """Import BZR Mesh (Ogre .mesh)"""

    bl_idname = "import_scene.bz98_bzr_mesh"
    bl_label = "Import Battlezone Redux Mesh (.mesh)"
    bl_options = {"UNDO"}

    filename_ext = ".mesh"
    filter_glob: Any = StringProperty(
        default="*.mesh",
        options={"HIDDEN"},
    )

    xml_converter: Any = StringProperty(
        name="XML Converter",
        description="Path to OgreXMLConverter.exe",
        default=get_default_ogre_xml_converter(),
        # subtype='FILE_PATH',
    )

    keep_xml: Any = BoolProperty(
        name="Keep XML files",
        description="Keep intermediate .xml files instead of deleting them",
        default=False,
    )

    import_normals: Any = BoolProperty(
        name="Import Normals",
        default=True,
    )

    normal_mode: Any = EnumProperty(
        name="Normal Import Mode",
        items=(
            ("custom", "Custom", "Use custom split normals if possible"),
            ("auto", "Auto", "Let Blender recompute normals"),
        ),
        default="custom",
    )

    import_shapekeys: Any = BoolProperty(
        name="Import Shape Keys",
        default=True,
    )

    import_animations: Any = BoolProperty(
        name="Import Animations",
        default=False,
    )

    round_frames: Any = BoolProperty(
        name="Round Animation Frames",
        description="Round animation frames to whole frames and set scene FPS",
        default=True,
    )

    use_selected_skeleton: Any = BoolProperty(
        name="Use Selected Armature as Skeleton",
        description="If an armature is selected, map mesh weights to it",
        default=False,
    )

    import_materials: Any = BoolProperty(
        name="Import Materials",
        default=True,
    )

    def execute(self, context):
        # Local import to avoid circular import on addon init
        from .ogretools import OgreImport
        from .ogrefast import backend as ogre_backend

        xml_converter = self.xml_converter or None
        diagnostics = getattr(context.scene, "bz_import_diagnostics", None)
        if diagnostics is not None:
            diagnostics.clear()

        result = ogre_backend.import_mesh(
            self,
            context,
            self.filepath,
            legacy_handler=OgreImport.load,
            xml_converter=xml_converter,
            keep_xml=self.keep_xml,
            import_normals=self.import_normals,
            normal_mode=self.normal_mode,
            import_shapekeys=self.import_shapekeys,
            import_animations=self.import_animations,
            round_frames=self.round_frames,
            use_selected_skeleton=self.use_selected_skeleton,
            import_materials=self.import_materials,
        )
        if result == {"FINISHED"}:
            _add_import_diagnostic(
                context.scene,
                "INFO",
                "Orientation",
                "Redux mesh front",
                "Imported Redux .mesh vehicles use Blender -Y as the nose/front direction.",
            )
        return result

    def draw(self, context):
        layout = self.layout
        _draw_orientation_reference_box(layout, mode="REDUX")
        layout.prop(self, "xml_converter")
        layout.prop(self, "keep_xml")
        layout.separator()
        layout.prop(self, "import_normals")
        layout.prop(self, "normal_mode")
        layout.prop(self, "import_shapekeys")
        layout.prop(self, "import_animations")
        layout.prop(self, "round_frames")
        layout.prop(self, "use_selected_skeleton")
        layout.prop(self, "import_materials")


class BZ98TOOLS_OT_export_bzr_mesh(bpy.types.Operator, ExportHelper):
    """Export BZR Mesh (Ogre .mesh + .skeleton)"""

    bl_idname = "export_scene.bz98_bzr_mesh"
    bl_label = "Export Redux Mesh Only (.mesh)"
    bl_options = {"UNDO"}

    filename_ext = ".mesh"
    filter_glob: Any = StringProperty(
        default="*.mesh",
        options={"HIDDEN"},
    )

    xml_converter: Any = StringProperty(
        name="XML Converter",
        description="Path to OgreXMLConverter.exe",
        default=get_default_ogre_xml_converter(),
        subtype="FILE_PATH",
    )

    keep_xml: Any = BoolProperty(
        name="Keep XML files",
        description="Keep intermediate .xml instead of deleting them",
        default=False,
    )

    export_tangents: Any = BoolProperty(
        name="Export Tangents",
        default=True,
    )

    export_binormals: Any = BoolProperty(
        name="Export Binormals",
        default=True,
    )

    zero_tangents_binormals: Any = BoolProperty(
        name="Zero Tangents",
        description="Force tangents/binormals to zero (e.g. for black building meshes)",
        default=False,
    )

    export_colour: Any = BoolProperty(
        name="Export Vertex Colours",
        default=True,
    )

    tangent_parity: Any = BoolProperty(
        name="Export Tangent Parity",
        default=True,
    )

    apply_transform: Any = BoolProperty(
        name="Apply Object Transforms",
        default=True,
    )

    apply_modifiers: Any = BoolProperty(
        name="Apply Modifiers",
        default=True,
    )

    export_materials: Any = BoolProperty(
        name="Export Materials",
        default=True,
    )

    overwrite_material: Any = BoolProperty(
        name="Overwrite Materials",
        default=False,
    )

    copy_textures: Any = BoolProperty(
        name="Copy Textures",
        default=False,
    )

    export_skeleton: Any = BoolProperty(
        name="Export Skeleton",
        default=True,
    )

    export_poses: Any = BoolProperty(
        name="Shape Keys as Poses",
        default=True,
    )

    export_animation: Any = BoolProperty(
        name="Export Animations",
        default=False,
    )

    renormalize_weights: Any = BoolProperty(
        name="Renormalize Weights",
        default=True,
    )

    batch_export: Any = BoolProperty(
        name="Batch Selected",
        description="Export each selected object as its own .mesh file",
        default=False,
    )

    def execute(self, context):
        # Local import to avoid circular import on addon init
        from .ogretools import OgreExport
        from .ogrefast import backend as ogre_backend

        xml_converter = self.xml_converter or None

        return ogre_backend.export_mesh(
            self,
            context,
            self.filepath,
            legacy_handler=OgreExport.save,
            xml_converter=xml_converter,
            keep_xml=self.keep_xml,
            export_tangents=self.export_tangents,
            export_binormals=self.export_binormals,
            zero_tangents_binormals=self.zero_tangents_binormals,
            export_colour=self.export_colour,
            tangent_parity=self.tangent_parity,
            apply_transform=self.apply_transform,
            apply_modifiers=self.apply_modifiers,
            export_materials=self.export_materials,
            overwrite_material=self.overwrite_material,
            copy_textures=self.copy_textures,
            export_skeleton=self.export_skeleton,
            export_poses=self.export_poses,
            export_animation=self.export_animation,
            renormalize_weights=self.renormalize_weights,
            batch_export=self.batch_export,
        )

    def draw(self, context):
        layout = self.layout
        _draw_redux_only_export_mode_box(layout)
        _draw_orientation_reference_box(layout, mode="REDUX")
        layout.prop(self, "xml_converter")
        layout.prop(self, "keep_xml")
        layout.separator()
        col = layout.column()
        col.label(text="Geometry:")
        col.prop(self, "apply_transform")
        col.prop(self, "apply_modifiers")
        col.prop(self, "batch_export")

        layout.separator()
        col = layout.column()
        col.label(text="Tangents / Binormals:")
        col.prop(self, "export_tangents")
        col.prop(self, "export_binormals")
        col.prop(self, "zero_tangents_binormals")
        col.prop(self, "tangent_parity")

        layout.separator()
        col = layout.column()
        col.label(text="Vertex Data:")
        col.prop(self, "export_colour")

        layout.separator()
        col = layout.column()
        col.label(text="Skeleton / Animation:")
        col.prop(self, "export_skeleton")
        col.prop(self, "export_poses")
        col.prop(self, "export_animation")
        col.prop(self, "renormalize_weights")

        layout.separator()
        col = layout.column()
        col.label(text="Materials:")
        col.prop(self, "export_materials")
        col.prop(self, "overwrite_material")
        col.prop(self, "copy_textures")


def _get_selected_zfs_entry(scene):
    index = getattr(scene, "zfs_active_index", -1)
    if index < 0 or index >= len(scene.zfs_files):
        return None
    return scene.zfs_files[index]


def _get_zfs_entry_icon(item):
    if item.is_model:
        return "MESH_DATA"
    if item.ext in ZFS_TEXTURE_EXTENSIONS:
        return "FILE_IMAGE"
    return "FILE"


def _get_current_zfs_cache_dir(scene):
    active_zfs_path = getattr(scene, "active_zfs_path", "")
    cache_root = getattr(scene, "zfs_cache_dir", "") or get_default_zfs_cache_dir()
    return get_zfs_archive_cache_dir(active_zfs_path, cache_root)


def _open_path_in_shell(path):
    target_path = os.path.abspath(path)
    if hasattr(bpy.ops, "wm") and hasattr(bpy.ops.wm, "path_open"):
        bpy.ops.wm.path_open(filepath=target_path)
        return
    if hasattr(os, "startfile"):
        os.startfile(target_path)
        return
    raise RuntimeError("No supported path-open handler is available.")


class BZ98TOOLS_MT_import_menu(bpy.types.Menu):
    bl_idname = "TOPBAR_MT_bz98_import"
    bl_label = "Battlezone"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Legacy Formats")
        layout.operator(ImportGEO.bl_idname, text="Geometry (.geo)")
        layout.operator(ImportVDF.bl_idname, text="Vehicle Definition (.vdf)")
        layout.operator(ImportSDF.bl_idname, text="Structure Definition (.sdf)")
        layout.separator()
        layout.label(text="Redux Formats")
        layout.operator(
            BZ98TOOLS_OT_import_bzr_mesh.bl_idname, text="Redux Mesh (.mesh)"
        )
        layout.separator()
        layout.label(text="Map Tools")
        layout.operator("bzmapio.open_template", text="Open Map Template (.blend)")
        layout.operator("bzmapimport.data", text="Height Grid Terrain (.hg2)")
        layout.separator()
        layout.label(text="Archive Tools")
        layout.operator(BZ98TOOLS_OT_open_zfs.bl_idname, text="Open ZFS Archive (.zfs)")


class BZ98TOOLS_MT_export_menu(bpy.types.Menu):
    bl_idname = "TOPBAR_MT_bz98_export"
    bl_label = "Battlezone"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Legacy Formats")
        layout.operator(ExportGEO.bl_idname, text="Legacy Geometry (.geo)")
        layout.operator(ExportVDF.bl_idname, text="Legacy Vehicle (.vdf)")
        layout.operator(ExportSDF.bl_idname, text="Legacy Structure (.sdf)")
        layout.separator()
        layout.label(text="Redux Formats")
        layout.operator(
            BZ98TOOLS_OT_export_bzr_mesh.bl_idname, text="Redux Mesh Only (.mesh)"
        )


def menu_func_import(self, context):
    self.layout.menu(BZ98TOOLS_MT_import_menu.bl_idname, text="Battlezone")


def menu_func_export(self, context):
    self.layout.menu(BZ98TOOLS_MT_export_menu.bl_idname, text="Battlezone")


Properties = [
    AnimationPropertyGroup,
    GEOPropertyGroup,
    SDFVDFPropertyGroup,
    MaterialPropertyGroup,
    ValidationIssuePropertyGroup,
    ImportDiagnosticPropertyGroup,
]


class ZFSFileEntry(bpy.types.PropertyGroup):
    name: Any = bpy.props.StringProperty(name="Name")
    ext: Any = bpy.props.StringProperty(name="Extension")
    is_model: Any = bpy.props.BoolProperty(name="Is Model", default=False)


def find_zfs_dependencies(reader, filename, extracted_files, temp_dir):
    """Recursively find and extract dependencies (GEOs and textures) from ZFS."""
    if filename.lower() in extracted_files:
        return

    path = reader.extract(filename, temp_dir)
    if not path:
        print(f"[BZ ZFS] Missing dependency '{filename}' in archive.")
        return
    extracted_files.add(filename.lower())

    ext = os.path.splitext(filename)[1].lower()

    if ext == ".vdf":
        from . import vdf_classes

        try:
            with open(path, "rb") as f:
                content = f.read()
                pos = 20  # Skip VDFHeader
                vdfc = vdf_classes.VDFCHeader()
                pos = vdfc.Read(content, pos)
                pos += 8  # Skip EXIT
                vgeo = vdf_classes.VGEOHeader()
                pos = vgeo.Read(content, pos)
                for _ in range(vgeo.geocount * 28):
                    geo = vdf_classes.GEOData()
                    geo.Read(content, pos)
                    pos += 100
                    if geo.name.lower() != "null":
                        find_zfs_dependencies(
                            reader, geo.name + ".geo", extracted_files, temp_dir
                        )
        except Exception as exc:
            print(f"[BZ ZFS] Failed to scan VDF dependencies for '{filename}': {exc}")

    elif ext == ".sdf":
        from . import sdf_classes

        try:
            with open(path, "rb") as f:
                content = f.read()
                pos = 20  # Skip SDFHeader
                sdfc = sdf_classes.SDFCHeader()
                pos = sdfc.Read(content, pos)
                sgeo = sdf_classes.SGEOHeader()
                pos = sgeo.Read(content, pos)
                for _ in range(sgeo.geocount):
                    geo = sdf_classes.GEOData()
                    geo.Read(content, pos)
                    pos += 120
                    if geo.name.lower() != "null":
                        find_zfs_dependencies(
                            reader, geo.name + ".geo", extracted_files, temp_dir
                        )
        except Exception as exc:
            print(f"[BZ ZFS] Failed to scan SDF dependencies for '{filename}': {exc}")

    elif ext == ".geo":
        # Scan for textures
        try:
            with open(path, "rb") as f:
                header_data = struct.unpack("<4si16sIII", f.read(36))
                faces_count = header_data[4]
                f.seek(
                    36 + header_data[3] * 12 + faces_count * 12
                )  # Skip header + verts + normals
                # Actually, parsing GEO is complex, but textures are usually 16-byte strings in GEOFace
                # Let's use a robust scan for common texture extensions
                f.seek(0)
                content = f.read()
                import re

                # Find .map, .pic, .tga references
                tex_matches = re.findall(
                    rb"([a-zA-Z0-9_.-]+)\.(map|pic|tga|dds|png|bmp)",
                    content,
                    re.IGNORECASE,
                )
                for tex_name, tex_ext in tex_matches:
                    full_tex = (
                        tex_name.decode("ascii", errors="ignore")
                        + "."
                        + tex_ext.decode("ascii", errors="ignore")
                    )
                    find_zfs_dependencies(reader, full_tex, extracted_files, temp_dir)
        except Exception as exc:
            print(f"[BZ ZFS] Failed to scan GEO dependencies for '{filename}': {exc}")


class BZ98TOOLS_OT_open_zfs(bpy.types.Operator, ImportHelper):
    """Open a Battlezone ZFS archive to browse its contents"""

    bl_idname = "bz.open_zfs"
    bl_label = "Open ZFS Archive"
    filename_ext = ".zfs"
    filter_glob: Any = bpy.props.StringProperty(default="*.zfs", options={"HIDDEN"})

    def execute(self, context):
        from .zfs_reader import ZFSReader

        context.scene.active_zfs_path = self.filepath
        context.scene.zfs_active_cache_path = get_zfs_archive_cache_dir(
            self.filepath,
            context.scene.zfs_cache_dir.strip() or get_default_zfs_cache_dir(),
        )
        context.scene.zfs_last_import_path = ""
        reader = ZFSReader(self.filepath)
        try:
            reader.open()
            context.scene.zfs_files.clear()
            for name in reader.list_files():
                item = context.scene.zfs_files.add()
                item.name = name
                item.ext = os.path.splitext(name)[1].lower()
                item.is_model = item.ext in {".vdf", ".sdf", ".geo"}
            first_model_index = next(
                (
                    idx
                    for idx, item in enumerate(context.scene.zfs_files)
                    if item.is_model
                ),
                -1,
            )
            context.scene.zfs_active_index = (
                first_model_index
                if first_model_index >= 0
                else (0 if len(context.scene.zfs_files) > 0 else -1)
            )
            reader.close()
        except Exception as e:
            self.report({"ERROR"}, f"Failed to open ZFS: {e}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BZ98TOOLS_OT_open_zfs_cache_folder(bpy.types.Operator):
    """Open the configured ZFS cache folder or the active archive cache folder"""

    bl_idname = "bz.open_zfs_cache_folder"
    bl_label = "Open ZFS Cache Folder"

    scope: Any = EnumProperty(
        name="Scope",
        items=(
            ("ROOT", "Root Cache", "Open the root ZFS cache folder"),
            (
                "ARCHIVE",
                "Archive Cache",
                "Open the cache folder for the active ZFS archive",
            ),
        ),
        default="ARCHIVE",
    )

    def execute(self, context):
        scene = context.scene
        if self.scope == "ROOT":
            target_dir = os.path.abspath(
                scene.zfs_cache_dir.strip() or get_default_zfs_cache_dir()
            )
        else:
            target_dir = _get_current_zfs_cache_dir(scene)
            if not target_dir:
                self.report({"ERROR"}, "No active ZFS archive is selected.")
                return {"CANCELLED"}

        os.makedirs(target_dir, exist_ok=True)
        try:
            _open_path_in_shell(target_dir)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not open cache folder: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BZ98TOOLS_OT_clear_zfs_cache(bpy.types.Operator):
    """Delete extracted ZFS cache files"""

    bl_idname = "bz.clear_zfs_cache"
    bl_label = "Clear ZFS Cache"

    def execute(self, context):
        import shutil

        cache_dir = context.scene.zfs_cache_dir.strip() or get_default_zfs_cache_dir()
        cache_dir = os.path.abspath(cache_dir)
        if not os.path.isdir(cache_dir):
            self.report({"INFO"}, "ZFS cache folder is already empty")
            return {"FINISHED"}

        removed = 0
        failures = 0
        for entry in os.listdir(cache_dir):
            path = os.path.join(cache_dir, entry)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed += 1
            except OSError:
                failures += 1

        if failures:
            self.report(
                {"WARNING"},
                f"Cleared {removed} cache entries, but {failures} could not be removed",
            )
        else:
            self.report({"INFO"}, f"Cleared {removed} cache entries")
        context.scene.zfs_last_import_path = ""
        return {"FINISHED"}


class BZ98TOOLS_OT_clear_zfs_archive_cache(bpy.types.Operator):
    """Delete extracted cache files for the active ZFS archive"""

    bl_idname = "bz.clear_zfs_archive_cache"
    bl_label = "Clear Active Archive Cache"

    def execute(self, context):
        import shutil

        archive_cache_dir = _get_current_zfs_cache_dir(context.scene)
        if not archive_cache_dir:
            self.report({"ERROR"}, "No active ZFS archive is selected.")
            return {"CANCELLED"}

        if not os.path.isdir(archive_cache_dir):
            self.report({"INFO"}, "The active archive cache is already empty.")
            return {"FINISHED"}

        try:
            shutil.rmtree(archive_cache_dir)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not clear archive cache: {exc}")
            return {"CANCELLED"}

        last_import = getattr(context.scene, "zfs_last_import_path", "")
        if last_import and os.path.abspath(last_import).startswith(
            os.path.abspath(archive_cache_dir)
        ):
            context.scene.zfs_last_import_path = ""

        self.report({"INFO"}, "Cleared the active archive cache.")
        return {"FINISHED"}


class BZ98TOOLS_OT_import_from_zfs(bpy.types.Operator):
    """Extract and import the selected model from ZFS"""

    bl_idname = "bz.import_from_zfs"
    bl_label = "Import from ZFS"

    filename: Any = bpy.props.StringProperty()

    def execute(self, context):
        from .zfs_reader import ZFSReader

        zfs_path = context.scene.active_zfs_path
        if not zfs_path or not os.path.exists(zfs_path):
            self.report({"ERROR"}, "No active ZFS archive")
            return {"CANCELLED"}

        filename = self.filename
        if not filename:
            selected = _get_selected_zfs_entry(context.scene)
            if not selected:
                self.report({"ERROR"}, "No ZFS file is selected")
                return {"CANCELLED"}
            filename = selected.name

        cache_root = context.scene.zfs_cache_dir.strip() or get_default_zfs_cache_dir()
        temp_dir = get_zfs_archive_cache_dir(zfs_path, cache_root)
        os.makedirs(temp_dir, exist_ok=True)

        try:
            reader = ZFSReader(zfs_path)
            reader.open()

            extracted_files = set()
            find_zfs_dependencies(reader, filename, extracted_files, temp_dir)

            reader.close()

            main_path = os.path.join(temp_dir, filename)
            if not os.path.exists(main_path):
                self.report({"ERROR"}, f"Failed to extract {filename}")
                return {"CANCELLED"}

            ext = os.path.splitext(filename)[1].lower()
            if ext == ".vdf":
                from . import import_vdf

                import_vdf.load(context, main_path)
            elif ext == ".sdf":
                from . import import_sdf

                import_sdf.load(context, main_path)
            elif ext == ".geo":
                from . import import_geo

                import_geo.geoload(context, main_path)
            else:
                self.report({"ERROR"}, f"Unsupported ZFS import type: {ext}")
                return {"CANCELLED"}

            context.scene.zfs_active_cache_path = temp_dir
            context.scene.zfs_last_import_path = main_path

            self.report({"INFO"}, f"Successfully imported {filename} and dependencies")
        except Exception as e:
            self.report({"ERROR"}, f"Import failed: {e}")
            import traceback

            traceback.print_exc()
            return {"CANCELLED"}
        finally:
            # Keep extracted dependencies in the cache folder so imported textures
            # remain available to Blender until the user clears the cache manually.
            pass

        return {"FINISHED"}


class BZ98TOOLS_UL_zfs_files(bpy.types.UIList):
    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        row = layout.row(align=True)
        row.label(text=item.name, icon=_get_zfs_entry_icon(item))
        row.label(text=item.ext or "file")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = []

        filter_text = data.zfs_filter.strip().lower()
        type_filter = data.zfs_type_filter

        for item in items:
            matches_text = not filter_text or filter_text in item.name.lower()
            matches_type = (
                type_filter == "ALL"
                or (type_filter == "MODELS" and item.is_model)
                or (type_filter == "TEXTURES" and item.ext in ZFS_TEXTURE_EXTENSIONS)
                or (
                    type_filter == "OTHER"
                    and not item.is_model
                    and item.ext not in ZFS_TEXTURE_EXTENSIONS
                )
            )
            flags.append(
                self.bitflag_filter_item if (matches_text and matches_type) else 0
            )

        order = bpy.types.UI_UL_list.sort_items_by_name(items, "name")
        return flags, order


class BZ98TOOLS_PT_zfs_explorer(bpy.types.Panel):
    bl_idname = "SCENE_PT_BZ_ZFS"
    bl_label = "Battlezone ZFS Explorer"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_BZ_SDFVDF"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        archive_cache_dir = _get_current_zfs_cache_dir(scene)
        if archive_cache_dir and scene.zfs_active_cache_path != archive_cache_dir:
            scene.zfs_active_cache_path = archive_cache_dir

        layout.operator("bz.open_zfs", icon="FILE_FOLDER")

        cache_box = layout.box()
        cache_box.label(text="Cache")
        cache_box.prop(scene, "zfs_cache_dir", text="Folder")
        cache_actions = cache_box.row(align=True)
        root_open = cache_actions.operator(
            "bz.open_zfs_cache_folder", text="Open Root", icon="FILE_FOLDER"
        )
        root_open.scope = "ROOT"
        cache_actions.operator("bz.clear_zfs_cache", icon="TRASH")

        if scene.active_zfs_path:
            archive_box = layout.box()
            archive_box.label(
                text=f"Archive: {os.path.basename(scene.active_zfs_path)}",
                icon="FILE_FOLDER",
            )
            archive_box.label(text=f"Files indexed: {len(scene.zfs_files)}")
            archive_path_row = archive_box.row()
            archive_path_row.enabled = False
            archive_path_row.prop(scene, "active_zfs_path", text="Archive Path")
            cache_dir_row = archive_box.row()
            cache_dir_row.enabled = False
            cache_dir_row.prop(scene, "zfs_active_cache_path", text="Cache Path")
            archive_actions = archive_box.row(align=True)
            archive_open = archive_actions.operator(
                "bz.open_zfs_cache_folder",
                text="Open Archive Cache",
                icon="FILE_FOLDER",
            )
            archive_open.scope = "ARCHIVE"
            archive_actions.operator("bz.clear_zfs_archive_cache", icon="TRASH")
            if scene.zfs_last_import_path:
                last_row = archive_box.row()
                last_row.enabled = False
                last_row.prop(scene, "zfs_last_import_path", text="Last Extracted")

            filters = layout.box()
            filters.label(text="Browser")
            row = filters.row(align=True)
            row.prop(scene, "zfs_filter", text="", icon="VIEWZOOM")
            row.prop(scene, "zfs_type_filter", text="")

            list_box = layout.box()
            list_box.template_list(
                "BZ98TOOLS_UL_zfs_files",
                "",
                scene,
                "zfs_files",
                scene,
                "zfs_active_index",
                rows=10,
            )

            selected = _get_selected_zfs_entry(scene)
            action_box = layout.box()
            action_box.label(text="Selection")
            if selected:
                action_box.label(text=selected.name, icon=_get_zfs_entry_icon(selected))
                import_row = action_box.row()
                import_row.enabled = selected.is_model
                op = import_row.operator(
                    "bz.import_from_zfs", text="Import Selected", icon="IMPORT"
                )
                op.filename = selected.name
                if not selected.is_model:
                    action_box.label(
                        text="Only GEO, VDF, and SDF files can be imported directly.",
                        icon="INFO",
                    )
            else:
                action_box.label(
                    text="Select a file from the archive list.", icon="INFO"
                )


GUIClasses = [
    BZ98TOOLS_MT_import_menu,
    BZ98TOOLS_MT_export_menu,
    BZ_OT_ShowAnimIndexReference,
    BZ98TOOLS_OT_show_model_system_info,
    BZ98TOOLS_OT_show_advanced_vdf_info,
    BZ98TOOLS_OT_show_pivot_dummyroot_info,
    BZ98TOOLS_OT_show_validation_checks,
    BZ_PT_GeoTypeListPopover,
    BattlezoneSDFVDFProperties,
    BZ98TOOLS_PT_scene_asset_properties,
    BZ98TOOLS_PT_scene_orientation_reference,
    BZ98TOOLS_PT_scene_collision_helpers,
    BZ98TOOLS_PT_scene_advanced,
    BZ98TOOLS_PT_scene_vdf_raw,
    BZ98TOOLS_OT_validate_scene,
    BZ98TOOLS_OT_select_validation_target,
    BZ98TOOLS_OT_fix_validation_name,
    BZ98TOOLS_OT_select_by_object_type,
    BZ98TOOLS_OT_quick_normals_fix,
    BZ98TOOLS_OT_create_organic_redux_skin,
    BZ98TOOLS_OT_create_vehicle_geo_set,
    BZ98TOOLS_PT_validation,
    BZ98TOOLS_PT_import_diagnostics,
    BattlezoneGEOProperties,
    BZ98TOOLS_PT_view3d_selected_geo,
    BZ98TOOLS_PT_view3d_quick_tools,
    BZ98TOOLS_PT_view3d_organic_redux_skin,
    BZ98TOOLS_PT_view3d_animation_tools,
    BZ98TOOLS_PT_view3d_cockpit_tools,
    BZ98TOOLS_PT_geo_collision,
    BZ98TOOLS_PT_geo_sdf,
    BZ98TOOLS_PT_geo_vdf,
    BZ98TOOLS_PT_geo_advanced,
    BZ98TOOLS_PT_geo_face_data,
    BZ98TOOLS_OT_fill_material_texture_name,
    BZ98TOOLS_OT_fill_material_texture_from_image,
    BattlezoneMaterialProperties,
    OPCreateNewElement,
    OPDeleteElement,
    OPDuplicateElement,
    OPMoveElement,
    OPApplyAnimationPreset,
    BZ98TOOLS_OT_mirror_legacy_animation_keys,
    BZ98TOOLS_OT_apply_export_preset,
    BZ98TOOLS_OT_save_export_preset,
    BZ98TOOLS_OT_delete_export_preset,
    BZ98TOOLS_MT_geo_export_presets,
    BZ98TOOLS_MT_vdf_export_presets,
    BZ98TOOLS_MT_sdf_export_presets,
    BZ98TOOLS_MT_geo_delete_export_preset,
    BZ98TOOLS_MT_vdf_delete_export_preset,
    BZ98TOOLS_MT_sdf_delete_export_preset,
    OPGenerateVDFCollisionMeshes,
    OPGenerateCollision,
    OPCreateSpinnerHelper,
    OPGenerateCockpitGeometry,
    OPCaptureRawVDFMatrix,
    AnimationUIList,
    AnimationPanel,
    BZ98TOOLS_UL_zfs_files,
    BZ98TOOLS_PT_zfs_explorer,
    BZ98TOOLS_OT_open_zfs,
    BZ98TOOLS_OT_open_zfs_cache_folder,
    BZ98TOOLS_OT_clear_zfs_cache,
    BZ98TOOLS_OT_clear_zfs_archive_cache,
    BZ98TOOLS_OT_import_from_zfs,
    ZFSFileEntry,
]

ImportExportClasses = [ImportGEO, ImportVDF, ImportSDF, ExportGEO, ExportVDF, ExportSDF]


def register():
    for property in Properties:
        bpy.utils.register_class(property)

    for guiclass in GUIClasses:
        bpy.utils.register_class(guiclass)

    for registerclass in ImportExportClasses:
        bpy.utils.register_class(registerclass)

    # Register BZR Ogre mesh operators
    bpy.utils.register_class(BZ98TOOLS_OT_import_bzr_mesh)
    bpy.utils.register_class(BZ98TOOLS_OT_export_bzr_mesh)

    bz_map_io.register()

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

    """
    Lets register some properties for objects. This part is notably important!
    """
    # The animations currently loaded.
    bpy.types.Scene.AnimationCollection = bpy.props.CollectionProperty(
        type=AnimationPropertyGroup
    )
    # The current animation we are viewing.
    bpy.types.Scene.CurAnimation = bpy.props.IntProperty(name="Entry")
    # Custom property groups.
    bpy.types.Material.MaterialPropertyGroup = bpy.props.PointerProperty(
        type=MaterialPropertyGroup
    )
    bpy.types.Object.GEOPropertyGroup = bpy.props.PointerProperty(type=GEOPropertyGroup)
    bpy.types.Scene.SDFVDFPropertyGroup = bpy.props.PointerProperty(
        type=SDFVDFPropertyGroup
    )

    # ZFS Explorer Properties
    bpy.types.Scene.zfs_files = bpy.props.CollectionProperty(type=ZFSFileEntry)
    bpy.types.Scene.active_zfs_path = bpy.props.StringProperty(name="Active ZFS")
    bpy.types.Scene.zfs_active_cache_path = bpy.props.StringProperty(
        name="Active ZFS Cache Path"
    )
    bpy.types.Scene.zfs_active_index = bpy.props.IntProperty(
        name="Active ZFS Entry", default=-1
    )
    bpy.types.Scene.zfs_filter = bpy.props.StringProperty(name="ZFS Filter")
    bpy.types.Scene.zfs_type_filter = bpy.props.EnumProperty(
        name="ZFS Type Filter",
        items=(
            ("ALL", "All Files", "Show all files"),
            ("MODELS", "Models", "Show importable GEO, VDF, and SDF files"),
            ("TEXTURES", "Textures", "Show texture and image files"),
            ("OTHER", "Other", "Show non-model support files"),
        ),
        default="MODELS",
    )
    bpy.types.Scene.zfs_cache_dir = bpy.props.StringProperty(
        name="ZFS Cache Folder",
        description="Folder used to keep extracted ZFS dependencies available after import",
        default=get_default_zfs_cache_dir(),
        subtype="DIR_PATH",
    )
    bpy.types.Scene.zfs_last_import_path = bpy.props.StringProperty(
        name="Last ZFS Import Path"
    )
    bpy.types.Scene.bz_validation_issues = bpy.props.CollectionProperty(
        type=ValidationIssuePropertyGroup
    )
    bpy.types.Scene.bz_validation_signature = bpy.props.StringProperty(
        name="Validation Signature"
    )
    bpy.types.Scene.bz_import_diagnostics = bpy.props.CollectionProperty(
        type=ImportDiagnosticPropertyGroup
    )


def unregister():
    # Remove menus first so UI won't try to use unregistered classes
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

    bz_map_io.unregister()

    # Remove dynamically registered Blender properties.
    for owner, prop_name in (
        (bpy.types.Scene, "AnimationCollection"),
        (bpy.types.Scene, "CurAnimation"),
        (bpy.types.Material, "MaterialPropertyGroup"),
        (bpy.types.Object, "GEOPropertyGroup"),
        (bpy.types.Scene, "SDFVDFPropertyGroup"),
        (bpy.types.Scene, "zfs_files"),
        (bpy.types.Scene, "active_zfs_path"),
        (bpy.types.Scene, "zfs_active_cache_path"),
        (bpy.types.Scene, "zfs_active_index"),
        (bpy.types.Scene, "zfs_filter"),
        (bpy.types.Scene, "zfs_type_filter"),
        (bpy.types.Scene, "zfs_cache_dir"),
        (bpy.types.Scene, "zfs_last_import_path"),
        (bpy.types.Scene, "bz_validation_issues"),
        (bpy.types.Scene, "bz_validation_signature"),
        (bpy.types.Scene, "bz_import_diagnostics"),
    ):
        if hasattr(owner, prop_name):
            delattr(owner, prop_name)

    # Unregister BZR Ogre mesh operators
    bpy.utils.unregister_class(BZ98TOOLS_OT_export_bzr_mesh)
    bpy.utils.unregister_class(BZ98TOOLS_OT_import_bzr_mesh)

    # Unregister main classes
    for registerclass in ImportExportClasses:
        bpy.utils.unregister_class(registerclass)

    for guiclass in GUIClasses:
        bpy.utils.unregister_class(guiclass)

    for property in Properties:
        bpy.utils.unregister_class(property)


# If being tested in script editor run.
if __name__ == "__main__":
    register()
