from typing import Any

# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Battlezone Map IO",
    "description": "Imports and exports Battlezone Redux map terrain, texture, object, and path data.",
    "author": "Business Lawyer; integrated in BZ98R Blender Toolkit by GrizzlyOne95; HG2 converter by DivisionByZero",
    "blender": (4, 5, 0),
    "location": "VIEW_3D > Battlezone > Map Tools",
    "category": "Import-Export",
    "version": (1, 6, 1),
}

import bpy
import bmesh
import math
import mathutils
from mathutils import Vector
import random
import string
import time
import functools
from mathutils import Matrix
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup
import os

import struct
import csv
import sys
from pathlib import Path
import re

ADDON_DIR = Path(__file__).resolve().parent
MAP_TEMPLATE_PATH = ADDON_DIR / "map_assets" / "BZMapIO.blend"
VARIANT_LABELS = "ABCDEFGHIJK"


def _strip_inline_comment(value):
    return value.split("//", 1)[0].strip()


def _read_trn_material_name(trn_path):
    try:
        with open(trn_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                clean = _strip_inline_comment(line)
                if "=" not in clean:
                    continue
                key, value = clean.split("=", 1)
                if key.strip().lower() == "materialname":
                    return value.strip()
    except OSError:
        return ""
    return ""


def _find_case_insensitive_file(folder, filename):
    if not folder or not filename:
        return ""
    target = filename.lower()
    try:
        for entry in os.listdir(folder):
            if entry.lower() == target:
                return os.path.join(folder, entry)
    except OSError:
        return ""
    return ""


def _find_first_matching_file(folder, suffix):
    try:
        for entry in os.listdir(folder):
            if entry.lower().endswith(suffix.lower()):
                return os.path.join(folder, entry)
    except OSError:
        return ""
    return ""


def _variant_label_from_token(token):
    token = (token or "0").strip().upper()
    if token.isdigit():
        index = int(token)
        if 0 <= index < len(VARIANT_LABELS):
            return VARIANT_LABELS[index]
        return VARIANT_LABELS[0]
    if token in VARIANT_LABELS:
        return token
    return VARIANT_LABELS[0]


def _tile_code_from_texture_name(texture_name):
    stem = os.path.splitext(os.path.basename(texture_name or ""))[0].lower()
    match = re.search(r"([0-9a-f])([0-9a-f])([scd])([0-9a-k])$", stem)
    if match is None:
        return ""
    base_tile, transition_tile, tile_type, variant_token = match.groups()
    return (
        base_tile
        + transition_tile
        + tile_type.upper()
        + _variant_label_from_token(variant_token)
    )


def _read_custom_atlas_csv(csv_path):
    rows = []
    with open(
        csv_path, "r", encoding="utf-8-sig", errors="ignore", newline=""
    ) as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 5:
                continue
            texture_name = row[0].strip()
            if not texture_name:
                continue
            tile_code = _tile_code_from_texture_name(texture_name)
            if not tile_code:
                continue
            try:
                coords = [str(float(row[index].strip())) for index in range(1, 5)]
            except (TypeError, ValueError):
                continue
            rows.append(["ZZ" + tile_code + "0.MAP", *coords])
    return rows


def _read_material_aliases(material_path):
    aliases = {}
    if not material_path:
        return aliases
    try:
        with open(material_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                clean = _strip_inline_comment(line)
                parts = clean.split()
                if len(parts) >= 3 and parts[0].lower() == "set_texture_alias":
                    aliases[parts[1]] = parts[2].strip('"')
    except OSError:
        pass
    return aliases


def _ensure_custom_world_material(material_name, texture_path):
    material = bpy.data.materials.get(material_name)
    if material is None:
        material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    if not texture_path or not os.path.isfile(texture_path):
        return material

    try:
        image = bpy.data.images.load(texture_path, check_existing=True)
    except RuntimeError:
        return material

    nodes = material.node_tree.nodes
    principled = nodes.get("Principled BSDF")
    texture_node = nodes.get("BZ Custom World Atlas")
    if texture_node is None:
        texture_node = nodes.new(type="ShaderNodeTexImage")
        texture_node.name = "BZ Custom World Atlas"
    texture_node.image = image
    if principled is not None:
        base_color = principled.inputs.get("Base Color")
        color_output = texture_node.outputs.get("Color")
        if base_color is not None and color_output is not None:
            for link in list(base_color.links):
                material.node_tree.links.remove(link)
            material.node_tree.links.new(color_output, base_color)
    return material


def _load_custom_world_definition(world_folder, preferred_material_name=""):
    world_folder = os.path.abspath(world_folder or "")
    if not os.path.isdir(world_folder):
        raise ValueError(
            "Choose a folder containing a custom world .trn and atlas files."
        )

    material_name = (preferred_material_name or "").strip()
    if not material_name:
        trn_path = _find_first_matching_file(world_folder, ".trn")
        material_name = _read_trn_material_name(trn_path)
    if not material_name:
        raise ValueError(
            "Could not find MaterialName in a .trn file. Pick the custom world folder that contains the .trn."
        )

    csv_path = _find_case_insensitive_file(world_folder, material_name + ".csv")
    if not csv_path:
        csv_path = _find_first_matching_file(world_folder, "_detail_atlas.csv")
    if not csv_path:
        raise ValueError("Could not find a matching *_detail_atlas.csv file.")

    csv_data = _read_custom_atlas_csv(csv_path)
    if not csv_data:
        raise ValueError(
            f"No usable tile rows were found in {os.path.basename(csv_path)}."
        )

    material_path = _find_case_insensitive_file(
        world_folder, material_name + ".material"
    )
    aliases = _read_material_aliases(material_path)
    texture_name = aliases.get("DetailMap") or aliases.get("DiffuseMap") or ""
    texture_path = (
        _find_case_insensitive_file(world_folder, texture_name) if texture_name else ""
    )
    return {
        "folder": world_folder,
        "material_name": material_name,
        "csv_path": csv_path,
        "material_path": material_path,
        "texture_path": texture_path,
        "csv_data": csv_data,
    }


def _scene_custom_world_definition(scene):
    folder = getattr(scene, "BZCustomWorldFolder", "")
    material_name = getattr(scene, "BZCustomWorldMaterial", "")
    if not folder or not material_name:
        return None
    try:
        definition = _load_custom_world_definition(folder, material_name)
    except ValueError:
        return None
    if definition["material_name"].lower() != material_name.lower():
        return None
    return definition


def _hide_view_clear_compat():
    for obj in bpy.data.objects:
        try:
            obj.hide_set(False)
        except RuntimeError:
            pass
        obj.hide_viewport = False


def _loop_multi_select_compat(ring=False):
    if hasattr(bpy.ops.mesh, "select_edge_loop_multi"):
        if ring:
            return bpy.ops.mesh.select_edge_loop_multi(delimit_edge_loop={"NORMAL"})
        return bpy.ops.mesh.select_edge_loop_multi()
    return bpy.ops.mesh.loop_multi_select(ring=ring)


######################################################################################################
######################################################################################################
#####  IMPORT ########################################################################################
######################################################################################################
######################################################################################################


class bzmapimport(Operator, ImportHelper):
    bl_idname = "bzmapimport.data"
    bl_label = "Import Map (.hg2)"

    # ImportHelper mixin class uses this
    filename_ext = "*.hg2"

    filter_glob: StringProperty(
        default="*.hg2",
        options={"HIDDEN"},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    def execute(self, context):
        # Get the file and folder user picked
        folder, file = os.path.split(self.filepath)
        context.scene.BZMapFile = self.filepath

        # PERFORM IMPORT!
        class HG2:
            structure_version = 1
            zone_bits = 8
            map_width = 0
            map_depth = 0
            map_version = 10
            heights = None

            def read(self, stream):
                self.structure_version = self.read_ushort(stream)
                self.zone_bits = self.read_ushort(stream)
                self.map_width = self.read_ushort(stream)
                self.map_depth = self.read_ushort(stream)
                self.map_version = self.read_uint(stream)

                zcount = self.map_width * self.map_depth
                vcount = 2 ** (2 * self.zone_bits) * zcount
                self.heights = list(
                    struct.unpack("<" + str(vcount) + "H", stream.read(2 * vcount))
                )
                for i, h in enumerate(self.heights):
                    self.heights[i] = (
                        h & 0x1FFF
                    )  # Note: This clobbers the four unused flag bits

                return self

            def read_file(self, filepath):
                with open(filepath, "rb") as stream:
                    return self.read(stream)

            def write(self, stream):
                self.write_ushort(stream, self.structure_version)
                self.write_ushort(stream, self.zone_bits)
                self.write_ushort(stream, self.map_width)
                self.write_ushort(stream, self.map_depth)
                self.write_uint(stream, self.map_version)

                zcount = self.map_width * self.map_depth
                vcount = 2 ** (2 * self.zone_bits) * zcount
                stream.write(struct.pack("<" + str(vcount) + "H", *self.heights))

            def write_file(self, filepath):
                with open(filepath, "wb") as stream:
                    return self.write(stream)

            def read_ushort(self, stream):
                return int.from_bytes(stream.read(2), "little")

            def read_uint(self, stream):
                return int.from_bytes(stream.read(4), "little")

            def write_ushort(self, stream, v):
                stream.write(int.to_bytes(v, 2, "little"))

            def write_uint(self, stream, v):
                stream.write(int.to_bytes(v, 4, "little"))

            def write_csv_file(self):
                zone_length = 2 ** (self.zone_bits)
                vwidth = zone_length * self.map_width
                vdepth = zone_length * self.map_depth

                heights2 = [[0 for i in range(vwidth)] for j in range(vdepth)]

                # Make sure user is in layout workspace and in object mode.
                bpy.context.window.workspace = bpy.data.workspaces["Layout"]
                bpy.ops.object.mode_set(mode="OBJECT")

                # Make sure the user's map is selected
                bpy.ops.object.select_all(action="DESELECT")
                for ob in bpy.data.objects:
                    if ".hg2_" in ob.name.lower():
                        bpy.data.objects[ob.name].select_set(True)
                        bpy.data.objects[ob.name].hide_select = (
                            False  # The map must be selectable because this script depends on selections to function.
                        )
                        bpy.context.view_layer.objects.active = ob

                for z in range(vdepth):
                    for x in range(vwidth):
                        sub_x = x % zone_length
                        sub_z = z % zone_length
                        zone_x = x // zone_length
                        zone_z = z // zone_length
                        i = (
                            (zone_z * self.map_width + zone_x) * zone_length + sub_z
                        ) * zone_length + sub_x
                        heights2[z][x] = f"{self.heights[i]:04}"

                # Begin by Generating the map. To achieve this we use geometry nodes on a template object which injects
                # the map's size values into a grid, duplicates that mesh, applies the geometry nodes then moves every
                # point to match the map.

                # If nothing is selected, for some stupid reason blender assumes
                # you want to delete hidden objects, so I reveal them temporarily.
                _hide_view_clear_compat()

                # Clear any existing meshes from the scene except for the template and references
                for obj in bpy.context.selected_objects:
                    obj.select_set(False)
                for ob in bpy.data.objects:
                    if (
                        ob.name != "BZMapGenerator"
                        and ob.users_collection[0].name != "ReferenceVisuals"
                        and ob.users_collection[0].name != "BZ_Unit_Models"
                    ):
                        bpy.context.view_layer.objects.active = ob
                        bpy.data.objects[ob.name].select_set(True)
                        bpy.ops.object.delete(use_global=False, confirm=False)
                        bpy.ops.object.select_all(action="DESELECT")

                # Clean up unused data blocks after deleting, because Blender is dumb and doesn't clean up after itself.
                bpy.ops.outliner.orphans_purge(
                    do_local_ids=True, do_linked_ids=True, do_recursive=True
                )

                # Select the grid template...
                bpy.ops.object.select_all(action="DESELECT")
                for ob in bpy.data.objects:
                    if ob.name == "BZMapGenerator":
                        bpy.context.view_layer.objects.active = ob
                        bpy.data.objects[ob.name].select_set(True)

                # To better line up with how objects are placed in Battlezone, we position the map so that the lower left
                # corner overlaps with the world origin center of the scene (coordinates 0/0)

                bpy.context.object.location[0] = (self.map_width * 1280) / 2
                bpy.context.object.location[1] = -(self.map_depth * 1280) / 2

                # Apply changes to geometry nodesto fit user map.
                bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                    0
                ].default_value = (self.map_width * 1280)
                bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                    1
                ].default_value = (self.map_depth * 1280)
                bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                    2
                ].default_value = (256 * self.map_width)
                bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                    3
                ].default_value = (256 * self.map_depth)

                # Place header information into Bledner's geometry nodes.

                bpy.data.node_groups["Geometry Nodes"].nodes["String"].string = str(
                    self.structure_version
                )
                bpy.data.node_groups["Geometry Nodes"].nodes["String.001"].string = str(
                    self.zone_bits
                )
                bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string = str(
                    self.map_width
                )
                bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string = str(
                    self.map_depth
                )
                bpy.data.node_groups["Geometry Nodes"].nodes["String.004"].string = str(
                    self.map_version
                )

                # Duplicate grid template and give it a descriptive name.
                bpy.ops.object.duplicate_move(
                    OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"}
                )
                bpy.context.object.name = os.path.basename(
                    context.scene.BZMapFile
                    + "_"
                    + str(self.map_width * 1280)
                    + "x"
                    + str(self.map_depth * 1280)
                )

                # Apply grid template into mesh to discard geo nodes but leave changes.
                bpy.ops.object.modifier_apply(modifier="GeometryNodes")

                # A duplicate adjustment object is needed to correct orientation issues.
                MAPOriginal = bpy.context.selected_objects[0]

                # The BZMapGenerator object's vertex index on the X axis is inverse of Battlezone terrain.
                # Flip it so that no conversion work is needed.
                bpy.context.object.rotation_euler[0] = 3.14159
                bpy.ops.object.transform_apply(
                    location=False, rotation=True, scale=False
                )
                bpy.ops.object.duplicate_move(
                    OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"}
                )
                MAPDuplicate = bpy.context.selected_objects[0]

                # Duplicate map needs to use old vertex indices
                bpy.context.object.rotation_euler[0] = 3.14159
                bpy.ops.object.transform_apply(
                    location=False, rotation=True, scale=False
                )

                # Take data from HG2 file and apply it to every vertex point on the grid.
                counter = 0
                obj = bpy.context.object
                for x in range(
                    0, len(heights2)
                ):  # this iterates 512 times on a medium size 2560x2560 map
                    for z in range(0, len(heights2[x])):
                        obj.data.vertices[counter].co[2] = float(heights2[z][x]) / 10
                        counter += 1

                # From here, rotate the duplicate map 90 degrees and invert its X coordinate to correct orientation differences.
                bpy.context.object.rotation_euler[2] = -1.5708

                # I use an indirect approach involving the shrinkmap modifier.
                # This lets me position and rotate the terrain without ever changing the point order.

                # Re-select the original map from generator
                bpy.ops.object.select_all(action="DESELECT")
                bpy.data.objects[MAPOriginal.name].select_set(True)
                bpy.context.view_layer.objects.active = MAPOriginal
                bpy.ops.mesh.uv_texture_add()  # Give it a blank uv map

                # Apply shrinkwrap (to re-orient map)
                bpy.ops.object.modifier_add(type="SHRINKWRAP")
                bpy.context.object.modifiers["Shrinkwrap"].target = bpy.data.objects[
                    MAPDuplicate.name
                ]
                bpy.context.object.modifiers["Shrinkwrap"].wrap_method = "PROJECT"
                bpy.context.object.modifiers["Shrinkwrap"].use_project_z = True
                bpy.context.object.modifiers["Shrinkwrap"].use_negative_direction = True
                bpy.ops.object.modifier_apply(modifier="Shrinkwrap")

                # Smooth shading on
                bpy.ops.object.shade_smooth()

                # The normals will be upside down, fix this.
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.mesh.flip_normals()
                bpy.ops.mesh.select_all(action="DESELECT")
                bpy.ops.object.mode_set(mode="OBJECT")

                # Eliminate the second duplicate map.
                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = MAPDuplicate
                bpy.data.objects[MAPDuplicate.name].select_set(True)
                bpy.ops.object.delete(use_global=False, confirm=False)

                # Now no one has to know that I used a stupid, horribly inefficient method to rotate a mesh without changing its point order.

                # The scale of the map is directly displayed (in text) to the user in the viewport.
                # Change it to represent current map size.

                # Find Text object and change it.
                for ob in bpy.data.objects:
                    if ob.name == "Scale_Display":
                        ob.data.body = (
                            str(1280 * (int(self.map_width)))
                            + "x"
                            + str(1280 * (int(self.map_depth)))
                        )
                        # Re-position the text appropriately.
                        bpy.data.objects[ob.name].location.x = (
                            ((1280 * (int(self.map_depth))) / 2) + 100
                        ) + ((self.map_width * 1280) / 2)
                        bpy.data.objects[ob.name].location.y = 0
                        bpy.data.objects[ob.name].location.z = bpy.data.objects[
                            MAPOriginal.name
                        ].dimensions.z  # this is the bounding box top of the map mesh.

            # Check for cloned objects in library and move them to BZ_Unit_Models
            # Reference objects are also temporarily moved to the scene origin
            # so object replacement can happen with fewer steps needed.
            ItemstoMove = []
            for collection in bpy.data.collections:
                if collection.name == "BZ_Unit_Models":
                    for obj in collection.all_objects:
                        if obj.name.find(".") != -1:
                            ItemstoMove.append(obj)
                        else:
                            obj.location[0] = 0
                            obj.location[1] = 0
                            obj.location[2] = 0

            for x in range(0, len(ItemstoMove)):
                bpy.ops.object.select_all(action="DESELECT")
                bpy.data.objects[ItemstoMove[x].name].select_set(True)
                bpy.context.view_layer.objects.active = ItemstoMove[x]
                bpy.ops.object.move_to_collection(collection_index=2)

        # Set active collection to USER_SCENE. We want any script-generated objects to go in there.
        scene_collection = bpy.context.view_layer.layer_collection
        bpy.context.view_layer.active_layer_collection = scene_collection

        # Process the information from the file. This applies the vertex point transforms to the generated mesh.
        HG2().read_file(Path(context.scene.BZMapFile)).write_csv_file()

        # The cloned map objects are still hidden at this point. Reveal them, but hide the BZMapGenerator template.
        _hide_view_clear_compat()
        for ob in bpy.data.objects:
            if ob.name == "BZMapGenerator":
                ob.hide_set(True)
            # Get the user map name while we're at it.
            if ".hg2_" in ob.name.lower():
                UserMap = ob

        bpy.context.view_layer.objects.active = UserMap
        bpy.data.objects[UserMap.name].select_set(True)

        # Apply transforms to the user map in case they moved or scaled it.
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Set up seams for texture grid. This is needed because the height data is denser
        # than the texture tile data. This is my way of keeping track of the two.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="EDGE")
        PolySelect = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        PolySelect.faces.ensure_lookup_table()  # Blender is dumb and can't do this for the user. Pathetic.
        PolySelect.edges.ensure_lookup_table()
        EdgeSeamDimensions = int(
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[2].default_value
        )
        Counter = 0
        for v in range(0, int(EdgeSeamDimensions / 4)):
            PolySelect.edges[int(Counter)].select = True
            Counter += (EdgeSeamDimensions * 4) - 4

        # Formula, EdgeSeam-1 * EdgeSeam-1 + edgeSeam
        Counter = (
            (EdgeSeamDimensions - 1) * (EdgeSeamDimensions - 1) + EdgeSeamDimensions - 1
        )
        for v in range(0, int(EdgeSeamDimensions / 4)):
            PolySelect.edges[int(Counter)].select = True
            Counter += (EdgeSeamDimensions * 4) - 4

        _loop_multi_select_compat(ring=False)
        bpy.ops.mesh.mark_seam(clear=False)
        bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.vertex_group_add()

        # Read TRN file data. We need this because the TRN can be set to offset the position of objects placed in the game's editor.
        f = open(context.scene.BZMapFile.lower().replace(".hg2", ".trn"), "r")
        TRNData = f.readlines()

        MinX = 0
        MinZ = 0
        MinHeight = 0

        # I don't know why, but for whatever reason some people's maps will have duplicate
        # entries of minx/minz/height... these flags make sure only the first instance of these are considered.
        MinXFound = False
        MinZFound = False
        HeightFound = False
        for x in range(0, len(TRNData)):
            if "MinX" in TRNData[x]:
                if MinXFound == False:
                    MinXValue = TRNData[x].find("=")
                    MinX = TRNData[x][MinXValue + 1 :].replace("\n", "")
                    MinXFound = True
            if "MinZ" in TRNData[x]:
                if MinZFound == False:
                    MinZValue = TRNData[x].find("=")
                    MinZ = TRNData[x][MinZValue + 1 :].replace("\n", "")
                    MinZFound = True
            if (
                TRNData[x][:6].lower() == "height" and x < 10
            ):  # I only check the first 10 lines of BZN file, theres no "clean" way to check this reliably.
                if HeightFound == False:
                    MinHeightValue = TRNData[x].find("=")
                    MinHeight = TRNData[x][MinHeightValue + 1 :].replace("\n", "")
                    HeightFound = True

            # This obtains the CSV file referenced in TRN and injects it into the geometry node called "texture_set"
            if "materialname" in TRNData[x].lower():
                TextureNameIndex = TRNData[x].rfind("=")
                TextureName = TRNData[x][TextureNameIndex + 1 :].replace(" ", "")
                TextureName = TRNData[x][TextureNameIndex + 1 :].replace("\n", "")
                bpy.data.node_groups["Geometry Nodes"].nodes[
                    "String.005"
                ].string = TextureName

        # Read BZN file data.
        try:
            f = open(context.scene.BZMapFile.lower().replace(".hg2", ".bzn"), "r")
            BZNData = f.readlines()

            # If user has import objects enabled, also import the objects from the BZN file.
            ImportBZNCheckbox = context.scene.BZMapIO_Toggles.ImportBZN
            if ImportBZNCheckbox == True:

                # GET HEADER INFORMATION
                HeaderInfo = []
                for x in range(0, len(BZNData)):
                    if BZNData[x] == "[GameObject]\n":
                        GameObjectIndex = x
                        break
                    else:
                        HeaderInfo.append(BZNData[x])
                # COLLECT GAME OBJECTS
                GameObjects = []
                for x in range(GameObjectIndex, len(BZNData)):
                    if BZNData[x] == "[AiMission]\n":
                        AiIndex = x
                        break
                    else:
                        GameObjects.append(BZNData[x])
                # COLLECT PATH POINT DATA
                AiPathData = []
                for x in range(AiIndex, len(BZNData)):
                    AiPathData.append(BZNData[x])

                # Set active collection to GAMEOBJECTS.
                layer_collection = bpy.context.view_layer.layer_collection.children[
                    "GAMEOBJECTS"
                ]
                bpy.context.view_layer.active_layer_collection = layer_collection

                # Get all the objects within BZ_Unit_Models collection. Needed to apply 3d models to placed objects.
                BZ_Unit_Models = []
                for collection in bpy.data.collections:
                    if collection.name == "BZ_Unit_Models":
                        for obj in collection.all_objects:
                            BZ_Unit_Models.append(obj)

                # Create object for every [GAMEOBJECT] detected in list.
                for x in range(0, len(GameObjects)):
                    if GameObjects[x][:12] == "[GameObject]":
                        UseCustomModel = 0
                        # Try to locate an object within BZ_Unit_Models collection.
                        for y in range(0, len(BZ_Unit_Models)):
                            if (
                                BZ_Unit_Models[y].name.lower()
                                in GameObjects[x + 2].lower()
                            ):
                                UseCustomModel = 1

                                # Select and duplicate the model.
                                bpy.ops.object.select_all(action="DESELECT")
                                bpy.context.view_layer.objects.active = BZ_Unit_Models[
                                    y
                                ]
                                bpy.data.objects[BZ_Unit_Models[y].name].select_set(
                                    True
                                )

                                # Update the shrinkwrap modifier to use the user's map.
                                bpy.context.object.constraints["Shrinkwrap"].target = (
                                    bpy.data.objects[UserMap.name]
                                )

                                bpy.ops.object.duplicate_move(
                                    OBJECT_OT_duplicate={
                                        "linked": False,
                                        "mode": "TRANSLATION",
                                    },
                                    TRANSFORM_OT_translate={
                                        "value": (0, 0, 0),
                                        "orient_type": "GLOBAL",
                                        "orient_matrix": (
                                            (1, 0, 0),
                                            (0, 1, 0),
                                            (0, 0, 1),
                                        ),
                                        "orient_matrix_type": "GLOBAL",
                                        "constraint_axis": (False, False, False),
                                        "mirror": False,
                                        "use_proportional_edit": False,
                                        "proportional_edit_falloff": "SMOOTH",
                                        "proportional_size": 1,
                                        "use_proportional_connected": False,
                                        "use_proportional_projected": False,
                                        "snap": False,
                                        "snap_elements": {
                                            "VERTEX",
                                            "FACE",
                                            "FACE_NEAREST",
                                        },
                                        "use_snap_project": True,
                                        "snap_target": "CENTER",
                                        "use_snap_self": True,
                                        "use_snap_edit": True,
                                        "use_snap_nonedit": True,
                                        "use_snap_selectable": False,
                                        "snap_point": (0, 0, 0),
                                        "snap_align": False,
                                        "snap_normal": (0, 0, 0),
                                        "gpencil_strokes": False,
                                        "cursor_transform": False,
                                        "texture_space": False,
                                        "remove_on_cancel": False,
                                        "view2d_edge_pan": False,
                                        "release_confirm": False,
                                        "use_accurate": False,
                                        "use_automerge_and_split": False,
                                    },
                                )
                                bpy.ops.object.move_to_collection(collection_index=2)

                                # We discard any existing data the template object has attached to it.
                                # ... this is only utilized in any meaningful capacity when the user is PLACING objects.

                                # Because blender is dumb we have to collect the list before
                                # deleting stuff from it... don't ask why. *sigh*
                                KeyList = []
                                for key, value in bpy.context.object.data.items():
                                    KeyList.append(key)
                                    pass

                                for x in range(0, len(KeyList)):
                                    del bpy.context.object.data[KeyList[x]]

                                break

                        if UseCustomModel == 0:
                            bpy.ops.mesh.primitive_cube_add(
                                size=2,
                                enter_editmode=False,
                                align="WORLD",
                                location=(0, 0, 0),
                                scale=(5, 5, 5),
                            )

                    # This is where data is written into the custom properties of each GAMEOBJECT.
                    bpy.context.object.data[str(x)] = GameObjects[x]

                    # Use label for object name
                    if "label = " in GameObjects[x]:
                        bpy.context.object.name = GameObjects[x][8:].replace("\n", "")

                    # Position the object, this data is just above euler properties in the data.
                    if "seqno [1]" in GameObjects[x]:
                        bpy.context.object.location[0] = (
                            float(GameObjects[x + 8])
                        ) - float(MinZ)
                        bpy.context.object.location[1] = (
                            float(GameObjects[x + 4]) * -1
                        ) - float(MinX)
                        bpy.context.object.location[2] = (
                            float(GameObjects[x + 6])
                        ) - float(MinHeight)

                    # Rotate the object.
                    if "transform [1]" in GameObjects[x]:
                        m = [
                            (
                                float(GameObjects[x + 2]),
                                float(GameObjects[x + 4]),
                                float(GameObjects[x + 6]),
                                1,
                            ),
                            (
                                float(GameObjects[x + 8]),
                                float(GameObjects[x + 10]),
                                float(GameObjects[x + 12]),
                                0,
                            ),
                            (
                                float(GameObjects[x + 14]),
                                float(GameObjects[x + 16]),
                                float(GameObjects[x + 18]),
                                0,
                            ),
                            (0, 0, 0, 1),
                        ]
                        objrot = Matrix(m)

                        # Matrix is constructed, now apply it!
                        bpy.context.object.rotation_euler[0] = objrot.to_euler()[0]
                        bpy.context.object.rotation_euler[2] = (
                            objrot.to_euler()[1] + 1.5708
                        )  # offset by 90 degrees
                        bpy.context.object.rotation_euler[1] = (
                            objrot.to_euler()[2] * -1
                        )  # Y axis is inverse in BZ

                # Set active collection to PATHS.
                layer_collection = bpy.context.view_layer.layer_collection.children[
                    "PATHS"
                ]
                bpy.context.view_layer.active_layer_collection = layer_collection
                # Create 1 object for every [AiPath] detected in list.

                def MakeAiPathPoint(
                    PathPointScale, NameShowFlag, ObjectFlag, ObjectName
                ):
                    # Generate first point. This is often used to spawn custom stuff or respawning items.
                    bpy.ops.mesh.primitive_cube_add(
                        size=2,
                        enter_editmode=False,
                        align="WORLD",
                        location=(0, 0, 0),
                        scale=(PathPointScale, PathPointScale, PathPointScale),
                    )
                    bpy.context.object.name = ObjectName

                    UserPathObject = bpy.context.view_layer.objects.active
                    bpy.context.object.display_type = "BOUNDS"
                    if NameShowFlag == 1:
                        bpy.context.object.show_name = True
                    bpy.context.object.show_in_front = True

                    # If flag is set, determine whether or not the object can be represented with a model.
                    if ObjectFlag == True:
                        # Get all the objects within BZ_Unit_Models collection. Needed to apply 3d models to placed objects.
                        BZ_Unit_Models = []
                        for collection in bpy.data.collections:
                            if collection.name == "BZ_Unit_Models":
                                for obj in collection.all_objects:
                                    BZ_Unit_Models.append(obj)

                        # Try to locate an object within BZ_Unit_Models collection.
                        for q in range(0, len(BZ_Unit_Models)):
                            if (
                                BZ_Unit_Models[q].name.lower()
                                in UserPathObject.name.lower()
                            ):
                                # If a model is found, duplicate the mesh and join it with the path point.
                                bpy.ops.object.select_all(action="DESELECT")
                                bpy.data.objects[BZ_Unit_Models[q].name].select_set(
                                    True
                                )
                                bpy.context.view_layer.objects.active = BZ_Unit_Models[
                                    q
                                ]
                                bpy.ops.object.duplicate_move(
                                    OBJECT_OT_duplicate={
                                        "linked": False,
                                        "mode": "TRANSLATION",
                                    },
                                    TRANSFORM_OT_translate={
                                        "value": (0, 0, 0),
                                        "orient_type": "GLOBAL",
                                        "orient_matrix": (
                                            (1, 0, 0),
                                            (0, 1, 0),
                                            (0, 0, 1),
                                        ),
                                        "orient_matrix_type": "GLOBAL",
                                        "constraint_axis": (False, False, False),
                                        "mirror": False,
                                        "use_proportional_edit": False,
                                        "proportional_edit_falloff": "SMOOTH",
                                        "proportional_size": 1,
                                        "use_proportional_connected": False,
                                        "use_proportional_projected": False,
                                        "snap": False,
                                        "snap_elements": {
                                            "VERTEX",
                                            "FACE",
                                            "FACE_NEAREST",
                                        },
                                        "use_snap_project": True,
                                        "snap_target": "CENTER",
                                        "use_snap_self": True,
                                        "use_snap_edit": True,
                                        "use_snap_nonedit": True,
                                        "use_snap_selectable": False,
                                        "snap_point": (0, 0, 0),
                                        "snap_align": False,
                                        "snap_normal": (0, 0, 0),
                                        "gpencil_strokes": False,
                                        "cursor_transform": False,
                                        "texture_space": False,
                                        "remove_on_cancel": False,
                                        "view2d_edge_pan": False,
                                        "release_confirm": False,
                                        "use_accurate": False,
                                        "use_automerge_and_split": False,
                                    },
                                )
                                bpy.context.view_layer.objects.active = UserPathObject
                                bpy.data.objects[UserPathObject.name].select_set(True)
                                bpy.ops.object.join()
                                bpy.context.object.display_type = "WIRE"
                                bpy.context.object.show_in_front = False

                                # Move the object into the AI path point collection.
                                break

                for x in range(0, len(AiPathData)):
                    if AiPathData[x] == "[AiPath]\n":

                        # For whatever reason, there can be path points which don't exist anywhere
                        # in the map and are created without labels. Ignore these.
                        if "label = " in AiPathData[x + 4]:
                            bpy.ops.object.select_all(action="DESELECT")
                            MakeAiPathPoint(
                                5, 1, True, AiPathData[x + 4][8:].replace("\n", "")
                            )

                            ParentPathPoint = bpy.context.object

                            # Position the path
                            bpy.context.object.location[1] = (
                                float(AiPathData[x + 9])
                            ) * -1 - float(MinX)
                            bpy.context.object.location[0] = (
                                float(AiPathData[x + 11])
                            ) - float(MinZ)

                            # Shrinkwrap path to terrain mesh.
                            bpy.ops.object.constraint_add(type="SHRINKWRAP")
                            bpy.context.object.constraints[
                                "Shrinkwrap"
                            ].shrinkwrap_type = "PROJECT"
                            bpy.context.object.constraints[
                                "Shrinkwrap"
                            ].project_axis = "POS_Z"
                            bpy.context.object.constraints["Shrinkwrap"].target = (
                                bpy.data.objects[UserMap.name]
                            )
                            bpy.context.object.constraints[
                                "Shrinkwrap"
                            ].project_axis_space = "WORLD"
                            bpy.context.object.constraints[
                                "Shrinkwrap"
                            ].use_project_opposite = True

                            # Get number of points.
                            IncrementPoint = 0
                            for y in range(1, int(AiPathData[x + 6])):

                                PrevAiPathPoint = bpy.context.object
                                MakeAiPathPoint(
                                    3, 0, False, AiPathData[x + 4][8:].replace("\n", "")
                                )

                                # Shrinkwrap path to terrain mesh.
                                bpy.ops.object.constraint_add(type="SHRINKWRAP")
                                bpy.context.object.constraints[
                                    "Shrinkwrap"
                                ].shrinkwrap_type = "PROJECT"
                                bpy.context.object.constraints[
                                    "Shrinkwrap"
                                ].project_axis = "POS_Z"
                                bpy.context.object.constraints["Shrinkwrap"].target = (
                                    bpy.data.objects[UserMap.name]
                                )
                                bpy.context.object.constraints[
                                    "Shrinkwrap"
                                ].project_axis_space = "WORLD"
                                bpy.context.object.constraints[
                                    "Shrinkwrap"
                                ].use_project_opposite = True

                                # Path point name
                                bpy.context.object.name = (
                                    AiPathData[x + 4][8:].replace("\n", "")
                                    + "_pathpoint"
                                )

                                # Position the path
                                bpy.context.object.location[1] = (
                                    float(AiPathData[x + 13 + IncrementPoint])
                                ) * -1 - float(MinX)
                                bpy.context.object.location[0] = (
                                    float(AiPathData[x + 15 + IncrementPoint])
                                ) - float(MinZ)
                                IncrementPoint += 4

                                if y == 1:
                                    # These are child points, parent them to the first-created point.
                                    bpy.context.object.parent = ParentPathPoint
                                    bpy.context.object.matrix_parent_inverse = (
                                        ParentPathPoint.matrix_world.inverted()
                                    )  # account for parent space.
                                else:
                                    bpy.context.object.parent = PrevAiPathPoint
                                    bpy.context.object.matrix_parent_inverse = (
                                        PrevAiPathPoint.matrix_world.inverted()
                                    )  # account for parent space.

                # All of the shrinkwrap constraints on every object need to be re-assigned since the map was deleted/recreated.
                for ob in bpy.data.objects:
                    try:
                        ob.constraints["Shrinkwrap"].target = bpy.data.objects[
                            UserMap.name
                        ]
                    except KeyError:
                        pass

            # Move BZ_Unit_Models out of user's view.
            for collection in bpy.data.collections:
                if collection.name == "BZ_Unit_Models":
                    for obj in collection.all_objects:
                        obj.location[0] = 0
                        obj.location[1] = 5000
                        obj.location[2] = 0

        except UnicodeDecodeError:
            self.report(
                {"WARNING"},
                "BZMapIO:  BZN file is binary and cannot be read. Re-save it using the game's asciisave launch argument.",
            )

        # Remove the tile selector interface if it is present. Textures must be re-loaded.

        for ob in bpy.data.objects:
            if "tileselector" in ob.name.lower():
                bpy.data.objects[ob.name].hide_select = False
                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = ob
                bpy.data.objects[ob.name].select_set(True)
                bpy.ops.object.delete(use_global=False, confirm=False)

        # Make sure the user's new map is selected, and that
        # its origin is geometrically centered to allow symmetry to work.
        bpy.ops.object.select_all(action="DESELECT")
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                bpy.data.objects[ob.name].select_set(True)
                bpy.context.view_layer.objects.active = ob
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

        return {"FINISHED"}


######################################################################################################
######################################################################################################
#####  EXPORT ########################################################################################
######################################################################################################
######################################################################################################

# Ok I have a confession to make... im an extremely experienced 3d developer, but i'm not a super smart programmer. DividebyZero is.
# So... instead of directly using the data I instead, like a muggle, manually construct a .CSV file then feed that to the script DividebyZero built
# so that it can deal with all the byte code black magic that makes the HG2 file work.


class bzmapexport(bpy.types.Operator):
    bl_idname = "button.bzmapexport"
    bl_label = "Export Map (.hg2)"

    def execute(self, context):

        # Make sure user is in layout workspace and in object mode.
        bpy.context.window.workspace = bpy.data.workspaces["Layout"]
        bpy.ops.object.mode_set(mode="OBJECT")

        # Make sure the user's map is selected
        bpy.ops.object.select_all(action="DESELECT")
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                bpy.data.objects[ob.name].select_set(True)
                bpy.data.objects[ob.name].hide_select = (
                    False  # The map must be selectable because this script depends on selections to function.
                )
                bpy.context.view_layer.objects.active = ob

        # Apply transforms to the user map in case they moved or scaled it.
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Make sure the user's map is selected, user maps always contain .HG2 in them.
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                obj = ob
                bpy.data.objects[ob.name].select_set(True)

        # acquire up/down coordinate from every single vertex.
        verts = [vert.co for vert in obj.data.vertices]
        plain_verts = [vert.to_tuple() for vert in verts]

        # BZ maps have a min/max floor/ceiling of 0 - 409.5m
        # If any vertex points exceed these boundaries they'll be moved to the floor/ceiling.

        for x in range(0, len(plain_verts)):
            if obj.data.vertices[x].co[2] < 0:
                obj.data.vertices[x].co[2] = 0
            elif obj.data.vertices[x].co[2] > 409.5:
                obj.data.vertices[x].co[2] = 409.5

        # Re-read the vertex points, as they may have changed.
        verts = [vert.co for vert in obj.data.vertices]
        plain_verts = [vert.to_tuple() for vert in verts]

        MapDepth = (
            int(bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string) * 256
        )  # Depth
        MapWidth = (
            int(bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string) * 256
        )  # Width

        # Concatenate data into a .CSV file.

        CSVData = []

        # HEADER
        H1 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String"].string + ","
        )  # structure version
        H2 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.001"].string + ","
        )  # zone bits
        H3 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string + ","
        )  # map width
        H4 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string + ","
        )  # map depth
        H5 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.004"].string
        )  # map version

        CSVData.append([H1, H2, H3, H4, H5])

        ScriptGeneratedCSVFilePath = context.scene.BZMapFile.lower().replace(
            ".hg2", ".csv"
        )

        f = open(context.scene.BZMapFile.lower().replace(".hg2", ".csv"), "w")

        # INSERT HEADER
        f.write(H1 + H2 + H3 + H4 + H5 + "\n")

        # INSERT DATA
        for x in range(0, len(plain_verts)):
            if x % MapDepth == 0 and x != 0:
                f.write("\n")
            elif x != 0:
                f.write(",")
            f.write(str(round(plain_verts[x][2] * 10)).zfill(4))
        f.close()

        # At this point, the CSV file is generated, the script below can now pull information from it and generate a
        # working .HG2 file.

        class HG2:
            structure_version = 1
            zone_bits = 8
            map_width = 0
            map_depth = 0
            map_version = 10
            heights = None

            def read(self, stream):
                self.structure_version = self.read_ushort(stream)
                self.zone_bits = self.read_ushort(stream)
                self.map_width = self.read_ushort(stream)
                self.map_depth = self.read_ushort(stream)
                self.map_version = self.read_uint(stream)

                zcount = self.map_width * self.map_depth
                vcount = 2 ** (2 * self.zone_bits) * zcount
                self.heights = list(
                    struct.unpack("<" + str(vcount) + "H", stream.read(2 * vcount))
                )
                for i, h in enumerate(self.heights):
                    self.heights[i] = (
                        h & 0x1FFF
                    )  # Note: This clobbers the four unused flag bits

                return self

            def read_file(self, filepath):
                with open(filepath, "rb") as stream:
                    return self.read(stream)

            def write(self, stream):
                self.write_ushort(stream, self.structure_version)
                self.write_ushort(stream, self.zone_bits)
                self.write_ushort(stream, self.map_width)
                self.write_ushort(stream, self.map_depth)
                self.write_uint(stream, self.map_version)

                zcount = self.map_width * self.map_depth
                vcount = 2 ** (2 * self.zone_bits) * zcount
                stream.write(struct.pack("<" + str(vcount) + "H", *self.heights))

            def write_file(self, filepath):
                with open(filepath, "wb") as stream:
                    return self.write(stream)

            def read_ushort(self, stream):
                return int.from_bytes(stream.read(2), "little")

            def read_uint(self, stream):
                return int.from_bytes(stream.read(4), "little")

            def write_ushort(self, stream, v):
                stream.write(int.to_bytes(v, 2, "little"))

            def write_uint(self, stream, v):
                stream.write(int.to_bytes(v, 4, "little"))

            def write_csv_file(self, filepath):
                zone_length = 2 ** (self.zone_bits)
                vwidth = zone_length * self.map_width
                vdepth = zone_length * self.map_depth

                heights2 = [[0 for i in range(vwidth)] for j in range(vdepth)]

                for z in range(vdepth):
                    for x in range(vwidth):
                        sub_x = x % zone_length
                        sub_z = z % zone_length
                        zone_x = x // zone_length
                        zone_z = z // zone_length
                        i = (
                            (zone_z * self.map_width + zone_x) * zone_length + sub_z
                        ) * zone_length + sub_x
                        heights2[z][x] = f"{self.heights[i]:04}"

                with open(filepath, "w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(
                        [
                            self.structure_version,
                            self.zone_bits,
                            self.map_width,
                            self.map_depth,
                            self.map_version,
                        ]
                    )
                    writer.writerows(heights2)

            def read_csv_file(self, filepath):
                with open(filepath, "r", newline="") as stream:
                    reader = csv.reader(stream)
                    header = next(reader)
                    (
                        self.structure_version,
                        self.zone_bits,
                        self.map_width,
                        self.map_depth,
                        self.map_version,
                    ) = (int(header[i]) for i in range(5))

                    heights2 = [[int(h) for h in row] for row in reader]

                zone_length = 2 ** (self.zone_bits)
                zone_area = zone_length**2
                vwidth = zone_length * self.map_width
                vdepth = zone_length * self.map_depth

                self.heights = [0 for i in range(vwidth * vdepth)]
                for i in range(vwidth * vdepth):
                    zone_i, sub_i = divmod(i, zone_area)
                    zone_z, zone_x = divmod(zone_i, self.map_width)
                    sub_z, sub_x = divmod(sub_i, zone_length)

                    x = zone_x * zone_length + sub_x
                    z = zone_z * zone_length + sub_z
                    self.heights[i] = heights2[z][x]
                return self

        filepath = ScriptGeneratedCSVFilePath

        # Overwrite original HG2 file.
        targetpath = context.scene.BZMapFile

        # Convert the file!
        HG2().read_csv_file(filepath).write_file(targetpath)

        # Clear the CSV file. It is no longer needed.
        os.remove(context.scene.BZMapFile.lower().replace(".hg2", ".csv"))

        # The TRN file must be updated to use new map size.
        TRNFile = context.scene.BZMapFile.lower()
        TRNFile = TRNFile.replace(".hg2", ".trn")

        with open(TRNFile) as file:
            TRNData = [line for line in file]
        file.close()

        # Change the depth and width in the TRN file to reflect updated map.
        for x in range(0, len(TRNData)):
            if "width" in TRNData[x][:5].lower():
                TRNData[x] = (
                    "Width=" + str(1280 * (int(H3.replace(",", "")))) + "\n"
                )  # need to remove the comma used for CSV
            if "depth" in TRNData[x][:5].lower():
                TRNData[x] = "Depth=" + str(1280 * (int(H4.replace(",", "")))) + "\n"

        TRNEdit = open(TRNFile, "w")
        TRNEdit.writelines(TRNData)
        TRNEdit.close()

        # One more thing. The user's map probably has an LGT file. This needs to be removed so it can be regenerated
        # with the updated map upon a game level researt. Remove it if present.
        if (
            os.path.isfile(context.scene.BZMapFile.lower().replace(".hg2", ".lgt"))
            == True
        ):
            os.remove(context.scene.BZMapFile.lower().replace(".hg2", ".lgt"))

        # If user has export objects enabled, also import the objects from the BZN file.
        ExportBZNCheckbox = context.scene.BZMapIO_Toggles.ExportBZN
        self.report(
            {"INFO"},
            "BZMapIO: "
            + os.path.basename(bpy.context.scene.BZMapFile.lower())
            + "    Saved/Updated",
        )

        if ExportBZNCheckbox == True:

            # We need data from the TRN file that was pulled to ensure we're accounting for the minx, minz, and height values
            # which modify objects positions in-game.

            MinX = 0
            MinZ = 0
            MinHeight = 0

            # I don't know why, but for whatever reason some people's maps will have duplicate
            # entries of minx/minz/height... these flags make sure only the first instance of these are considered.
            MinXFound = False
            MinZFound = False
            HeightFound = False
            for x in range(0, len(TRNData)):
                if "MinX" in TRNData[x]:
                    if MinXFound == False:
                        MinXValue = TRNData[x].find("=")
                        MinX = TRNData[x][MinXValue + 1 :].replace("\n", "")
                        MinXFound = True
                if "MinZ" in TRNData[x]:
                    if MinZFound == False:
                        MinZValue = TRNData[x].find("=")
                        MinZ = TRNData[x][MinZValue + 1 :].replace("\n", "")
                        MinZFound = True
                if (
                    TRNData[x][:6].lower() == "height" and x < 10
                ):  # I only check the first 10 lines of BZN file, theres no "clean" way to check this reliably.
                    if HeightFound == False:
                        MinHeightValue = TRNData[x].find("=")
                        MinHeight = TRNData[x][MinHeightValue + 1 :].replace("\n", "")
                        HeightFound = True

            # Before exporting check for objects the user drag-and-dropped from the
            # object library. The objects are placed in BZ_Unit_Models collection.
            ItemstoMove = []
            for collection in bpy.data.collections:
                if collection.name == "BZ_Unit_Models":
                    for obj in collection.all_objects:
                        if obj.name.find(".") != -1:
                            ItemstoMove.append(obj)

            # User-placed items need to be moved over to GAMEOBJECTS so they can be processed.
            for x in range(0, len(ItemstoMove)):
                bpy.ops.object.select_all(action="DESELECT")
                bpy.data.objects[ItemstoMove[x].name].select_set(True)
                bpy.context.view_layer.objects.active = ItemstoMove[x]
                bpy.ops.object.move_to_collection(collection_index=2)

            # Collect all gameobjects.
            # We also need to specify a "TYPE" because the info from the object differs.

            BZ_USER_ITEMS = []
            for collection in bpy.data.collections:
                if collection.name == "GAMEOBJECTS":
                    for obj in collection.all_objects:

                        # Make object active to pull data from it.
                        bpy.context.view_layer.objects.active = obj

                        # Every object in this list needs to be paired with a "type"
                        # We do this by scanning the label in the object in various ways.

                        PropertyKey = "-10"
                        TargetLabel = ""
                        IsProducer = False
                        IsPlayer = False
                        for key, value in bpy.context.object.data.items():
                            if "PrjID [1] =" in value:
                                PropertyKey = key
                                break
                        for key, value in bpy.context.object.data.items():
                            if int(key) == int(PropertyKey) + 1:
                                TargetLabel = value.replace("\n", "")
                                break

                        # Third check because we need to identify whether or not
                        # this is a production unit.
                        for key, value in bpy.context.object.data.items():
                            if "timeDeploy [1] =" in value:
                                IsProducer = True
                                break

                        # Finally, we check whether or not this is a player object
                        for key, value in bpy.context.object.data.items():
                            if "player" in value:
                                IsPlayer = True
                                break

                        # DETERMINE TYPE OF OBJECT BY READING ODF HANDLE.
                        # Types consist of the following:
                        # Wingman
                        # Turret/Howie
                        # Producer
                        # Scavenger
                        # Power supply
                        # Building
                        # APC
                        if TargetLabel != "":
                            UnitType = "UNKNOWN"
                            UnitSubType = "UNKNOWN"
                            if IsProducer == True:
                                UnitType = "PRODUCER"
                                # Get the subtype, as the constructor, armory, recycler and factory all have unique pointers.
                                if TargetLabel[2:6].lower() == "recy":
                                    UnitSubType = "RECYCLER"
                                if TargetLabel[2:6].lower() == "cnst":
                                    UnitSubType = "CONSTRUCTOR"
                                if TargetLabel[2:5].lower() == "muf":
                                    UnitSubType = "FACTORY"
                                if TargetLabel[2:5].lower() == "slf":
                                    UnitSubType = "ARMORY"

                            else:
                                # a vehicle will always have v as second character
                                if TargetLabel[1].lower() == "v":
                                    UnitType = "WINGMAN"

                                    # is it a scav?
                                    if TargetLabel[2:6].lower() == "scav":
                                        UnitType = "SCAVENGER"

                                    # is it a tug?
                                    if TargetLabel[2:6].lower() == "haul":
                                        UnitType = "TUG"

                                    # is it a turret or howie?
                                    if (
                                        TargetLabel[2:6].lower() == "turr"
                                        or TargetLabel[2:6].lower() == "artl"
                                    ):
                                        UnitType = "TURRETTANK"

                                    # is it an APC?
                                    if TargetLabel[2:5].lower() == "apc":
                                        UnitType = "APC"

                                # Building will always has b is second character
                                # Since powerups have same data structure we also classify those under the building category.
                                if (
                                    TargetLabel[1].lower() == "b"
                                    or TargetLabel[0:2] == "ap"
                                ):
                                    UnitType = "BUILDING"

                                # Determine if this is a gun tower. Despite being a building, it uses same structure as wingmen.
                                if TargetLabel[2:6].lower() == "towe":
                                    UnitType = "WINGMAN"

                                # Determine if this is a silo. Silos have bits of info that no other building has for whatever reason.
                                if TargetLabel[2:6].lower() == "silo":
                                    UnitSubType = "SILO"

                            if IsPlayer == True:
                                UnitType = "WINGMAN"

                            # default to building if unknown
                            if UnitType == "UNKNOWN":
                                UnitType = "BUILDING"

                            BZ_USER_ITEMS.append([obj, UnitType, UnitSubType])

            # Now the BZN file is ready to be re-constructed.

            f = open(context.scene.BZMapFile.lower().replace(".hg2", ".bzn"), "w")

            # WRITE HEADER. I just use static values with user filenames in place.
            UserFileName = os.path.basename(bpy.context.scene.BZMapFile.lower())
            f.write("version [1] =\n")
            f.write("2016\n")
            f.write("binarySave [1] =\n")
            f.write("false\n")
            f.write("msn_filename = " + UserFileName.replace(".hg2", ".bzn") + "\n")
            f.write("seq_count [1] =\n")
            f.write(str(len(BZ_USER_ITEMS)) + "\n")  # Number of objects in scene
            f.write("missionSave [1] =\n")
            f.write("true\n")
            f.write("TerrainName = " + UserFileName.replace(".hg2", "") + "\n")
            f.write("size [1] =\n")
            f.write(str(len(BZ_USER_ITEMS)) + "\n")  # Number of objects in scene

            # For every GAMEOBJECT, read its data.
            for x in range(0, len(BZ_USER_ITEMS)):

                UnitType = BZ_USER_ITEMS[x][1]
                UnitSubType = BZ_USER_ITEMS[x][2]
                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = BZ_USER_ITEMS[x][0]
                bpy.data.objects[BZ_USER_ITEMS[x][0].name].select_set(True)

                # Constraint transform and the thing the user actually sees are two different things.
                # Apply essentially makes the transform of the object match exactly the constraint it is bound to.
                bpy.ops.object.visual_transform_apply()

                f.write("[GameObject]\nPrjID [1] =\n")

                # Pulls data from the object to insert into the BZN file.
                # For properties where value is listed on next line rather than on same line
                def GetObjectPropertyB(UserPrefix, ObjectValue, UserSuffix):
                    GetValue = 0
                    for key, value in bpy.context.object.data.items():
                        if GetValue == 1:
                            f.write(
                                UserPrefix + value.replace("\n", "") + UserSuffix + "\n"
                            )
                            break
                        if ObjectValue in value:
                            GetValue = 1
                        pass

                # POSITION
                BlenderX = (BZ_USER_ITEMS[x][0].location[1] * -1) + float(MinX)
                BlenderY = BZ_USER_ITEMS[x][0].location[2]
                BlenderZ = BZ_USER_ITEMS[x][0].location[0] + float(MinZ)

                # ROTATION
                eul = mathutils.Euler((0.0, math.radians(45.0), 0.0), "XYZ")
                eul[:] = (
                    bpy.context.object.rotation_euler[0] * -1,
                    (bpy.context.object.rotation_euler[2]) - 1.5708,
                    bpy.context.object.rotation_euler[1] * -1,
                )
                mat_rot = eul.to_matrix()

                GetObjectPropertyB("", "PrjID [1] =", "")
                f.write("seqno [1] =\n")
                f.write(str(x + 1) + "\n")
                f.write("pos [1] =\n")
                f.write("  x [1] =\n")
                f.write(str(round(BlenderX, 8)) + "\n")
                f.write("  y [1] =\n")
                f.write(str(round(BlenderY, 8)) + "\n")
                f.write("  z [1] =\n")
                f.write(str(round(BlenderZ, 8)) + "\n")
                f.write("team [1] =" + "\n")
                GetObjectPropertyB("", "team [1] =", "")
                GetObjectPropertyB("label = ", "PrjID [1] =", str(x + 1))
                f.write("isUser [1] =\n")
                GetObjectPropertyB("", "isUser [1] =", "")
                # Object address is basically just the sequence number in hex.
                ObjAddr = hex(x)
                ObjAddr = ObjAddr[2:]
                # Rotations in 3x3 matrix format
                f.write("obj_addr = " + str(ObjAddr.zfill(8)).upper() + "\n")
                f.write("transform [1] =\n")
                f.write("  right_x [1] =\n")
                f.write(str(mat_rot[0][0]) + "\n")
                f.write("  right_y [1] =\n")
                f.write(str(mat_rot[0][1]) + "\n")
                f.write("  right_z [1] =\n")
                f.write(str(mat_rot[0][2]) + "\n")
                f.write("  up_x [1] =\n")
                f.write(str(mat_rot[1][0]) + "\n")
                f.write("  up_y [1] =\n")
                f.write(str(mat_rot[1][1]) + "\n")
                f.write("  up_z [1] =\n")
                f.write(str(mat_rot[1][2]) + "\n")
                f.write("  front_x [1] =\n")
                f.write(str(mat_rot[2][0]) + "\n")
                f.write("  front_y [1] =\n")
                f.write(str(mat_rot[2][1]) + "\n")
                f.write("  front_z [1] =\n")
                f.write(str(mat_rot[2][2]) + "\n")
                f.write("  posit_x [1] =\n")
                f.write(str(round(BlenderX, 8)) + "\n")
                f.write("  posit_y [1] =\n")
                f.write(str(round(BlenderY, 8)) + "\n")
                f.write("  posit_z [1] =\n")
                f.write(str(round(BlenderZ, 8)) + "\n")

                # Not really sure what these undefined pointers are all about, but
                # the game crashes in the absence of their presence.
                if UnitType == "TUG" or UnitType == "RECYCLER":
                    f.write("undefptr = 00000000\n")

                # For the time being, I just give all map-placed turrettank class units the same stats as a regular turret.
                if UnitType == "TURRETTANK":
                    f.write("undeffloat [1] =\n")
                    f.write("2\n")
                    f.write("undeffloat [1] =\n")
                    f.write("0\n")
                    f.write("undeffloat [1] =\n")
                    f.write("8\n")
                    f.write("undeffloat [1] =\n")
                    f.write("0.7\n")
                    f.write("undefraw = 02000000\n")
                    f.write("undeffloat [1] =\n")
                    f.write("-0.00101133\n")
                    f.write("undefbool [1] =\n")
                    f.write("false\n")

                if UnitSubType == "SILO":
                    f.write("undefptr = 00000000\n")

                # I'm assuming the constructor data is purposed to preserve the calculated position/rotation of the
                # thing the constructor was building if the BZN was saved while it was building.
                # I just assume the map maker will never want to save a BZN of something in mid-build.
                if UnitSubType == "CONSTRUCTOR":
                    f.write("dropMat [1] =\n")
                    f.write("  right_x [1] =\n")
                    f.write("0\n")
                    f.write("  right_y [1] =\n")
                    f.write("0\n")
                    f.write("  right_z [1] =\n")
                    f.write("0\n")
                    f.write("  up_x [1] =\n")
                    f.write("0\n")
                    f.write("  up_y [1] =\n")
                    f.write("0\n")
                    f.write("  up_z [1] =\n")
                    f.write("0\n")
                    f.write("  front_x [1] =\n")
                    f.write("0\n")
                    f.write("  front_y [1] =\n")
                    f.write("0\n")
                    f.write("  front_z [1] =\n")
                    f.write("0\n")
                    f.write("  posit_x [1] =\n")
                    f.write("0\n")
                    f.write("  posit_y [1] =\n")
                    f.write("0\n")
                    f.write("  posit_z [1] =\n")
                    f.write("0\n")
                    f.write("dropClass [1] =\n")
                    f.write("\n")
                    f.write("lastRecycled [1] =\n")
                    f.write("0\n")

                if UnitType == "PRODUCER":
                    if UnitSubType == "RECYCLER":
                        f.write("undefptr = 00000000\n")
                    f.write("timeDeploy [1] =\n")
                    f.write("5\n")
                    f.write("timeUndeploy [1] =\n")
                    f.write("5\n")
                    f.write("undefptr = 00000000\n")
                    f.write("state = 00000000\n")
                    f.write("delayTimer [1] =\n")
                    f.write("-1e+030\n")
                    f.write("nextRepair [1] =\n")
                    f.write("50.567\n")
                    f.write("buildClass [1] =\n")
                    f.write("\n")
                    f.write("buildDoneTime [1] =\n")
                    f.write("0\n")

                if UnitType == "SCAVENGER":
                    f.write("scrapHeld [1] =\n")
                    GetObjectPropertyB("", "scrapHeld [1] =", "")

                if UnitType == "APC":
                    f.write("soldierCount [1] =\n")
                    GetObjectPropertyB("", "soldierCount [1] =", "")
                    f.write("state = 00000000\n")

                if UnitType != "BUILDING":
                    f.write("abandoned [1] =\n")
                    f.write("0\n")
                    f.write("cloakState = 00000000\n")
                    f.write("cloakTransBeginTime [1] =\n")
                    f.write("0\n")
                    f.write("cloakTransEndTime [1] =\n")
                    f.write("0\n")

                f.write("illumination [1] =\n")
                f.write("1\n")
                f.write("pos [1] =\n")
                f.write("  x [1] =\n")

                # I really don't get why the BZN file needs 3 clones of transform property present in the file.
                f.write(str(round(BlenderX, 8)) + "\n")
                f.write("  y [1] =\n")
                f.write(str(round(BlenderY, 8)) + "\n")
                f.write("  z [1] =\n")
                f.write(str(round(BlenderZ, 8)) + "\n")
                f.write("euler =\n")
                f.write(" mass [1] =\n")
                GetObjectPropertyB("", "mass [1] =", "")

                # This section is just velocity, we pretty much ignore this and use default values.
                f.write(" mass_inv [1] =\n")
                f.write("0.000666667\n")
                f.write(" v_mag [1] =\n")
                f.write("1.62541\n")
                f.write(" v_mag_inv [1] =\n")
                f.write("1.62541\n")
                f.write(" I [1] =\n")
                f.write("1500\n")
                f.write(" k_i [1] =\n")
                f.write("1\n")
                f.write(" v [1] =\n")
                f.write("  x [1] =\n")
                f.write("0\n")
                f.write("  y [1] =\n")
                f.write("0\n")
                f.write("  z [1] =\n")
                f.write("0\n")
                f.write(" omega [1] =\n")
                f.write("  x [1] =\n")
                f.write("0\n")
                f.write("  y [1] =\n")
                f.write("0\n")
                f.write("  z [1] =\n")
                f.write("0\n")
                f.write(" Accel [1] =\n")
                f.write("  x [1] =\n")
                f.write("0\n")
                f.write("  y [1] =\n")
                f.write("0\n")
                f.write("  z [1] =\n")
                f.write("0\n")

                # Yeah... we need multiple copies of the sequence too apparently.
                f.write("seqNo [1] =\n")
                f.write(str(x + 1) + "\n")
                f.write("name = \n")
                f.write("isCritical [1] =\n")
                f.write("false\n")
                f.write("isObjective [1] =\n")
                f.write("false\n")
                f.write("isSelected [1] =\n")
                f.write("false\n")
                f.write("isVisible [1] =\n")
                f.write("2\n")
                f.write("seen [1] =\n")
                f.write("1\n")
                f.write("healthRatio [1] =\n")
                f.write("0\n")
                f.write("curHealth [1] =\n")
                GetObjectPropertyB("", "curHealth [1] =", "")
                f.write("maxHealth [1] =\n")
                GetObjectPropertyB("", "maxHealth [1] =", "")
                f.write("ammoRatio [1] =\n")
                f.write("0\n")
                f.write("curAmmo [1] =\n")
                GetObjectPropertyB("", "curAmmo [1] =", "")
                f.write("maxAmmo [1] =\n")
                GetObjectPropertyB("", "maxAmmo [1] =", "")
                f.write("priority [1] =\n")
                f.write("0\n")
                f.write("what = 00000000\n")
                f.write("who [1] =\n")
                f.write("0\n")
                f.write("where = 00000000\n")
                f.write("param [1] =\n")
                f.write("\n")
                f.write("aiProcess [1] =\n")
                GetObjectPropertyB("", "aiProcess [1] =", "")
                f.write("isCargo [1] =\n")
                f.write("false\n")
                f.write("independence [1] =\n")
                f.write("1\n")
                f.write("curPilot [1] =\n")
                GetObjectPropertyB("", "curPilot [1] =", "")
                f.write("perceivedTeam [1] =\n")
                GetObjectPropertyB("", "perceivedTeam [1]", "")

            # After last object is written, we write in the mission type
            # and what appears to be a sequence value which denotes the last object.

            f.write("name = MultSTMission\n")
            # Object address is basically just the sequence number in hex.
            ObjAddr = hex(x + 2)
            ObjAddr = ObjAddr[2:]
            f.write("sObject = " + str(ObjAddr.zfill(8)).upper() + "\n")

            # Now write path objects. We ignore AOIs, mostly depreciated by lua anyhow.
            f.write("[AiMission]\n")
            f.write("[AOIs]\n")
            f.write("size [1] =\n")
            f.write("0\n")

            # Collect all path objects
            BZ_PATH_ITEMS = []
            for collection in bpy.data.collections:
                if collection.name == "PATHS":
                    for obj in collection.all_objects:
                        if obj.parent == None:  # we care about topmost path points.
                            BZ_PATH_ITEMS.append(obj)

            f.write("[AiPaths]\n")
            f.write("count [1] =\n")
            f.write(str(len(BZ_PATH_ITEMS)) + "\n")

            # For each path point found, process it! We do make some fairly safe assumptions here...
            # Any parent path point with two underscores are assumed to be respawning objects.
            # Furthermore, objects with dots in their name are assumed to be cloned path points.
            for x in range(0, len(BZ_PATH_ITEMS)):

                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = BZ_PATH_ITEMS[x]
                bpy.data.objects[BZ_PATH_ITEMS[x].name].select_set(True)

                # Check for child path points. If present, their coordinates must be added
                # to the end of the parent path sequentially.
                ChildPathPoints = []
                if BZ_PATH_ITEMS[x].children == ():
                    pass
                else:
                    ChildCount = 1
                    ChildPathSearch = BZ_PATH_ITEMS[x].children[0]
                    while ChildPathSearch != ():
                        try:
                            ChildPathPoints.append(ChildPathSearch)
                            ChildPathSearch = ChildPathSearch.children[0]
                            ChildCount += 1
                        except IndexError:
                            pass
                            break

                # Constraint transform and the thing the user actually sees are two different things.
                # Apply essentially makes the transform of the object match exactly the constraint it is bound to.
                bpy.ops.object.visual_transform_apply()

                BlenderX = (BZ_PATH_ITEMS[x].matrix_world.translation[1] * -1) + float(
                    MinX
                )
                BlenderZ = BZ_PATH_ITEMS[x].matrix_world.translation[0] + float(MinZ)

                NameAdjust = BZ_PATH_ITEMS[x].name
                if NameAdjust.find(".") != -1:
                    NameAdjust = BZ_PATH_ITEMS[x].name[
                        : BZ_PATH_ITEMS[x].name.find(".")
                    ]  # Remove clone suffix.

                # Next, determine whether or not this is a path object or respawning object.
                PathNameRespawnTime = NameAdjust.find("_")
                PathNameSequence = NameAdjust.find("_", PathNameRespawnTime + 1)

                # If both sequence and respawntime underscores are found, treat it as respawning object.

                PathName = NameAdjust[: NameAdjust.find("_")]
                PathRespawn = NameAdjust[PathNameRespawnTime + 1 : PathNameSequence]
                PathSeq = NameAdjust[PathNameSequence - 1]
                FullPathName = PathName + "_" + PathRespawn + "_" + str(x + 1)

                f.write("[AiPath]\n")
                f.write(
                    "old_ptr = 00000000\n"
                )  # I don't know what this is for, I leave it at 0.
                f.write(
                    "size [1] =\n"
                )  # This is the # of characters in the name of the object.
                if PathNameSequence != -1 and PathNameRespawnTime != -1:
                    f.write(str(len(FullPathName)) + "\n")
                    f.write("label = " + FullPathName + "\n")
                else:
                    f.write(str(len(NameAdjust)) + "\n")
                    f.write("label = " + NameAdjust + "\n")
                f.write("pointCount [1] =\n")
                f.write(str(len(ChildPathPoints) + 1) + "\n")
                f.write("points [" + str(len(ChildPathPoints) + 1) + "] =\n")
                f.write("  x [1] =\n")
                f.write(str(BlenderX) + "\n")
                f.write("  z [1] =\n")
                f.write(str(BlenderZ) + "\n")

                # If the path has child points, iterate through all of them to insert their position coordinates.
                if ChildPathPoints != []:
                    for q in range(0, len(ChildPathPoints)):
                        # POSITION
                        BlenderX = (
                            ChildPathPoints[q].matrix_world.translation[1] * -1
                        ) + float(MinX)
                        BlenderZ = ChildPathPoints[q].matrix_world.translation[
                            0
                        ] + float(MinZ)
                        f.write("  x [1] =\n")
                        f.write(str(BlenderX) + "\n")
                        f.write("  z [1] =\n")
                        f.write(str(BlenderZ) + "\n")
                f.write("pathType = 00000000\n")

            f.close()

            # Display message to say map was saved.
            self.report(
                {"INFO"},
                "BZMapIO: "
                + os.path.basename(bpy.context.scene.BZMapFile.lower())
                + " & objects Saved/Updated",
            )

        return {"FINISHED"}


######################################################################################################
######################################################################################################
#####  BUTTON FUNCTIONS ##############################################################################
######################################################################################################
######################################################################################################

# SET RESPAWNING
# Gives the user a way to easily set respawning objects on the map. Automatically handles sequencing and other stuff.


class bzbutton_setrespawning(bpy.types.Operator):
    bl_idname = "button.bzsetrespawning"
    bl_label = "Sel Set Respawning"
    bl_description = "Takes the selected objects and converts them to path points which respawn objects."

    def execute(self, context):

        # We only move selected objects.
        ItemstoMove = []
        for collection in bpy.data.collections:
            if collection.name == "BZ_Unit_Models":
                for obj in collection.all_objects:
                    if obj.name.find(".") != -1 and obj.select_get() == True:
                        ItemstoMove.append(obj)

        Respawn = context.scene.BZMapIO_Toggles.RespawnTime
        for obj in ItemstoMove:
            if (
                obj.users_collection[0].name != "PATHS"
                and obj.users_collection[0].name != "Scene Collection"
            ):
                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = obj
                bpy.data.objects[obj.name].select_set(True)
                obj.display_type = "WIRE"
                bpy.context.object.show_name = True
                bpy.ops.object.move_to_collection(collection_index=3)
                GetSuffix = obj.name.find(".")
                obj.name = (
                    obj.name[:GetSuffix] + "_" + Respawn + "_1"
                )  # We don't care about sequence numbers until export.

                # Draw a box, helps user identify the object as a "path" point.
                bpy.ops.mesh.primitive_cube_add(
                    size=2,
                    enter_editmode=False,
                    align="WORLD",
                    location=(obj.location[0], obj.location[1], obj.location[2]),
                    scale=(5, 5, 5),
                )
                bpy.data.objects[obj.name].select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.join()

        # This is performed in case the user's object is already a respawning
        # object, in which case its merely updated.

        for collection in bpy.data.collections:
            if collection.name == "PATHS":
                for obj in collection.all_objects:
                    if obj.select_get() == True:
                        TruncateIndex = obj.name.find("_")
                        obj.name = (
                            obj.name[:TruncateIndex]
                            + "_"
                            + str(context.scene.BZMapIO_Toggles.RespawnTime)
                            + "_1"
                        )

        return {"FINISHED"}


# REPOSITION MAP BUTTON
# This function uses shrinkwrap to enable the user to re-position the map without modifying vertex point order.
# It works as an on/off mode toggle.


class bzbutton_transform(bpy.types.Operator):
    bl_idname = "button.bztransform"
    bl_label = "Reposition Map"
    bl_description = "Toggles a mode which lets the user move, rotate, and scale the entire map freely."

    def execute(self, context):

        bpy.ops.object.mode_set(mode="OBJECT")

        # Select user's map
        bpy.ops.object.select_all(action="DESELECT")
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                obj = ob
                bpy.data.objects[ob.name].select_set(True)
                bpy.context.view_layer.objects.active = ob

        # Determine whether or not this button needs to add or remove the reposition setup.
        BZTransformMode = 0

        for ob in bpy.data.objects:
            if "_ADJUSTOR" in ob.name:
                BZTransformMode = 1

        # MODE 0: ADD TRANSFORM SETUP
        # This is done by generating a new grid and applying a shrinkwrap modifier to it which
        # points to the user's map.

        if BZTransformMode == 0:

            # Turn the MaxHeightBoundsDisplay RED to visually indicate this is an edit mode.
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if "BZMap_MaxHeightBoundsDisplay" in ob.name:
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.context.object.active_material.diffuse_color = (1, 0, 0, 0.95)
                    bpy.context.object.color = (1, 0.0410447, 0.0288181, 1)
                    bpy.data.materials["BoundsDisplay"].node_tree.nodes[
                        "Principled BSDF"
                    ].inputs[0].default_value = (0.2, 0, 0, 1)

            # Get user's current working map using string match of "HG2"
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.data.objects[ob.name].hide_select = (
                        False  # The map must be selectable because this script depends on selections to function.
                    )
                    UserMap = ob
                    bpy.context.object.display_type = "BOUNDS"  # the user's map gets replaced in this operation, hide it visually

            # Duplicate the BZMapGenerator object
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ob.name == "BZMapGenerator":
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    _hide_view_clear_compat()  # Duplicate operation will REPLACE the object if its hidden... not a clue why.

            bpy.ops.object.duplicate_move(
                OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"},
                TRANSFORM_OT_translate={
                    "value": (0, 0, 0),
                    "orient_type": "GLOBAL",
                    "orient_matrix": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                    "orient_matrix_type": "GLOBAL",
                    "constraint_axis": (False, False, False),
                    "mirror": False,
                    "use_proportional_edit": False,
                    "proportional_edit_falloff": "SMOOTH",
                    "proportional_size": 1,
                    "use_proportional_connected": False,
                    "use_proportional_projected": False,
                    "snap": False,
                    "snap_elements": {"VERTEX", "FACE", "FACE_NEAREST"},
                    "use_snap_project": True,
                    "snap_target": "CENTER",
                    "use_snap_self": True,
                    "use_snap_edit": True,
                    "use_snap_nonedit": True,
                    "use_snap_selectable": False,
                    "snap_point": (0, 0, 0),
                    "snap_align": False,
                    "snap_normal": (0, 0, 0),
                    "gpencil_strokes": False,
                    "cursor_transform": False,
                    "texture_space": False,
                    "remove_on_cancel": False,
                    "view2d_edge_pan": False,
                    "release_confirm": False,
                    "use_accurate": False,
                    "use_automerge_and_split": False,
                },
            )

            bpy.context.object.name = "_ADJUSTOR_"

            # There are some cicumstances that can cause the adjustor to not be corner-fit to world origin. (Scaling down mostly)
            # Account for this here.
            bpy.ops.object.align(
                align_mode="OPT_1", relative_to="OPT_1", align_axis={"X"}
            )
            bpy.ops.object.align(
                align_mode="OPT_3", relative_to="OPT_1", align_axis={"Y"}
            )

            # Apply grid template into mesh to discard geo nodes but leave changes.
            bpy.ops.object.modifier_apply(modifier="GeometryNodes")
            bpy.ops.object.shade_smooth()

            # Apply shrinkwrap to duplicated map
            bpy.ops.object.modifier_add(type="SHRINKWRAP")
            bpy.context.object.modifiers["Shrinkwrap"].target = bpy.data.objects[
                UserMap.name
            ]
            bpy.context.object.modifiers["Shrinkwrap"].wrap_method = "PROJECT"
            bpy.context.object.modifiers["Shrinkwrap"].use_project_z = True
            bpy.context.object.modifiers["Shrinkwrap"].use_negative_direction = True

            # Flip map to invert x axis vertex index to match battlezone terrain.
            bpy.context.object.rotation_euler[0] = 3.14159
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

            # Hide the BZMapGenerator object.
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ob.name == "BZMapGenerator":
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    ob.hide_set(True)

            # RE-SELECT the user map and give them message.
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = UserMap
            bpy.data.objects[UserMap.name].select_set(True)
            bpy.ops.wm.tool_set_by_id(name="builtin.move")

            # There are edge cases where the resulting map ends up being blank. This fixes it.
            bpy.context.object.scale[0] = 0.9999
            bpy.context.object.scale[1] = 0.9999
            bpy.context.object.scale[2] = 0.9999

            self.report(
                {"WARNING"},
                "BZMapIO:  MOVE MODE ACTIVE. Re-position map and press button again to confirm change.",
            )

        # MODE 1: REMOVE TRANSFORM SETUP
        if BZTransformMode == 1:

            # Turn the MaxHeightBoundsDisplay BLACK to visually indicate exiting of edit mode.
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if "BZMap_MaxHeightBoundsDisplay" in ob.name:
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.context.object.active_material.diffuse_color = (0, 0, 0, 0.95)
                    bpy.context.object.color = (0, 0, 0, 1)
                    bpy.data.materials["BoundsDisplay"].node_tree.nodes[
                        "Principled BSDF"
                    ].inputs[0].default_value = (0, 0, 0, 1)

            # Essentially, all we do here is apply changes, then replace user's map with new version.
            for ob in bpy.data.objects:
                if "_ADJUSTOR_" in ob.name:
                    bpy.ops.object.select_all(action="DESELECT")
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    UserMap = ob
                if ".hg2_" in ob.name.lower():
                    OldMap = ob
                    OldMapName = ob.name

            # Apply modifier to new map which is now selected.
            bpy.ops.object.modifier_apply(modifier="Shrinkwrap")
            # Delete the old map... again because blender is dumb we have to reveal all objects before performing this operation.
            _hide_view_clear_compat()
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.ops.object.select_all(action="DESELECT")
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
            bpy.ops.object.delete(use_global=False, confirm=False)

            # Hide the BZMapGenerator
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ob.name == "BZMapGenerator":
                    ob.hide_set(True)

            # Rename the old map to match the name of the new one.
            UserMap.name = OldMapName
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = UserMap
            bpy.data.objects[UserMap.name].select_set(True)

            # BZ maps have a min/max floor/ceiling of 0 - 409.5m
            # If any vertex points exceed these boundaries they'll be moved to the floor/ceiling. Fix ths upon completion.

            verts = [vert.co for vert in UserMap.data.vertices]
            # acquire up/down coordinate from every single vertex.
            plain_verts = [vert.to_tuple() for vert in verts]

            for x in range(0, len(plain_verts)):
                if UserMap.data.vertices[x].co[2] < 0:
                    UserMap.data.vertices[x].co[2] = 0
                elif UserMap.data.vertices[x].co[2] > 409.5:
                    UserMap.data.vertices[x].co[2] = 409.5

            # Apply transforms to the user map
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            # Map needs a UVMap, vertex group, and seams to work with texturing.
            # Set up seams for texture grid. This is needed because the height data is denser
            # than the texture tile data. This is my way of keeping track of the two.
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="EDGE")
            PolySelect = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            PolySelect.faces.ensure_lookup_table()  # Blender is dumb and can't do this for the user. Pathetic.
            PolySelect.edges.ensure_lookup_table()
            EdgeSeamDimensions = int(
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[2]
                .default_value
            )
            Counter = 0
            for v in range(0, int(EdgeSeamDimensions / 4)):
                PolySelect.edges[int(Counter)].select = True
                Counter += (EdgeSeamDimensions * 4) - 4

            # Formula, EdgeSeam-1 * EdgeSeam-1 + edgeSeam
            Counter = (
                (EdgeSeamDimensions - 1) * (EdgeSeamDimensions - 1)
                + EdgeSeamDimensions
                - 1
            )
            for v in range(0, int(EdgeSeamDimensions / 4)):
                PolySelect.edges[int(Counter)].select = True
                Counter += (EdgeSeamDimensions * 4) - 4

            bpy.ops.mesh.loop_multi_select(ring=False)
            bpy.ops.mesh.mark_seam(clear=False)
            bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")

            # The user's map will have inverted normals as a result of this operation. Flip them back.
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.flip_normals()  # uv data transfer only works when normals are facing downward.
            bpy.ops.mesh.select_all(action="DESELECT")

            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.vertex_group_add()
            bpy.ops.mesh.uv_texture_add()

            self.report({"WARNING"}, "BZMapIO:  Map re-positioning applied.")

            # All of the shrinkwrap constraints on every object need to be re-assigned since the map was deleted/recreated.
            for ob in bpy.data.objects:
                try:
                    ob.constraints["Shrinkwrap"].target = bpy.data.objects[OldMapName]
                except KeyError:
                    pass

        # Make sure the user's new map is selected, and that
        # its origin is geometrically centered to allow symmetry to work.
        bpy.ops.object.select_all(action="DESELECT")
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                obj = ob
                bpy.data.objects[ob.name].select_set(True)
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

        return {"FINISHED"}


# SEL OBJECT SHRINKWRAP BUTTON


class bzshrinkwrap(bpy.types.Operator):
    bl_idname = "button.bzshrinkwrap"
    bl_label = "Sel Obj Shrinkwrap"
    bl_description = "Makes the terrain wrap around the selected object. Good for making artificial or stylized terrain."

    def execute(self, context):

        if bpy.context.active_object.mode == "OBJECT":

            # Get selected object.
            UserSelection = bpy.context.selected_objects

            # Get user's current working map using string match of "HG2"
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    UserMap = ob

            # Move selected objects to ReferenceVisuals; this is where handles, gizmos, and other non-scene elements go.
            bpy.ops.object.select_all(action="DESELECT")
            for x in range(0, len(UserSelection)):

                if (
                    ".hg2_" not in UserSelection[x].name.lower()
                ):  # make sure its not the user's map.
                    # NOTE: I use a random string for the object name because constraints (which inherit the name of the object)
                    # must be unique in order to stack reliably.
                    UserSelection[x].name = "ShrinkWrapObject_" + "".join(
                        random.choices(string.ascii_lowercase, k=5)
                    )
                    bpy.data.objects[UserSelection[x].name].select_set(True)
                    bpy.context.view_layer.objects.active = UserSelection[x]
                    bpy.context.object.display_type = "BOUNDS"
                    bpy.ops.object.move_to_collection(collection_index=1)

                    # I remove the shrinkwrap CONSTRAINT on the object if present, otherwise user will have problems
                    # moving the object if they pulled it from the library.
                    bpy.ops.constraint.delete(constraint="Shrinkwrap", owner="OBJECT")

            bpy.ops.object.select_all(action="DESELECT")
            bpy.data.objects[UserMap.name].select_set(True)
            bpy.context.view_layer.objects.active = UserMap

            # the modifier is applied and given the name of the object for easy reference.
            for x in range(0, len(UserSelection)):
                if (
                    ".hg2_" not in UserSelection[x].name.lower()
                ):  # make sure its not the user's map.
                    bpy.ops.object.modifier_add(type="SHRINKWRAP")
                    bpy.context.object.modifiers["Shrinkwrap"].name = UserSelection[
                        x
                    ].name
                    bpy.context.object.modifiers[UserSelection[x].name].wrap_method = (
                        "PROJECT"
                    )
                    bpy.context.object.modifiers[
                        UserSelection[x].name
                    ].use_project_z = True
                    bpy.context.object.modifiers[UserSelection[x].name].wrap_mode = (
                        "OUTSIDE_SURFACE"
                    )
                    bpy.context.object.modifiers[UserSelection[x].name].target = (
                        bpy.data.objects[UserSelection[x].name]
                    )

            # Re-select objects for the user.
            bpy.ops.object.select_all(action="DESELECT")
            for x in range(0, len(UserSelection)):
                if x == 0:
                    bpy.context.view_layer.objects.active = UserSelection[x]
                    bpy.data.objects[UserSelection[x].name].select_set(True)
                bpy.data.objects[UserSelection[x].name].select_set(True)

        else:
            self.report({"WARNING"}, "BZMapIO: This tool only operates in OBJECT mode.")

        return {"FINISHED"}


# Helper function to make inverting shrinkwrap object influence simpler.
class bzshrinkwrapinvert(bpy.types.Operator):
    bl_idname = "button.bzinvertshrinkwrap"
    bl_label = "Sel Invert Shrinkwrap"
    bl_description = "Inverts the influence of any selected shrinkwrap objects"

    def execute(self, context):

        if bpy.context.active_object.mode == "OBJECT":

            UserSelection = bpy.context.selected_objects
            bpy.ops.object.select_all(action="DESELECT")

            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    UserTerrain = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.data.objects[ob.name].hide_select = False

            # We essentially just invert the checkboxes for the user on the user terrain object.
            ErrorFlag = 0
            for x in range(0, len(UserSelection)):
                if "ShrinkWrapObject_" in UserSelection[x].name:
                    bpy.context.object.modifiers[
                        UserSelection[x].name
                    ].use_positive_direction = not bpy.context.object.modifiers[
                        UserSelection[x].name
                    ].use_positive_direction
                    bpy.context.object.modifiers[
                        UserSelection[x].name
                    ].use_negative_direction = not bpy.context.object.modifiers[
                        UserSelection[x].name
                    ].use_negative_direction
                    if (
                        bpy.context.object.modifiers[
                            UserSelection[x].name
                        ].use_positive_direction
                        == True
                    ):
                        bpy.context.object.modifiers[
                            UserSelection[x].name
                        ].cull_face = "FRONT"
                    else:
                        bpy.context.object.modifiers[
                            UserSelection[x].name
                        ].cull_face = "BACK"
                    ErrorFlag += 1

            if ErrorFlag == 0:
                self.report(
                    {"WARNING"}, "BZMapIO: No invertable shrinkwrap objects detected."
                )

            bpy.ops.object.select_all(action="DESELECT")

            # Re-select user selection
            for x in range(0, len(UserSelection)):
                if x == 0:
                    bpy.context.view_layer.objects.active = UserSelection[x]
                bpy.data.objects[UserSelection[x].name].select_set(True)
        else:
            self.report({"WARNING"}, "BZMapIO: This tool only operates in OBJECT mode.")

        return {"FINISHED"}


class bzshrinkwrapbake(bpy.types.Operator):
    bl_idname = "button.bzshrinkwrapbake"
    bl_label = "Sel Bake Shrinkwrap"
    bl_description = "Deletes the shrinkwrap object, leaving its changes in place."

    def execute(self, context):
        # Get selected object.
        UserSelection = bpy.context.selected_objects

        # Get user's current working map using string match of "HG2"
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                UserMap = ob
                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = UserMap
                bpy.data.objects[UserMap.name].select_set(True)

        ErrorFlag = 0
        for x in range(0, len(UserSelection)):
            if UserSelection[x].name[:16] == "ShrinkWrapObject":
                bpy.ops.object.modifier_apply(modifier=UserSelection[x].name)
                ErrorFlag += 1

        bpy.ops.object.select_all(action="DESELECT")
        # Delete all shrinkwrap objects user selected.
        for x in range(0, len(UserSelection)):
            if UserSelection[x].name[:16] == "ShrinkWrapObject":
                bpy.context.view_layer.objects.active = UserSelection[x]
                bpy.data.objects[UserSelection[x].name].select_set(True)
                bpy.ops.object.delete(use_global=False, confirm=False)

        if ErrorFlag == 0:
            self.report(
                {"WARNING"}, "BZMapIO: No bakeable shrinkwrap objects detected."
            )

        return {"FINISHED"}


class bzgosculpt(bpy.types.Operator):
    bl_idname = "button.bzgosculpt"
    bl_label = "Go Sculpt"
    bl_description = "When pressed, selects the scene terrain and brings the user to sculpting interface."

    def execute(self, context):

        if bpy.context.window.workspace.name == "Sculpting":
            bpy.context.window.workspace = bpy.data.workspaces["Layout"]

        else:
            bpy.context.window.workspace = bpy.data.workspaces["Layout"]
            if bpy.context.active_object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

            bpy.ops.object.select_all(action="DESELECT")
            # Get user's current working map using string match of "HG2"
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
            bpy.context.window.workspace = bpy.data.workspaces["Sculpting"]

        return {"FINISHED"}


class bzgopaint(bpy.types.Operator):
    bl_idname = "button.bzgopaint"
    bl_label = "Go Paint"
    bl_description = "When pressed, toggles between the layout and paint workspaces."

    def execute(self, context):

        bpy.context.window.workspace = bpy.data.workspaces["Layout"]

        if bpy.context.active_object.mode == "OBJECT":
            bpy.ops.object.select_all(action="DESELECT")
            # Get user's current working map using string match of "HG2"
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)

            bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
        elif bpy.context.active_object.mode == "WEIGHT_PAINT":
            bpy.ops.object.mode_set(mode="OBJECT")

        return {"FINISHED"}


# PAINT SHRINKWRAP BUTTON
class bzpaintshrinkwrap(bpy.types.Operator):
    bl_idname = "button.bzpaintshrinkwrap"
    bl_label = "Paint Shrinkwrap"
    bl_description = "Creates a shrinkwrap object out of user-painted terrain. Useful for duplicating or moving specific land details."

    def execute(self, context):

        # Basically, this button does the following:
        # 1) Switches the user to edit mode
        # 2) Selects the terrain based on weight map painted
        # 3) Copies and duplicates the selected polygons
        # 4) Separate the resulting geometry into a separate object (renaming it)
        # 5) Apply sel obj shrinkwrap to it.

        # Get user's current working map using string match of "HG2"
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                bpy.context.view_layer.objects.active = ob
                bpy.data.objects[ob.name].select_set(True)
                UserTerrain = ob
                bpy.data.objects[ob.name].hide_select = False

        bpy.context.window.workspace = bpy.data.workspaces["Layout"]
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.vertex_group_clean(limit=0.1)  # clean up 0 values if present.
        bpy.ops.object.vertex_group_select()
        bpy.ops.mesh.duplicate_move(MESH_OT_duplicate={"mode": 1})
        bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")
        UserTerrain.select_set(False)
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

        # Because the script is set to not apply shrinkwrap to the terrain,
        # we must rename the object.
        bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
        bpy.context.object.name = "PaintShrinkwrapOBJ"
        bpy.ops.button.bzshrinkwrap()

        return {"FINISHED"}


# The SIZE UP and SIZE DOWN buttons increase the map's size by increments of 1280
# Minimum size is 1280, maximum size is 5120.
class bzbutton_mapsizeup(bpy.types.Operator):
    bl_idname = "button.bzsizeup"
    bl_label = "↑ Map Size ↑"
    bl_description = "Scales the map up in 1280x1280 increments"

    def execute(self, context):

        # Get user's current working map using string match of "HG2"
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                bpy.context.view_layer.objects.active = ob
                bpy.data.objects[ob.name].select_set(True)
                bpy.data.objects[ob.name].hide_select = False

        # Make sure user is in layout workspace and in object mode.
        bpy.context.window.workspace = bpy.data.workspaces["Layout"]
        bpy.ops.object.mode_set(mode="OBJECT")

        # HEADER
        H1 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String"].string
        )  # structure version
        H2 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.001"].string
        )  # zone bits
        H3 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string
        )  # map width
        H4 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string
        )  # map depth
        H5 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.004"].string
        )  # map version

        # Adjust BZMapGenerator object to increment 1 size up.
        if H3 != "4" and H4 != "4":

            bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string = str(
                int(H3) + 1
            )  # map width header
            bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string = str(
                int(H4) + 1
            )  # map depth header

            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                0
            ].default_value = 1280 * (
                int(H3) + 1
            )  # Dimensions X
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                1
            ].default_value = 1280 * (
                int(H4) + 1
            )  # Dimensions Y

            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                2
            ].default_value = 256 * (
                int(H3) + 1
            )  # Vertices X
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                3
            ].default_value = 256 * (
                int(H4) + 1
            )  # Vertices Y

            # Get user's current working map using string match of "HG2"
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    UserMap = ob

            # Duplicate the now-updated BZMapGenerator object
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ob.name == "BZMapGenerator":
                    MapGenerator = ob
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    _hide_view_clear_compat()  # Duplicate operation will REPLACE the object if its hidden... not a clue why.

                    # To better line up with how objects are placed in Battlezone, we position the map so that the lower left
                    # corner overlaps with the world origin center of the scene (coordinates 0/0)
                    ob.location[0] = ((int(H3) + 1) * 1280) / 2
                    ob.location[1] = -((int(H4) + 1) * 1280) / 2

            bpy.ops.object.duplicate_move(
                OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"},
                TRANSFORM_OT_translate={
                    "value": (0, 0, 0),
                    "orient_type": "GLOBAL",
                    "orient_matrix": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                    "orient_matrix_type": "GLOBAL",
                    "constraint_axis": (False, False, False),
                    "mirror": False,
                    "use_proportional_edit": False,
                    "proportional_edit_falloff": "SMOOTH",
                    "proportional_size": 1,
                    "use_proportional_connected": False,
                    "use_proportional_projected": False,
                    "snap": False,
                    "snap_elements": {"VERTEX", "FACE", "FACE_NEAREST"},
                    "use_snap_project": True,
                    "snap_target": "CENTER",
                    "use_snap_self": True,
                    "use_snap_edit": True,
                    "use_snap_nonedit": True,
                    "use_snap_selectable": False,
                    "snap_point": (0, 0, 0),
                    "snap_align": False,
                    "snap_normal": (0, 0, 0),
                    "gpencil_strokes": False,
                    "cursor_transform": False,
                    "texture_space": False,
                    "remove_on_cancel": False,
                    "view2d_edge_pan": False,
                    "release_confirm": False,
                    "use_accurate": False,
                    "use_automerge_and_split": False,
                },
            )
            bpy.ops.object.modifier_apply(modifier="GeometryNodes")
            NewMap = bpy.context.view_layer.objects.active
            NewMap.name = UserMap.name.replace(
                str(1280 * int(H3)), str(1280 * (int(H3) + 1))
            )

            # Duplicated map must be moved to fit lower left corner as well.
            NewMap.location[0] = ((int(H3) + 1) * 1280) / 2
            NewMap.location[1] = -((int(H4) + 1) * 1280) / 2

            # Shrinkwrap newly generated duplicate on old map

            # Apply shrinkwrap
            bpy.ops.object.modifier_add(type="SHRINKWRAP")
            bpy.context.object.modifiers["Shrinkwrap"].target = bpy.data.objects[
                UserMap.name
            ]
            bpy.context.object.modifiers["Shrinkwrap"].wrap_method = "PROJECT"
            bpy.context.object.modifiers["Shrinkwrap"].use_project_z = True
            bpy.context.object.modifiers["Shrinkwrap"].use_negative_direction = True
            bpy.ops.object.modifier_apply(modifier="Shrinkwrap")

            # Remove previous user map
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = UserMap
            bpy.data.objects[UserMap.name].select_set(True)
            bpy.ops.object.delete(use_global=False, confirm=False)

            # The scale of the map is directly displayed to the user in the viewport.
            # Change it to represent current map size.

            # Find Text object and change it.
            for ob in bpy.data.objects:
                if ob.name == "Scale_Display":
                    ob.data.body = (
                        str(1280 * (int(H3) + 1)) + "x" + str(1280 * (int(H4) + 1))
                    )
                    # Re-position the text appropriately.
                    bpy.data.objects[ob.name].location.x = (
                        (1280 * (int(H3) + 1))
                    ) + 100
                    bpy.data.objects[ob.name].location.y = 0
                    bpy.data.objects[ob.name].location.z = bpy.data.objects[
                        NewMap.name
                    ].dimensions.z  # this is the bounding box top of the map mesh.

            MapGenerator.hide_set(True)

            # I honestly don't know why this is needed, the bztransform function selects the user's map
            # at the very start but for whatever reason when it is called externally it does not function
            # unless the selection is made beforehand.
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)

            # I perform a transform so that the map uniformly scales to the next size up.
            bpy.ops.button.bztransform()
            bpy.context.object.location[0] = 1280 * (int(H3) + 1)
            bpy.context.object.location[1] = -(1280 * (int(H4) + 1))
            NewSize = 1280 * (float(H3) + 1)
            OldSize = 1280 * float(H3)
            bpy.context.object.scale[0] = NewSize / OldSize
            bpy.context.object.scale[1] = NewSize / OldSize
            bpy.context.object.scale[2] = NewSize / OldSize

            # Move map so its corner is at world origin.
            bpy.ops.object.align(
                align_mode="OPT_1", relative_to="OPT_1", align_axis={"X"}
            )
            bpy.ops.object.align(
                align_mode="OPT_3", relative_to="OPT_1", align_axis={"Y"}
            )
            bpy.ops.button.bztransform()

            # If any objects are present in the map, scale their positions proportionately to the size increase.

            # Before moving anything, check for objects the user drag-and-dropped from the
            # object library. The objects are placed in BZ_Unit_Models collection.
            ItemstoMove = []
            for collection in bpy.data.collections:
                if collection.name == "BZ_Unit_Models":
                    for obj in collection.all_objects:
                        if obj.name.find(".") != -1:
                            ItemstoMove.append(obj)

            # User-placed items need to be moved over to GAMEOBJECTS so they can be processed.
            for x in range(0, len(ItemstoMove)):
                bpy.ops.object.select_all(action="DESELECT")
                bpy.data.objects[ItemstoMove[x].name].select_set(True)
                bpy.context.view_layer.objects.active = ItemstoMove[x]
                bpy.ops.object.move_to_collection(collection_index=2)

            # Perform scaling operation on GAMEOBJECTS
            BZ_USER_ITEMS = []
            for collection in bpy.data.collections:
                if collection.name == "GAMEOBJECTS":
                    for obj in collection.all_objects:
                        obj.matrix_world.translation[0] = obj.matrix_world.translation[
                            0
                        ] * (NewSize / OldSize)
                        obj.matrix_world.translation[1] = obj.matrix_world.translation[
                            1
                        ] * (NewSize / OldSize)
                        obj.matrix_world.translation[2] = obj.matrix_world.translation[
                            2
                        ] * (NewSize / OldSize)

            # Perform scaling operation on PATHS
            BZ_USER_ITEMS = []
            for collection in bpy.data.collections:
                if collection.name == "PATHS":
                    for obj in collection.all_objects:
                        obj.matrix_world.translation[0] = obj.matrix_world.translation[
                            0
                        ] * (NewSize / OldSize)
                        obj.matrix_world.translation[1] = obj.matrix_world.translation[
                            1
                        ] * (NewSize / OldSize)
                        obj.matrix_world.translation[2] = obj.matrix_world.translation[
                            2
                        ] * (NewSize / OldSize)

            # It is possible the user's map isn't selected at this point, re-select it.
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)

            # Map needs a UVMap, vertex group, and seams to work with texturing.
            # Set up seams for texture grid. This is needed because the height data is denser
            # than the texture tile data. This is my way of keeping track of the two.
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="EDGE")
            PolySelect = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            PolySelect.faces.ensure_lookup_table()  # Blender is dumb and can't do this for the user. Pathetic.
            PolySelect.edges.ensure_lookup_table()
            EdgeSeamDimensions = int(
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[2]
                .default_value
            )
            Counter = 0
            for v in range(0, int(EdgeSeamDimensions / 4)):
                PolySelect.edges[int(Counter)].select = True
                Counter += (EdgeSeamDimensions * 4) - 4

            # Formula, EdgeSeam-1 * EdgeSeam-1 + edgeSeam
            Counter = (
                (EdgeSeamDimensions - 1) * (EdgeSeamDimensions - 1)
                + EdgeSeamDimensions
                - 1
            )
            for v in range(0, int(EdgeSeamDimensions / 4)):
                PolySelect.edges[int(Counter)].select = True
                Counter += (EdgeSeamDimensions * 4) - 4

            bpy.ops.mesh.loop_multi_select(ring=False)
            bpy.ops.mesh.mark_seam(clear=False)
            bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")
            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.vertex_group_add()
            bpy.ops.mesh.uv_texture_add()

            # Textures can't be preserved with map scaling. Remove the tile selector interface.
            for ob in bpy.data.objects:
                if "tileselector" in ob.name.lower():
                    bpy.data.objects[ob.name].hide_select = False
                    bpy.ops.object.select_all(action="DESELECT")
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.ops.object.delete(use_global=False, confirm=False)

            # Make sure the user's new map is selected, and that
            # its origin is geometrically centered to allow symmetry to work.
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    obj = ob
                    bpy.data.objects[ob.name].select_set(True)
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

            self.report(
                {"INFO"},
                "BZMapIO:  Map canvas scaled UP to "
                + str(1280 * (int(H3) + 1))
                + "x"
                + str(1280 * (int(H4) + 1)),
            )

        else:
            self.report(
                {"ERROR"},
                "BZMapIO:  Map is currently at the highest possible scale (5120x5120).",
            )

        return {"FINISHED"}


class bzbutton_mapsizedn(bpy.types.Operator):
    bl_idname = "button.bzsizedn"
    bl_label = "↓ Map Size ↓"
    bl_description = "Scales the map down in 1280x1280 increments"

    def execute(self, context):

        # Make sure user is in layout workspace and in object mode.
        bpy.context.window.workspace = bpy.data.workspaces["Layout"]

        # Make sure the user's map is selected
        bpy.ops.object.select_all(action="DESELECT")
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                bpy.data.objects[ob.name].select_set(True)
                bpy.data.objects[ob.name].hide_select = (
                    False  # The map must be selectable because this script depends on selections to function.
                )
                bpy.context.view_layer.objects.active = ob

        bpy.ops.object.mode_set(mode="OBJECT")

        # HEADER
        H1 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String"].string
        )  # structure version
        H2 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.001"].string
        )  # zone bits
        H3 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string
        )  # map width
        H4 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string
        )  # map depth
        H5 = (
            bpy.data.node_groups["Geometry Nodes"].nodes["String.004"].string
        )  # map version

        # Adjust BZMapGenerator object to increment 1 size down.
        if H3 != "1" and H4 != "1":
            # Align BZMapGenerator so its lower left corner rests on the world origin.
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ob.name == "BZMapGenerator":
                    MapGenerator = ob
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    _hide_view_clear_compat()  # Duplicate operation will REPLACE the object if its hidden... not a clue why.
                    bpy.ops.object.align(
                        align_mode="OPT_1", relative_to="OPT_1", align_axis={"X"}
                    )
                    bpy.ops.object.align(
                        align_mode="OPT_3", relative_to="OPT_1", align_axis={"Y"}
                    )

            # An extra step is needed to size down a map because otherwise the user will lose a large portion of their terrain.
            # I use the bztransform function from this script to pull it off.

            bpy.ops.button.bztransform()
            bpy.context.object.location[0] = 1280 * (int(H3) - 1)
            bpy.context.object.location[1] = -(1280 * (int(H4) - 1))
            NewSize = 1280 * (float(H3) - 1)
            OldSize = 1280 * float(H3)
            bpy.context.object.scale[0] = NewSize / OldSize
            bpy.context.object.scale[1] = NewSize / OldSize
            bpy.context.object.scale[2] = NewSize / OldSize

            # Move map so its corner is at world origin.
            bpy.ops.object.align(
                align_mode="OPT_1", relative_to="OPT_1", align_axis={"X"}
            )
            bpy.ops.object.align(
                align_mode="OPT_3", relative_to="OPT_1", align_axis={"Y"}
            )
            bpy.ops.button.bztransform()

            bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string = str(
                int(H3) - 1
            )  # map width header
            bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string = str(
                int(H4) - 1
            )  # map depth header

            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                0
            ].default_value = 1280 * (
                int(H3) - 1
            )  # Dimensions X
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                1
            ].default_value = 1280 * (
                int(H4) - 1
            )  # Dimensions Y

            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                2
            ].default_value = 256 * (
                int(H3) - 1
            )  # Vertices X
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                3
            ].default_value = 256 * (
                int(H4) - 1
            )  # Vertices Y

            # Get user's current working map using string match of "HG2"
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    UserMap = ob

            # Duplicate the now-updated BZMapGenerator object
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ob.name == "BZMapGenerator":
                    MapGenerator = ob
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    _hide_view_clear_compat()  # Duplicate operation will REPLACE the object if its hidden... not a clue why.

            bpy.ops.object.duplicate_move(
                OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"},
                TRANSFORM_OT_translate={
                    "value": (0, 0, 0),
                    "orient_type": "GLOBAL",
                    "orient_matrix": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                    "orient_matrix_type": "GLOBAL",
                    "constraint_axis": (False, False, False),
                    "mirror": False,
                    "use_proportional_edit": False,
                    "proportional_edit_falloff": "SMOOTH",
                    "proportional_size": 1,
                    "use_proportional_connected": False,
                    "use_proportional_projected": False,
                    "snap": False,
                    "snap_elements": {"VERTEX", "FACE", "FACE_NEAREST"},
                    "use_snap_project": True,
                    "snap_target": "CENTER",
                    "use_snap_self": True,
                    "use_snap_edit": True,
                    "use_snap_nonedit": True,
                    "use_snap_selectable": False,
                    "snap_point": (0, 0, 0),
                    "snap_align": False,
                    "snap_normal": (0, 0, 0),
                    "gpencil_strokes": False,
                    "cursor_transform": False,
                    "texture_space": False,
                    "remove_on_cancel": False,
                    "view2d_edge_pan": False,
                    "release_confirm": False,
                    "use_accurate": False,
                    "use_automerge_and_split": False,
                },
            )
            bpy.ops.object.modifier_apply(modifier="GeometryNodes")
            NewMap = bpy.context.view_layer.objects.active
            NewMap.name = UserMap.name.replace(
                str(1280 * int(H3)), str(1280 * (int(H3) - 1))
            )

            # Move map so its corner is at world origin.
            bpy.ops.object.align(
                align_mode="OPT_1", relative_to="OPT_1", align_axis={"X"}
            )
            bpy.ops.object.align(
                align_mode="OPT_3", relative_to="OPT_1", align_axis={"Y"}
            )

            # Shrinkwrap newly generated duplicate on old map
            bpy.ops.object.modifier_add(type="SHRINKWRAP")
            bpy.context.object.modifiers["Shrinkwrap"].target = bpy.data.objects[
                UserMap.name
            ]
            bpy.context.object.modifiers["Shrinkwrap"].wrap_method = "PROJECT"
            bpy.context.object.modifiers["Shrinkwrap"].use_project_z = True
            bpy.context.object.modifiers["Shrinkwrap"].use_negative_direction = True
            bpy.ops.object.modifier_apply(modifier="Shrinkwrap")

            # Remove previous user map
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = UserMap
            bpy.data.objects[UserMap.name].select_set(True)
            bpy.ops.object.delete(use_global=False, confirm=False)

            # The scale of the map (in geometry text) is directly displayed to the user in the viewport.
            # Change it to represent current map size.

            # Find Text object and change it.
            for ob in bpy.data.objects:
                if ob.name == "Scale_Display":
                    ob.data.body = (
                        str(1280 * (int(H3) - 1)) + "x" + str(1280 * (int(H4) - 1))
                    )
                    # Re-position the text appropriately.
                    bpy.data.objects[ob.name].location.x = (
                        (1280 * (int(H3) - 1))
                    ) + 100
                    bpy.data.objects[ob.name].location.y = 0
                    bpy.data.objects[ob.name].location.z = bpy.data.objects[
                        NewMap.name
                    ].dimensions.z  # this is the bounding box top of the map mesh.

            MapGenerator.hide_set(True)

            # Select new user map for user.
            bpy.context.view_layer.objects.active = NewMap
            bpy.data.objects[NewMap.name].select_set(True)
            bpy.ops.object.shade_smooth(
                use_auto_smooth=True, auto_smooth_angle=0.872665
            )

            # If any objects are present in the map, scale their positions proportionately to the size increase.

            # Before moving anything, check for objects the user drag-and-dropped from the
            # object library. The objects are placed in BZ_Unit_Models collection.
            ItemstoMove = []
            for collection in bpy.data.collections:
                if collection.name == "BZ_Unit_Models":
                    for obj in collection.all_objects:
                        if obj.name.find(".") != -1:
                            ItemstoMove.append(obj)

            # User-placed items need to be moved over to GAMEOBJECTS so they can be processed.
            for x in range(0, len(ItemstoMove)):
                bpy.ops.object.select_all(action="DESELECT")
                bpy.data.objects[ItemstoMove[x].name].select_set(True)
                bpy.context.view_layer.objects.active = ItemstoMove[x]
                bpy.ops.object.move_to_collection(collection_index=2)

            # Perform scaling operation on GAMEOBJECTS
            BZ_USER_ITEMS = []
            for collection in bpy.data.collections:
                if collection.name == "GAMEOBJECTS":
                    for obj in collection.all_objects:
                        obj.matrix_world.translation[0] = obj.matrix_world.translation[
                            0
                        ] / (OldSize / NewSize)
                        obj.matrix_world.translation[1] = obj.matrix_world.translation[
                            1
                        ] / (OldSize / NewSize)
                        obj.matrix_world.translation[2] = obj.matrix_world.translation[
                            2
                        ] / (OldSize / NewSize)

            # Perform scaling operation on PATHS
            BZ_USER_ITEMS = []
            for collection in bpy.data.collections:
                if collection.name == "PATHS":
                    for obj in collection.all_objects:
                        obj.matrix_world.translation[0] = obj.matrix_world.translation[
                            0
                        ] / (OldSize / NewSize)
                        obj.matrix_world.translation[1] = obj.matrix_world.translation[
                            1
                        ] / (OldSize / NewSize)
                        obj.matrix_world.translation[2] = obj.matrix_world.translation[
                            2
                        ] / (OldSize / NewSize)

            # All of the shrinkwrap constraints on every object need to be re-assigned since the map was deleted/recreated.
            # NOTE: Size up doesn't need this because it uses BZTRANSFORM which includes re-assignment of this constraint.
            for ob in bpy.data.objects:
                try:
                    ob.constraints["Shrinkwrap"].target = bpy.data.objects[NewMap.name]
                except KeyError:
                    pass

            # Map needs a UVMap, vertex group, and seams to work with texturing.
            # Set up seams for texture grid. This is needed because the height data is denser
            # than the texture tile data. This is my way of keeping track of the two.
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="EDGE")
            PolySelect = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            PolySelect.faces.ensure_lookup_table()  # Blender is dumb and can't do this for the user. Pathetic.
            PolySelect.edges.ensure_lookup_table()
            EdgeSeamDimensions = int(
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[2]
                .default_value
            )
            Counter = 0
            for v in range(0, int(EdgeSeamDimensions / 4)):
                PolySelect.edges[int(Counter)].select = True
                Counter += (EdgeSeamDimensions * 4) - 4

            # Formula, EdgeSeam-1 * EdgeSeam-1 + edgeSeam
            Counter = (
                (EdgeSeamDimensions - 1) * (EdgeSeamDimensions - 1)
                + EdgeSeamDimensions
                - 1
            )
            for v in range(0, int(EdgeSeamDimensions / 4)):
                PolySelect.edges[int(Counter)].select = True
                Counter += (EdgeSeamDimensions * 4) - 4

            bpy.ops.mesh.loop_multi_select(ring=False)
            bpy.ops.mesh.mark_seam(clear=False)
            bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")
            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.vertex_group_add()
            bpy.ops.mesh.uv_texture_add()

            # Textures can't be preserved with map scaling. Remove the tile selector interface.
            for ob in bpy.data.objects:
                if "tileselector" in ob.name.lower():
                    bpy.data.objects[ob.name].hide_select = False
                    bpy.ops.object.select_all(action="DESELECT")
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.ops.object.delete(use_global=False, confirm=False)

            self.report(
                {"INFO"},
                "BZMapIO:  Map canvas scaled DOWN to "
                + str(1280 * (int(H3) - 1))
                + "x"
                + str(1280 * (int(H4) - 1)),
            )

            # Make sure the user's new map is selected, and that
            # its origin is geometrically centered to allow symmetry to work.
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    obj = ob
                    bpy.data.objects[ob.name].select_set(True)
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

        else:
            self.report(
                {"ERROR"},
                "BZMapIO:  Map is currently at the lowest possible scale (1280x1280).",
            )

        return {"FINISHED"}


class bzbutton_loadtextures(bpy.types.Operator):
    bl_idname = "button.bzloadtextures"
    bl_label = "Import Map Textures (.mat)"
    bl_description = "Loads textures using the tileset associated with the map."

    def execute(self, context):

        # BEFORE DOING ANYTHING... compare the current size of
        # the map with the size of the .MAT file. If they do not match,
        # halt operation and prompt user.

        MatData = []
        with open(
            bpy.context.scene.BZMapFile.lower().replace(".hg2", ".mat"), "rb"
        ) as f:
            while byte := f.read(2):
                matfiledata = hex(int.from_bytes(byte, "big"))
                matfiledata = matfiledata[2:].zfill(4)
                MatData.append(matfiledata[0])

        MapDepth = int(
            bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string
        )

        # Determine whether or not the .MAT file size matches the user map size.
        if (MapDepth * MapDepth) * 4096 == len(MatData):
            SizeIsEqual = True
        else:
            SizeIsEqual = False

        # If size matches, operate as normal.
        if SizeIsEqual == True:

            # Get rid of any MatGeneratorPrompt if present.
            MatGeneratorPrompt = None
            for ob in bpy.data.objects:
                if "matgeneratorprompt" in ob.name.lower():
                    MatGeneratorPrompt = ob
                    break
            # Delete MatGeneratorPrompt
            if MatGeneratorPrompt != None:
                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = MatGeneratorPrompt
                bpy.data.objects[MatGeneratorPrompt.name].select_set(True)
                bpy.ops.object.delete(use_global=False, confirm=False)

            #############################################################################
            # Texturing requires a workaround process of projecting UV coordinates from
            # a low poly mesh (using data transfer modifier) to the user's terrain due
            # to python processing constraints.
            #############################################################################

            # STEP 1) Generate a low poly version of the user's terrain (TextureTiles), matching 4x4 tile
            # grid of battlezone redux terrain, this process is repeated depending on the # of "zones"
            # the map has. 2560x2560 has 4 zones, 3840x3840 has 9, 5120x5120 has 16

            # If nothing is selected, for some stupid reason blender assumes
            # you want to delete hidden objects, so I reveal them temporarily.

            _hide_view_clear_compat()

            # Select the grid template...
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if ob.name == "BZMapGenerator":
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    BZMapGenerator = ob

            # Get map dimensions (Square maps only, so we only care about X).
            UserMapSize = (
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[0]
                .default_value
            )

            # Get user map dimensions so it can be set back when script finishes.
            UserMapDimensions = [
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[0]
                .default_value,
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[1]
                .default_value,
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[2]
                .default_value,
                bpy.data.node_groups["Geometry Nodes"]
                .nodes["Grid"]
                .inputs[3]
                .default_value,
            ]

            # Set tilesize to match the size of a "zone", a 64x64 grid (Blender counts from 1 not 0 so its 65).
            # Maps larger than 1280x1280 are textured using multiple zones.

            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                0
            ].default_value = 1280
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                1
            ].default_value = 1280
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                2
            ].default_value = 65  # 65 because Blender's grid counts from 1 not 0
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                3
            ].default_value = 65

            XYCount = 0
            XMultiplier = 0
            YMultiplier = 1
            for q in range(0, int((UserMapSize / 1280) * (UserMapSize / 1280))):

                bpy.ops.object.select_all(action="DESELECT")
                bpy.context.view_layer.objects.active = BZMapGenerator
                bpy.data.objects[BZMapGenerator.name].select_set(True)

                # Duplicate grid template, this serves as our texture tileset.
                bpy.ops.object.duplicate_move(
                    OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"}
                )
                bpy.context.object.name = os.path.basename("TextureTiles")
                bpy.ops.object.modifier_apply(modifier="GeometryNodes")

                # We need to move the 64x64 tile grid based on the q loop. A 2560x2560 map for example is structured in 4 zones like so...
                # 2 > 3
                # 0 > 1
                # So, this whole thing is computed 4 times in total.

                # We need to iterate every "row". a 2x2 grid

                # Iterate Row
                XYCount += 1
                if 1280 * XYCount > UserMapSize:
                    XYCount = 1
                    YMultiplier += 1
                    XMultiplier = 0

                XMultiplier += 1

                # Duplicated map must be moved to fit lower left corner as well.
                bpy.context.object.location[0] = -640 + (YMultiplier * 1280)
                bpy.context.object.location[1] = 640 - (XMultiplier * 1280)

                # The BZMapGenerator object's vertex index on the X axis is inverse of Battlezone terrain.
                # Flip it so that no conversion work is needed.
                bpy.context.object.rotation_euler[0] = 3.14159
                bpy.ops.object.transform_apply(
                    location=False, rotation=True, scale=False
                )
                TextureTiles = bpy.context.selected_objects[0]

                # This thing needs a UV map too.
                bpy.ops.mesh.uv_texture_add()

                # STEP 2) Gather data. We need to know:
                # - Which tileset to use
                # - Tile Match using CSV file
                # - Data from .MAT file.

                # Determine whether its a stock map.
                CSVFile = (
                    bpy.data.node_groups["Geometry Nodes"].nodes["String.005"].string
                )

                StockTiles = [
                    ["ac_detail_atlas", "ACHILLES"],
                    ["el_detail_atlas", "ELYSIUM"],
                    ["eu_detail_atlas", "EUROPA"],
                    ["ga_detail_atlas", "GANYMEDE"],
                    ["io_detail_atlas", "IO"],
                    ["ma_detail_atlas", "MARS"],
                    ["mn_detail_atlas", "MOON"],
                    ["ti_2_detail_atlas", "TITAN"],
                    ["ti_3_detail_atlas", "TITAN"],
                    ["ti_detail_atlas", "TITAN"],
                    ["ve_detail_atlas", "VENUS"],
                ]

                # Stock Map Data Sets. I figured it'd just be easier to not reference external CSV files and embed them here.
                CSVData = None
                CSVMaterial = None

                # ACHILLES
                if StockTiles[0][0] in CSVFile:
                    CSVMaterial = StockTiles[0][1]
                    CSVData = [
                        ["AC00SA0.MAP", "0.000", "0.000", "0.125", "0.125"],
                        ["AC00SB0.MAP", "0.000", "0.000", "0.125", "0.125"],
                        ["AC00SC0.MAP", "0.000", "0.000", "0.125", "0.125"],
                        ["AC00SD0.MAP", "0.500", "0.000", "0.125", "0.125"],
                        [
                            "AC00SE0.MAP",
                            "0.500",
                            "0.000",
                            "0.125",
                            "0.125",
                        ],  # Don't ask me why Achilles has a ton of duplicate tiles. Meh.
                        ["AC01CA0.MAP", "0.625", "0.000", "0.125", "0.125"],
                        ["AC05CA0.MAP", "0.750", "0.000", "0.125", "0.125"],
                        ["AC05CB0.MAP", "0.875", "0.000", "0.125", "0.125"],
                        ["AC05CC0.MAP", "0.000", "0.125", "0.125", "0.125"],
                        ["AC01DA0.MAP", "0.125", "0.125", "0.125", "0.125"],
                        ["AC05DA0.MAP", "0.250", "0.125", "0.125", "0.125"],
                        ["AC11SA0.MAP", "0.375", "0.125", "0.125", "0.125"],
                        ["AC12CA0.MAP", "0.500", "0.125", "0.125", "0.125"],
                        ["AC12DA0.MAP", "0.625", "0.125", "0.125", "0.125"],
                        ["AC22SA0.MAP", "0.750", "0.125", "0.125", "0.125"],
                        [
                            "AC33SA0.MAP",
                            "0.875",
                            "0.125",
                            "0.125",
                            "0.125",
                        ],  # River tiles are so jank.
                        ["AC33SB0.MAP", "0.000", "0.250", "0.125", "0.125"],
                        ["AC33SC0.MAP", "0.125", "0.250", "0.125", "0.125"],
                        ["AC33SD0.MAP", "0.250", "0.250", "0.125", "0.125"],
                        ["AC44SA0.MAP", "0.375", "0.250", "0.125", "0.125"],
                        ["AC55SA0.MAP", "0.500", "0.250", "0.125", "0.125"],
                        ["AC55SB0.MAP", "0.625", "0.250", "0.125", "0.125"],
                    ]

                # ELYSIUM
                if StockTiles[1][0] in CSVFile:
                    CSVMaterial = StockTiles[1][1]
                    CSVData = [
                        ["EL00SA0.MAP", "0.125", "0.000", "0.125", "0.125"],
                        ["EL00SB0.MAP", "0.250", "0.000", "0.125", "0.125"],
                        ["EL00SC0.MAP", "0.375", "0.000", "0.125", "0.125"],
                        ["EL01CA0.MAP", "0.500", "0.000", "0.125", "0.125"],
                        ["EL02CA0.MAP", "0.625", "0.000", "0.125", "0.125"],
                        ["EL04CA0.MAP", "0.750", "0.000", "0.125", "0.125"],
                        ["EL01DA0.MAP", "0.825", "0.000", "0.125", "0.125"],
                        ["EL02DA0.MAP", "0.000", "0.125", "0.125", "0.125"],
                        ["EL04DA0.MAP", "0.125", "0.125", "0.125", "0.125"],
                        ["EL11SA0.MAP", "0.250", "0.125", "0.125", "0.125"],
                        ["EL11SB0.MAP", "0.375", "0.125", "0.125", "0.125"],
                        ["EL11SC0.MAP", "0.500", "0.125", "0.125", "0.125"],
                        ["EL12CA0.MAP", "0.625", "0.125", "0.125", "0.125"],
                        ["EL12DA0.MAP", "0.750", "0.125", "0.125", "0.125"],
                        ["EL22SA0.MAP", "0.825", "0.125", "0.125", "0.125"],
                        ["EL22SB0.MAP", "0.000", "0.250", "0.125", "0.125"],
                        ["EL22SC0.MAP", "0.125", "0.250", "0.125", "0.125"],
                        ["EL33SA0.MAP", "0.250", "0.250", "0.125", "0.125"],
                        ["EL04SA0.MAP", "0.375", "0.250", "0.125", "0.125"],
                        ["EL04SB0.MAP", "0.500", "0.250", "0.125", "0.125"],
                    ]

                # EUROPA
                if StockTiles[2][0] in CSVFile:
                    CSVMaterial = StockTiles[2][1]
                    CSVData = [
                        ["EU00SA0.MAP", "0.00", "0.00", "0.25", "0.25"],
                        ["EU00SB0.MAP", "0.25", "0.00", "0.25", "0.25"],
                        ["EU01CA0.MAP", "0.50", "0.00", "0.25", "0.25"],
                        ["EU04CA0.MAP", "0.75", "0.00", "0.25", "0.25"],
                        ["EU01DA0.MAP", "0.00", "0.25", "0.25", "0.25"],
                        ["EU04DA0.MAP", "0.25", "0.25", "0.25", "0.25"],
                        ["EU11SA0.MAP", "0.50", "0.25", "0.25", "0.25"],
                        ["EU12CA0.MAP", "0.75", "0.25", "0.25", "0.25"],
                        ["EU12DA0.MAP", "0.00", "0.50", "0.25", "0.25"],
                        ["EU22SA0.MAP", "0.25", "0.50", "0.25", "0.25"],
                        ["EU23CA0.MAP", "0.50", "0.50", "0.25", "0.25"],
                        ["EU23DA0.MAP", "0.75", "0.50", "0.25", "0.25"],
                        ["EU33SA0.MAP", "0.00", "0.75", "0.25", "0.25"],
                        ["EU44SA0.MAP", "0.25", "0.75", "0.25", "0.25"],
                        ["EU44SB0.MAP", "0.50", "0.75", "0.25", "0.25"],
                        ["EU44SC0.MAP", "0.75", "0.75", "0.25", "0.25"],
                    ]

                # GANYMEDE
                if StockTiles[3][0] in CSVFile:
                    CSVMaterial = StockTiles[3][1]
                    CSVData = [
                        ["GA00SA0.MAP", "0.125", "0.000", "0.125", "0.125"],
                        ["GA00SB0.MAP", "0.250", "0.000", "0.125", "0.125"],
                        ["GA00SC0.MAP", "0.375", "0.000", "0.125", "0.125"],
                        ["GA01CA0.MAP", "0.500", "0.000", "0.125", "0.125"],
                        ["GA04CA0.MAP", "0.625", "0.000", "0.125", "0.125"],
                        ["GA01DA0.MAP", "0.750", "0.000", "0.125", "0.125"],
                        ["GA04DA0.MAP", "0.825", "0.000", "0.125", "0.125"],
                        ["GA11SA0.MAP", "0.000", "0.125", "0.125", "0.125"],
                        ["GA11SB0.MAP", "0.125", "0.125", "0.125", "0.125"],
                        ["GA12CA0.MAP", "0.250", "0.125", "0.125", "0.125"],
                        ["GA14CA0.MAP", "0.375", "0.125", "0.125", "0.125"],
                        ["GA12DA0.MAP", "0.500", "0.125", "0.125", "0.125"],
                        ["GA14DA0.MAP", "0.625", "0.125", "0.125", "0.125"],
                        ["GA22SA0.MAP", "0.750", "0.125", "0.125", "0.125"],
                        ["GA22SB0.MAP", "0.825", "0.125", "0.125", "0.125"],
                        ["GA22SC0.MAP", "0.000", "0.250", "0.125", "0.125"],
                        ["GA24CA0.MAP", "0.125", "0.250", "0.125", "0.125"],
                        ["GA24DA0.MAP", "0.250", "0.250", "0.125", "0.125"],
                        ["GA33SA0.MAP", "0.375", "0.250", "0.125", "0.125"],
                        ["GA33SB0.MAP", "0.500", "0.250", "0.125", "0.125"],
                        ["GA44SA0.MAP", "0.625", "0.250", "0.125", "0.125"],
                        ["GA44SB0.MAP", "0.750", "0.250", "0.125", "0.125"],
                    ]

                # IO
                if StockTiles[4][0] in CSVFile:
                    CSVMaterial = StockTiles[4][1]
                    CSVData = [
                        ["IO00SA0.MAP", "0.125", "0.000", "0.125", "0.125"],
                        ["IO00SA0.MAP", "0.250", "0.000", "0.125", "0.125"],
                        ["IO00SB0.MAP", "0.375", "0.000", "0.125", "0.125"],
                        ["IO00SC0.MAP", "0.500", "0.000", "0.125", "0.125"],
                        ["IO01CA0.MAP", "0.625", "0.000", "0.125", "0.125"],
                        ["IO03CA0.MAP", "0.750", "0.000", "0.125", "0.125"],
                        ["IO01DA0.MAP", "0.875", "0.000", "0.125", "0.125"],
                        ["IO03DA0.MAP", "0.000", "0.125", "0.125", "0.125"],
                        ["IO11SA0.MAP", "0.125", "0.125", "0.125", "0.125"],
                        ["IO33SA0.MAP", "0.250", "0.125", "0.125", "0.125"],
                        ["IO33SB0.MAP", "0.375", "0.125", "0.125", "0.125"],
                        ["IO33SA0.MAP", "0.500", "0.125", "0.125", "0.125"],
                        ["IO33SA0.MAP", "0.625", "0.125", "0.125", "0.125"],
                        ["IO34CA0.MAP", "0.750", "0.125", "0.125", "0.125"],
                        ["IO34DA0.MAP", "0.875", "0.125", "0.125", "0.125"],
                        ["IO44SA0.MAP", "0.000", "0.250", "0.125", "0.125"],
                        ["IO45CA0.MAP", "0.125", "0.250", "0.125", "0.125"],
                        ["IO45CB0.MAP", "0.250", "0.250", "0.125", "0.125"],
                        ["IO45DA0.MAP", "0.375", "0.250", "0.125", "0.125"],
                        ["IO55SA0.MAP", "0.500", "0.250", "0.125", "0.125"],
                        ["IO55SB0.MAP", "0.625", "0.250", "0.125", "0.125"],
                        ["IO55SC0.MAP", "0.750", "0.250", "0.125", "0.125"],
                    ]

                # MARS
                if StockTiles[5][0] in CSVFile:
                    CSVMaterial = StockTiles[5][1]
                    CSVData = [
                        ["MA00SA0.MAP", "0.125", "0.000", "0.125", "0.125"],
                        ["MA00SB0.MAP", "0.250", "0.000", "0.125", "0.125"],
                        ["MA00SC0.MAP", "0.375", "0.000", "0.125", "0.125"],
                        ["MA01CA0.MAP", "0.500", "0.000", "0.125", "0.125"],
                        ["MA04CA0.MAP", "0.625", "0.000", "0.125", "0.125"],
                        ["MA04CB0.MAP", "0.750", "0.000", "0.125", "0.125"],
                        ["MA01DA0.MAP", "0.875", "0.000", "0.125", "0.125"],
                        ["MA04DA0.MAP", "0.000", "0.125", "0.125", "0.125"],
                        ["MA11SA0.MAP", "0.125", "0.125", "0.125", "0.125"],
                        ["MA11SA0.MAP", "0.250", "0.125", "0.125", "0.125"],
                        ["MA11SB0.MAP", "0.375", "0.125", "0.125", "0.125"],
                        ["MA11SC0.MAP", "0.500", "0.125", "0.125", "0.125"],
                        ["MA13CA0.MAP", "0.625", "0.125", "0.125", "0.125"],
                        ["MA13DA0.MAP", "0.750", "0.125", "0.125", "0.125"],
                        ["MA22SA0.MAP", "0.875", "0.125", "0.125", "0.125"],
                        ["MA33SA0.MAP", "0.000", "0.250", "0.125", "0.125"],
                        ["MA44SA0.MAP", "0.125", "0.250", "0.125", "0.125"],
                        ["MA44SB0.MAP", "0.250", "0.250", "0.125", "0.125"],
                        ["MA44SC0.MAP", "0.375", "0.250", "0.125", "0.125"],
                    ]

                # MOON
                if StockTiles[6][0] in CSVFile:
                    CSVMaterial = StockTiles[6][1]
                    CSVData = [
                        ["mn00sa0.map", "0.00", "0.00", "0.25", "0.25"],
                        ["mn00sc0.map", "0.25", "0.00", "0.25", "0.25"],
                        ["mn03ca0.map", "0.50", "0.00", "0.25", "0.25"],
                        ["mn04ca0.map", "0.75", "0.00", "0.25", "0.25"],
                        ["mn03da0.map", "0.00", "0.25", "0.25", "0.25"],
                        ["mn04da0.map", "0.25", "0.25", "0.25", "0.25"],
                        ["mn33sa0.map", "0.50", "0.25", "0.25", "0.25"],
                        ["mn44sa0.map", "0.75", "0.25", "0.25", "0.25"],
                        ["mn44sb0.map", "0.00", "0.50", "0.25", "0.25"],
                        ["mn55sa0.map", "0.25", "0.50", "0.25", "0.25"],
                        ["mn66sa0.map", "0.50", "0.50", "0.25", "0.25"],
                    ]

                # Titan has 3 redundant identical reference sheets for some reason, I combine them here.
                if (
                    StockTiles[7][0] in CSVFile
                    or StockTiles[8][0] in CSVFile
                    or StockTiles[9][0] in CSVFile
                ):
                    CSVMaterial = StockTiles[7][1]
                    CSVData = [
                        ["TI00SA0.MAP", "0.00", "0.00", "0.25", "0.25"],
                        ["TI00SB0.MAP", "0.25", "0.00", "0.25", "0.25"],
                        ["TI01CA0.MAP", "0.50", "0.00", "0.25", "0.25"],
                        ["TI03CA0.MAP", "0.75", "0.00", "0.25", "0.25"],
                        ["TI01CB0.MAP", "0.00", "0.25", "0.25", "0.25"],
                        ["TI01CC0.MAP", "0.25", "0.25", "0.25", "0.25"],
                        ["TI01DA0.MAP", "0.50", "0.25", "0.25", "0.25"],
                        ["TI03DA0.MAP", "0.75", "0.25", "0.25", "0.25"],
                        ["TI03DB0.MAP", "0.00", "0.50", "0.25", "0.25"],
                        ["TI11SA0.MAP", "0.25", "0.50", "0.25", "0.25"],
                        ["TI11SB0.MAP", "0.50", "0.50", "0.25", "0.25"],
                        ["TI33SA0.MAP", "0.75", "0.50", "0.25", "0.25"],
                        ["TI33SB0.MAP", "0.00", "0.75", "0.25", "0.25"],
                    ]

                # VENUS
                if StockTiles[10][0] in CSVFile:
                    CSVMaterial = StockTiles[10][1]
                    CSVData = [
                        ["VE00SA0.MAP", "0.00", "0.00", "0.25", "0.25"],
                        ["VE01CA0.MAP", "0.25", "0.00", "0.25", "0.25"],
                        ["VE02CA0.MAP", "0.50", "0.00", "0.25", "0.25"],
                        ["VE03CA0.MAP", "0.75", "0.00", "0.25", "0.25"],
                        ["VE02CB0.MAP", "0.00", "0.25", "0.25", "0.25"],
                        ["VE01DA0.MAP", "0.25", "0.25", "0.25", "0.25"],
                        ["VE02DA0.MAP", "0.50", "0.25", "0.25", "0.25"],
                        ["VE03DA0.MAP", "0.75", "0.25", "0.25", "0.25"],
                        ["VE11SA0.MAP", "0.00", "0.50", "0.25", "0.25"],
                        ["VE22SA0.MAP", "0.25", "0.50", "0.25", "0.25"],
                        ["VE22SB0.MAP", "0.50", "0.50", "0.25", "0.25"],
                        ["VE33SA0.MAP", "0.75", "0.50", "0.25", "0.25"],
                        ["VE44SA0.MAP", "0.00", "0.75", "0.25", "0.25"],
                    ]

                CustomAtlas = _scene_custom_world_definition(context.scene)
                if CustomAtlas is not None:
                    active_material = (CSVFile or "").strip().lower()
                    custom_material = CustomAtlas["material_name"].lower()
                    if active_material == custom_material or CSVData is None:
                        CSVMaterial = CustomAtlas["material_name"]
                        CSVData = CustomAtlas["csv_data"]
                        _ensure_custom_world_material(
                            CSVMaterial, CustomAtlas["texture_path"]
                        )

                if CSVData is None or CSVMaterial is None:
                    self.report(
                        {"ERROR"},
                        "BZMapIO: Unknown terrain atlas. Import a custom world or use a stock world atlas.",
                    )
                    return {"CANCELLED"}

                # STEP 3) Move every polygon's UV on TextureTiles to match user's map.

                # GET TILE DATA
                MatData = []
                with open(
                    context.scene.BZMapFile.lower().replace(".hg2", ".mat"), "rb"
                ) as f:
                    while byte := f.read(2):
                        matfiledata = hex(int.from_bytes(byte, "big"))
                        matfiledata = matfiledata[2:].zfill(4)
                        MatData.append(
                            [
                                str(matfiledata)[0],
                                str(matfiledata)[1],
                                str(matfiledata)[2],
                                str(matfiledata)[3],
                            ]
                        )

                #  Texture Tile data is stored in 4 bytes, noted below:

                #  0                                1                         2                    3
                # /\ Rotation + Corner info        /\  Variant                /\   Tile           /\  Tile-Transition-to (IE: "05" would be grass to sand on achilles)

                # If bytes 2 and 3 are not the same, it is cap tile unless byte 0 is set to change it to a corner.

                # bpy.ops.mesh.uv_texture_add()
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.context.area.ui_type = "UV"
                bpy.ops.mesh.select_all(action="DESELECT")
                bpy.ops.uv.select_all(action="DESELECT")
                bpy.ops.uv.snap_cursor(target="ORIGIN")
                bpy.context.space_data.pivot_point = "CURSOR"
                PolySelect = bmesh.from_edit_mesh(bpy.context.edit_object.data)
                PolySelect.faces.ensure_lookup_table()  # Blender is dumb and can't do this for the user. Pathetic.
                PolySelect.edges.ensure_lookup_table()

                VariantTable = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]

                # With maps larger than 1280x1280, data is split up into what are called "zones". We compute zones using map width.
                # The amount of zones are always equivalent to this value times itself.
                NumZones = int(
                    bpy.data.node_groups["Geometry Nodes"].nodes["String.002"].string
                )
                NumZones = NumZones * NumZones
                WorkOnTile = False
                OperatingTile = 0
                OperateXQuadrant = False
                ApplyTile = False
                Interlace = 0
                TileOffset = 0
                SelectOffset = 0
                TilePrint = 0

                # Iterates through every tile and applies UV coordinates relative to lower left corner.

                for x in range(0, 4096):
                    PolySelect.faces[x].select = True
                    bpy.ops.uv.select_all(action="SELECT")

                    # Identify the tile to use.
                    TileRotationCorner = MatData[x + (q * 4096)][0]
                    TileVariant = MatData[x + (q * 4096)][1]
                    TileBase = MatData[x + (q * 4096)][2]
                    TileTransition = MatData[x + (q * 4096)][3]

                    # Assemble the tile mix so we can sift through CSV data to choose a tile.

                    TileType = "S"
                    # Is it a solid, cap, or diagonal?
                    if TileBase != TileTransition:
                        TileType = "C"

                        if TileRotationCorner.isdigit() == True:
                            if int(TileRotationCorner) > 7:
                                TileType = "D"
                        if TileRotationCorner.isdigit() != True:
                            TileType = "D"

                    TileSelect = (
                        TileBase
                        + TileTransition
                        + TileType
                        + VariantTable[int(TileVariant)]
                    )
                    TileSelectAlt = (
                        TileBase + TileTransition + TileType + VariantTable[0]
                    )
                    FoundTile = 0

                    for k in range(0, len(CSVData)):
                        if CSVData[k][0][2:6].lower() == TileSelect.lower():
                            TileSelect = CSVData[k]
                            FoundTile = 1
                            break

                    # For whatever reason, it is possible to have tiles tagged as variants without actually being variants.
                    # Try match without variant.
                    if FoundTile == 0:
                        for k in range(0, len(CSVData)):
                            if CSVData[k][0][2:6].lower() == TileSelectAlt.lower():
                                TileSelect = CSVData[k]
                                FoundTile = 1
                                break

                    # If all else fails, we apply the default first tile in place and give up.
                    if FoundTile == 0:
                        TileSelect = CSVData[0]
                        FoundTile = 1

                    # Position and Rotate the tile to correspond with texture tileset.
                    # Sorry about the mess, it was easier to just program each possible mirror/rotation into the mix
                    # due to differences between Blender and BZ.
                    if FoundTile == 1:
                        bpy.ops.uv.snap_cursor(target="ORIGIN")
                        bpy.ops.transform.resize(
                            value=(float(TileSelect[3]), float(TileSelect[4]), 1),
                            orient_type="GLOBAL",
                            orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                            orient_matrix_type="GLOBAL",
                            mirror=False,
                            use_proportional_edit=False,
                            proportional_edit_falloff="SMOOTH",
                            proportional_size=1,
                            use_proportional_connected=False,
                            use_proportional_projected=False,
                            snap=False,
                            snap_elements={"INCREMENT"},
                            use_snap_project=False,
                            snap_target="CLOSEST",
                            use_snap_self=False,
                            use_snap_edit=False,
                            use_snap_nonedit=False,
                            use_snap_selectable=False,
                            release_confirm=True,
                        )
                        bpy.ops.transform.translate(
                            value=(
                                float(TileSelect[1]),
                                1 - float(TileSelect[2]) - float(TileSelect[3]),
                                0,
                            )
                        )

                        # NOTE: The default orientation for the tiles in Blender is different from battlezones so you'll see some differences here.
                        if TileType == "D":
                            bpy.ops.uv.select_all(action="SELECT")
                            bpy.ops.uv.snap_cursor(target="SELECTED")
                            if TileRotationCorner == "8":
                                bpy.ops.transform.rotate(
                                    value=1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(False, True, False),
                                )

                            if TileRotationCorner == "9":
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(False, True, False),
                                )

                            if TileRotationCorner == "a":
                                bpy.ops.transform.rotate(
                                    value=-1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(False, True, False),
                                )

                            if TileRotationCorner == "b":
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(True, False, False),
                                )
                            if TileRotationCorner == "c":
                                bpy.ops.transform.rotate(
                                    value=-3.14159,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                            if TileRotationCorner == "d":
                                bpy.ops.transform.rotate(
                                    value=1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                            # if TileRotationCorner == "e":No rotation needed.
                            if TileRotationCorner == "f":
                                bpy.ops.transform.rotate(
                                    value=-1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )

                        else:  # For TypeType S or C
                            bpy.ops.uv.select_all(action="SELECT")
                            bpy.ops.uv.snap_cursor(target="SELECTED")

                            if TileRotationCorner == "0":
                                bpy.ops.transform.rotate(
                                    value=1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(False, True, False),
                                )

                            if TileRotationCorner == "1":
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(False, True, False),
                                )

                            if TileRotationCorner == "2":
                                bpy.ops.transform.rotate(
                                    value=-1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(False, True, False),
                                )

                            if TileRotationCorner == "3":
                                bpy.ops.transform.rotate(
                                    value=-3.14159,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                                bpy.ops.transform.mirror(
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(False, True, False),
                                )

                            if TileRotationCorner == "4":
                                bpy.ops.transform.rotate(
                                    value=-1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                            if TileRotationCorner == "5":
                                bpy.ops.transform.rotate(
                                    value=-3.14159,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )
                            if TileRotationCorner == "6":
                                bpy.ops.transform.rotate(
                                    value=1.5708,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=False,
                                    use_snap_edit=False,
                                    use_snap_nonedit=False,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )

                            # if TileRotationCorner == "7":No rotation needed
                        bpy.ops.mesh.select_all(action="DESELECT")

                # Subdivide TextureTile object twice so UVs match up more closely to user mesh.
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.mesh.flip_normals()  # uv data transfer only works when normals are facing downward.
                bpy.ops.mesh.subdivide()
                bpy.ops.mesh.subdivide()
                bpy.context.area.ui_type = "VIEW_3D"
                bpy.ops.object.mode_set(mode="OBJECT")

            # After completion, we are left with a number of TextureTiles objects.
            # Select and merge all of them.
            bpy.ops.object.select_all(action="DESELECT")
            for ob in bpy.data.objects:
                if "TextureTiles" in ob.name:
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
            bpy.ops.object.join()
            FinalTileObject = bpy.context.view_layer.objects.active

            # From here, apply data transfer modified to user mesh.
            # Get user's current working map using string match of "HG2"
            for ob in bpy.data.objects:
                if ".hg2_" in ob.name.lower():
                    bpy.context.view_layer.objects.active = ob
                    UserTerrain = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.data.objects[ob.name].hide_select = False
            bpy.ops.object.transform_apply(
                location=True, rotation=True, scale=True
            )  # if transforms aren't applied we get ugly uv distortion.

            ## Transfer UV data from TextureTiles to User Map
            bpy.ops.object.modifier_add(type="DATA_TRANSFER")
            bpy.context.object.modifiers["DataTransfer"].object = bpy.data.objects[
                FinalTileObject.name
            ]
            bpy.context.object.modifiers["DataTransfer"].use_loop_data = True
            bpy.context.object.modifiers["DataTransfer"].data_types_loops = {"UV"}
            bpy.context.object.modifiers["DataTransfer"].loop_mapping = (
                "NEAREST_POLYNOR"
            )
            bpy.ops.object.modifier_apply(modifier="DataTransfer")

            # Select User terrain and apply
            # the texture for the user.
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = UserTerrain
            bpy.data.objects[UserTerrain.name].select_set(True)
            bpy.ops.object.material_slot_remove()
            mat = bpy.data.materials.get(CSVMaterial)
            bpy.context.active_object.data.materials.append(mat)

            # Delete TextureTiles
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = FinalTileObject
            bpy.data.objects[FinalTileObject.name].select_set(True)
            bpy.ops.object.delete(use_global=False, confirm=False)

            ## Hide the BZMapGenerator
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = BZMapGenerator
            bpy.data.objects[BZMapGenerator.name].select_set(True)
            BZMapGenerator.hide_set(True)

            # Restore values back to user's map, these were
            # temporarily changed to generate the texture tiles from the BZMapGenerator object.
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                0
            ].default_value = UserMapDimensions[0]
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                1
            ].default_value = UserMapDimensions[1]
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                2
            ].default_value = UserMapDimensions[2]
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[
                3
            ].default_value = UserMapDimensions[3]

            ####################################################
            # CONSTRUCT THE USER SELECTOR PANEL IN 3D VIEWPORT #
            ####################################################
            # This exists in the 3d viewport and is meant to be used with other buttons present on the main UI.

            # If anything with "TileSelector" in their name is present, get rid of it so it can be replaced.
            for ob in bpy.data.objects:
                if "tileselector" in ob.name.lower():
                    bpy.data.objects[ob.name].hide_select = False
                    bpy.ops.object.select_all(action="DESELECT")
                    bpy.context.view_layer.objects.active = ob
                    bpy.data.objects[ob.name].select_set(True)
                    bpy.ops.object.delete(use_global=False, confirm=False)

            # Set active collection
            layer_collection = bpy.context.view_layer.layer_collection.children[
                "ReferenceVisuals"
            ]
            bpy.context.view_layer.active_layer_collection = layer_collection

            # Create the plane which will represent the user's tileset.
            bpy.ops.mesh.primitive_plane_add(
                enter_editmode=False,
                align="WORLD",
                location=(-400, -300, 1),
                scale=(1, 1, 1),
            )  # FYI scale is broken for this command, it must be 1 1 1
            bpy.context.object.scale[0] = 300
            bpy.context.object.scale[1] = 300
            bpy.context.object.scale[2] = 300
            bpy.context.object.rotation_euler[2] = -1.5708
            bpy.context.active_object.data.materials.append(mat)
            TileSelectContainer = bpy.context.object
            bpy.context.object.name = "TileSelector_Container"
            bpy.data.objects[TileSelectContainer.name].hide_select = True

            # For whatever reason CSV files often contain duplicate entries, this
            # ensures we don't get duplicate selectors.
            PrevCSVEntry = ["None", "None"]

            # Name the tileselector tiles and complete the task!
            for x in range(0, len(CSVData)):
                if PrevCSVEntry != [CSVData[x][1], CSVData[x][2]]:
                    PosX = (600 - (float(CSVData[x][2]) * 600)) - (
                        600 * float(CSVData[x][3])
                    ) / 2
                    PosY = (
                        float(CSVData[x][1]) * -600 - (600 * float(CSVData[x][3])) / 2
                    )
                    bpy.ops.mesh.primitive_cube_add(
                        enter_editmode=False,
                        align="WORLD",
                        location=(PosX - 700, PosY, 1),
                        scale=(1, 1, 1),
                    )

                    # Tile selectors contain the following information:
                    # 1) Texture Tile (only relevant bits, IE: 00SA is tile 0 solid with no variant)
                    # 2) X Coordinate
                    # 3) Y Coordinate
                    # 4) Scale
                    # 5) X.XX (not BZ data, used by plugin to identify assigned tiles, gets replaced by 1.00, 1.0C or 1.0D)
                    bpy.context.object.name = (
                        "TileSelector_Tile_"
                        + str(x)
                        + ":"
                        + CSVData[x][0][2:-5]
                        + ":"
                        + CSVData[x][1]
                        + ":"
                        + CSVData[x][2]
                        + ":"
                        + CSVData[x][3]
                        + ":X.XX"
                    )

                    bpy.context.object.scale[0] = 5
                    bpy.context.object.scale[1] = 5
                    bpy.context.object.scale[2] = 5
                    PrevCSVEntry = [CSVData[x][1], CSVData[x][2]]
        # If the users MAT file does not match the size of the terrain, initiate prompt to generate blank file.
        else:
            MatGeneratorPrompt = None
            # Check for existence of a MatGenerator object
            for ob in bpy.data.objects:
                if "matgeneratorprompt" in ob.name.lower():
                    MatGeneratorPrompt = ob
                    break

            MatMapSize = 0

            if len(MatData) == 4096:
                MatMapSize = "1280x1280"
            if len(MatData) == 16384:
                MatMapSize = "2560x2560"
            if len(MatData) == 36864:
                MatMapSize = "3840x3840"
            if len(MatData) == 65536:
                MatMapSize = "5120x5120"

            # If the MatGeneratorPrompt does not exist, create it and prompt user.
            if MatGeneratorPrompt == None:
                bpy.ops.object.select_all(action="DESELECT")
                bpy.ops.object.empty_add(
                    type="PLAIN_AXES",
                    align="WORLD",
                    location=(0, 0, 0),
                    scale=(1, 1, 1),
                )
                bpy.context.object.name = "MatGeneratorPrompt_0"
                self.report(
                    {"WARNING"},
                    "BZMapIO:  .MAT size "
                    + str(MatMapSize)
                    + " size mismatch. Click Import 4 times to overwrite with blank sized template.",
                )

            # If it does exist, check the number and increment it... if the number is
            # high enough, execute a blank file write.
            else:

                if int(MatGeneratorPrompt.name[-1:]) < 3:
                    MatGeneratorPrompt.name = MatGeneratorPrompt.name[:-1] + str(
                        int(MatGeneratorPrompt.name[-1:]) + 1
                    )
                    if int(MatGeneratorPrompt.name[-1:]) == 3:
                        self.report(
                            {"ERROR"},
                            "BZMapIO: WARNING! CLICKING THIS ONE MORE TIME WILL OVERWRITE YOUR .MAT FILE. There is no undo for this. Click import again to confirm.",
                        )
                    else:
                        self.report(
                            {"WARNING"},
                            "BZMapIO:  .MAT size "
                            + MatMapSize
                            + " size mismatch. Click Import "
                            + str(4 - int(MatGeneratorPrompt.name[-1:]))
                            + " more times to overwrite with blank sized template.",
                        )
                else:
                    UserTerrainMapSize = str(
                        int(
                            bpy.data.node_groups["Geometry Nodes"]
                            .nodes["Grid"]
                            .inputs[0]
                            .default_value
                        )
                    )

                    # Overwrite the user's .MAT file with a blank that matches the size of the terrain.
                    output = b""
                    TileAssembly = struct.pack(">BB", 0 << 4 | 0, 0 << 4 | 0)
                    for q in range(0, (MapDepth * MapDepth) * 4096):
                        output += TileAssembly
                    with open(
                        context.scene.BZMapFile.lower().replace(".hg2", ".mat"), "wb"
                    ) as fp:
                        fp.write(output)

                    # Get rid of any MatGeneratorPrompt if present.
                    MatGeneratorPrompt = None
                    for ob in bpy.data.objects:
                        if "matgeneratorprompt" in ob.name.lower():
                            MatGeneratorPrompt = ob
                            break
                    # Delete MatGeneratorPrompt
                    if MatGeneratorPrompt != None:
                        bpy.ops.object.select_all(action="DESELECT")
                        bpy.context.view_layer.objects.active = MatGeneratorPrompt
                        bpy.data.objects[MatGeneratorPrompt.name].select_set(True)
                        bpy.ops.object.delete(use_global=False, confirm=False)

                    self.report(
                        {"WARNING"},
                        "BZMapIO: .MAT overriden with blank "
                        + UserTerrainMapSize
                        + "x"
                        + UserTerrainMapSize
                        + " file. Import again to load this blank.",
                    )

        return {"FINISHED"}


###########################################################################
# TILE SELECTOR BUTTONS ON UI. AFTER PRESSED IT LOOKS AT THE 3D VIEWPORT
# TO DETERMINE IF A TILE IS SELECTED, AND ASSIGNS A WEIGHT MAP VALUE
# TO THAT TILE.

# "D" and "C" buttons refer to diagonal and cap assignments.
###########################################################################


# When a TileSelector_Tile is selected, set Tile.
# Used prior to the Apply Tile Paint button
class bzbutton_settile1a(bpy.types.Operator):
    bl_idname = "button.bzsettile1a"
    bl_label = "Set Solid"
    bl_description = "Makes Weight Painted Values of 1.0 apply the selected tile."

    def execute(self, context):
        # Get selected object.
        UserSelection = bpy.context.selected_objects
        if UserSelection != []:
            if "TileSelector_Tile_" not in UserSelection[0].name:
                self.report(
                    {"WARNING"},
                    "BZMapIO:  Select a texture tile to assign it to a weight value",
                )
            else:
                # Look through all over tiles to make sure they don't have same value.
                for ob in bpy.data.objects:
                    if "tileselector_tile_" in ob.name.lower():
                        if ob.name[-4:] == "1.00":
                            ob.name = ob.name[:-4] + "X.XX"
                UserSelection[0].name = UserSelection[0].name[:-4] + "1.00"
                self.report(
                    {"WARNING"}, "BZMapIO:  Tile for weight map value of 1.0 set!"
                )
        if UserSelection == []:
            self.report(
                {"WARNING"},
                "BZMapIO:  Select a texture tile to assign it to a weight value",
            )
        return {"FINISHED"}


class bzbutton_settile1b(bpy.types.Operator):
    bl_idname = "button.bzsettile1b"
    bl_label = "Set Diagonal"
    bl_description = "Diagonal tile to use for weight value of 1.0."

    def execute(self, context):
        # Get selected object.
        UserSelection = bpy.context.selected_objects
        if UserSelection != []:
            if "TileSelector_Tile_" not in UserSelection[0].name:
                self.report(
                    {"WARNING"},
                    "BZMapIO:  Select a texture tile to assign it to a weight value",
                )
            else:
                # Look through all over tiles to make sure they don't have same value.
                for ob in bpy.data.objects:
                    if "tileselector_tile_" in ob.name.lower():
                        if ob.name[-4:] == "1.0D":
                            ob.name = ob.name[:-4] + "X.XX"
                UserSelection[0].name = UserSelection[0].name[:-4] + "1.0D"
                self.report(
                    {"WARNING"},
                    "BZMapIO:  Diagonal Tile for weight map value of 1.0 set!",
                )
        if UserSelection == []:
            self.report(
                {"WARNING"},
                "BZMapIO:  Select a texture tile to assign it to a weight value",
            )
        return {"FINISHED"}


class bzbutton_settile1c(bpy.types.Operator):
    bl_idname = "button.bzsettile1c"
    bl_label = "Set Cap"
    bl_description = "Diagonal tile to use for weight value of 1.0."

    def execute(self, context):
        # Get selected object.
        UserSelection = bpy.context.selected_objects
        if UserSelection != []:
            if "TileSelector_Tile_" not in UserSelection[0].name:
                self.report(
                    {"WARNING"},
                    "BZMapIO:  Select a texture tile to assign it to a weight value",
                )
            else:
                # Look through all over tiles to make sure they don't have same value.
                for ob in bpy.data.objects:
                    if "tileselector_tile_" in ob.name.lower():
                        if ob.name[-4:] == "1.0C":
                            ob.name = ob.name[:-4] + "X.XX"
                UserSelection[0].name = UserSelection[0].name[:-4] + "1.0C"
                self.report(
                    {"WARNING"}, "BZMapIO:  Cap Tile for weight map value of 1.0 set!"
                )
        if UserSelection == []:
            self.report(
                {"WARNING"},
                "BZMapIO:  Select a texture tile to assign it to a weight value",
            )
        return {"FINISHED"}


class bzbutton_clearpaint(bpy.types.Operator):
    bl_idname = "button.bzclearpaint"
    bl_label = "Clear All Paint"
    bl_description = "Removes all weight painting from map."
    bl_options = {"REGISTER", "UNDO"}  # Enable undo for the operator.

    def execute(self, context):
        if bpy.context.active_object.mode == "WEIGHT_PAINT":
            bpy.ops.object.vertex_group_clean(limit=1)
        return {"FINISHED"}


class bzbutton_applytilepaint(bpy.types.Operator):
    bl_idname = "button.bzapplytilepaint"
    bl_label = "Apply Tile Paint"
    bl_description = "Uses painted weight maps to place tiles."

    def execute(self, context):

        EnableSolids = context.scene.BZMapIO_Toggles.EnableSolids
        EnableDiagonals = context.scene.BZMapIO_Toggles.EnableDiagonals
        EnableCaps = context.scene.BZMapIO_Toggles.EnableCaps

        # There's a good chance the user is gonna want to use this applicator while in weight paint mode.
        # I conveniently switch back to it after operation if its enabled.
        UIWeightSwitch = False
        if bpy.context.weight_paint_object != None:
            UIWeightSwitch = True  # this means user was in weight painting mode, switch back at end of operation.
        OriginalContext = context.space_data.type
        bpy.ops.object.mode_set(mode="OBJECT")

        # Collect all TileSelector objects
        TileSelector_Tiles = []
        for ob in bpy.data.objects:
            if "tileselector_tile" in ob.name.lower():
                TileSelector_Tiles.append(ob)

        # Get user terrain
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                UserTerrain = ob
                break

        # Select user terrain
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = UserTerrain
        bpy.data.objects[UserTerrain.name].select_set(True)

        # The user terrain must be almost completely flat to avoid trashy
        # uv maps when unwrapping. We change this back at the end of the operation.
        bpy.context.object.scale[2] = 0.001
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

        # Blender doesn't provide the user with an obvious way to completely erase
        # weight maps (it leaves values of "0" everywhere), so we clean up before operating.
        # to prevent large computations from happening unneccecarily.
        bpy.ops.object.vertex_group_clean(limit=0.1)

        MapWidth = (
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[3].default_value
            - 1
        )

        # Go into edit mode, select polygons.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="VERT")
        bpy.ops.mesh.select_all(action="DESELECT")

        # Here, we are going through each weight map value range,
        # de-selecting the vertex points outside of that value range,
        # then adjusting the resulting UV tiles to match the user's
        # tile selection for that range.

        obj = bpy.context.object

        def get_weights(ob, vgroup):
            group_index = vgroup.index
            for i, v in enumerate(ob.data.vertices):
                for g in v.groups:
                    if g.group == group_index:
                        yield (i, g.weight)
                        break

        obj = bpy.context.object
        vgroup = obj.vertex_groups[0]
        weights = list(
            get_weights(obj, vgroup)
        )  # weights is a list with index and weight map value

        PolySelect = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        PolySelect.faces.ensure_lookup_table()
        PolySelect.verts.ensure_lookup_table()

        LoopCount = []
        for x in range(0, len(TileSelector_Tiles)):

            # Corner and Cap tiles have letters as part of their name,
            # neccecitating an additional check.
            IsNumeric = False
            try:
                float(TileSelector_Tiles[x].name[-4:])
                IsNumeric = True
            except ValueError:
                IsNumeric = False

            if TileSelector_Tiles[x].name[-4:] != "X.XX" and IsNumeric == True:

                # Insert ranges of operation for each weight value.
                if TileSelector_Tiles[x].name[-4:] == "1.00":
                    ValueA = 0.95
                    ValueB = 1.05

                if TileSelector_Tiles[x].name[-4:] == "0.75":
                    ValueA = 0.70
                    ValueB = 0.80

                if TileSelector_Tiles[x].name[-4:] == "0.50":
                    ValueA = 0.45
                    ValueB = 0.55

                if TileSelector_Tiles[x].name[-4:] == "0.25":
                    ValueA = 0.20
                    ValueB = 0.30

                LoopCount.append([TileSelector_Tiles[x].name[-4:], ValueA, ValueB])

        for z in range(0, len(LoopCount)):

            # TILE SELECTION
            for x in range(0, len(weights)):

                # Here, the goal is to select a point range which corresponds with a very specific value.
                # at a very narrow range.
                if weights[x][1] > LoopCount[z][1] and weights[x][1] < LoopCount[z][2]:
                    PolySelect.verts[weights[x][0]].select = True

            bpy.ops.mesh.select_linked(delimit={"SEAM"})

            # Save selection so we can recall it.
            SelectedFaces = [v for v in PolySelect.faces if v.select]

            # Modify ShellFaces so that the outermost edges of the map
            # are de-selected. Due to an oversight on my part the editor
            # can't paint these outermost tiles.
            for v in range(0, len(SelectedFaces)):
                if (
                    SelectedFaces[v].index > len(PolySelect.faces) - MapWidth * 3
                    or SelectedFaces[v].index % MapWidth == MapWidth - 1
                    or SelectedFaces[v].index % MapWidth == MapWidth - 2
                    or SelectedFaces[v].index % MapWidth == MapWidth - 3
                ):
                    SelectedFaces[v].select = False
            SelectedFaces = [v for v in PolySelect.faces if v.select]

            # Identify which TileSelector_Tile object is set to a target weight map value
            for x in range(0, len(TileSelector_Tiles)):
                if TileSelector_Tiles[x].name[-4:] == LoopCount[z][0]:
                    TargetTile = TileSelector_Tiles[x].name
                    break

            # Split target tile gives us the following:
            # TargetTile[0] - Object Name
            # TargetTile[1] - X Coordinate
            # TargetTile[2] - Y Coordinate
            # TargetTile[3] - Scale
            # TargetTile[4] - Weight Value

            TargetTile = TargetTile.split(":")

            ##################################################################
            #                          APPLY UV TILE                         #
            ##################################################################

            # To speed things along, all we do is copy the uv map of ONE modified tile then
            # paste it to all the other tiles simultaneously. Moving tiles
            # individually is far too slow to do in python.

            # Only process if a selection is present.

            if SelectedFaces != [] and EnableSolids == True:
                bpy.ops.mesh.select_all(action="DESELECT")
                SelectedFaces[0].select = True
                bpy.ops.mesh.select_linked(delimit={"SEAM"})
                bpy.ops.uv.select_all(action="SELECT")
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.context.area.ui_type = "UV"

                # Unwrap must be performed on the tile or else copy paste UVs can fail.
                bpy.ops.uv.smart_project(island_margin=0.001, correct_aspect=False)
                # Scale uvs to match size of tile.
                bpy.ops.transform.resize(
                    value=(
                        float(TargetTile[4]),
                        float(TargetTile[4]),
                        float(TargetTile[4]),
                    ),
                    orient_type="GLOBAL",
                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                    orient_matrix_type="GLOBAL",
                    constraint_axis=(True, True, True),
                    mirror=False,
                    use_proportional_edit=False,
                    proportional_edit_falloff="SMOOTH",
                    proportional_size=1,
                    use_proportional_connected=False,
                    use_proportional_projected=False,
                    snap=False,
                    snap_elements={"INCREMENT"},
                    use_snap_project=False,
                    snap_target="CENTER",
                    use_snap_self=True,
                    use_snap_edit=True,
                    use_snap_nonedit=True,
                    use_snap_selectable=False,
                    release_confirm=True,
                )
                bpy.ops.uv.snap_cursor(target="ORIGIN")
                bpy.ops.uv.snap_selected(target="CURSOR_OFFSET")
                bpy.ops.transform.translate(
                    value=(
                        (float(TargetTile[4]) / 2) + float(TargetTile[2]),
                        (float(TargetTile[4]) / 2)
                        + 1
                        - (float(TargetTile[3]))
                        - float(TargetTile[4]),
                        0,
                    )
                )
                bpy.ops.uv.copy()

                for x in range(0, len(SelectedFaces)):
                    SelectedFaces[x].select = True
                bpy.ops.uv.select_all(action="SELECT")

                # Before pasting, an unwrap is needed, otherwise theres a chance paste uvs will fail on tiles.
                bpy.ops.uv.smart_project(island_margin=0.001, correct_aspect=False)
                bpy.ops.uv.paste()

            ################################################################################
            ################################################################################
            ################################################################################
            ################################################################################

            # PROCESS DIAGONAL TILES. We perform a series of steps in order to identify them:

            # 1) Expand selection (made by user weight map) by 1 polygon

            # 2) De-select just the user weight map, leaving only the "shell"

            # 3) De-select all polygons from this outline which do not have two adjacent user-weight polygons
            # by checking previous/next and +row -row . Store corner data with this selection.

            # 4) Isolate 1 tile. Unwrap and place. Repeat for each of the 4 possible corner tiles.
            # NOTE: will need to determine if unwrapping gives same coords every time!

            ################################################################################
            ################################################################################
            ################################################################################
            ################################################################################

            # Reset UV selection.
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.select_all(action="DESELECT")
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.context.area.ui_type = "UV"

            TargetCorner = None
            # determine which weight value it is.
            if TargetTile[5] == "1.00":
                TargetCorner = "1.0D"
            if TargetTile[5] == "0.75":
                TargetCorner = "0.7D"
            if TargetTile[5] == "0.50":
                TargetCorner = "0.5D"
            if TargetTile[5] == "0.25":
                TargetCorner = "0.2D"

            # If the Tile of a weight value is found, determine whether or not the user
            # has assigned it a corner.

            TargetCornerTile = None
            if TargetCorner != None:

                # We gotta be a little more nuanced with the diagonals flag because
                # caps depends on the computations made for diagonals.
                if EnableDiagonals == True or EnableCaps == True:

                    # Obtain the name of the TileSelector_Tile object containing the coordinate data for the user corner tile.
                    for x in range(0, len(TileSelector_Tiles)):
                        if TileSelector_Tiles[x].name[-4:] == TargetCorner:
                            TargetCornerTile = TileSelector_Tiles[x]
                            break

                    if TargetCornerTile != None:
                        TargetCornerTile = TargetCornerTile.name.split(":")

                        # Go into edit mode, select vertices form user weight map.
                        bpy.ops.object.mode_set(mode="EDIT")
                        bpy.ops.mesh.select_mode(
                            use_extend=False, use_expand=False, type="FACE"
                        )
                        for x in range(0, len(SelectedFaces)):
                            SelectedFaces[x].select = True
                        bpy.ops.uv.select_all(action="SELECT")
                        bpy.ops.mesh.select_more()

                        # DE-select faces, leaving only the outer shell.
                        for x in range(0, len(SelectedFaces)):
                            SelectedFaces[x].select = False

                        ShellFaces = [v for v in PolySelect.faces if v.select]
                        # Modify ShellFaces so that the outermost edges of the map
                        # are de-selected. Due to an oversight on my part the editor
                        # can't paint these outermost tiles.
                        for v in range(0, len(ShellFaces)):
                            if (
                                ShellFaces[v].index
                                > len(PolySelect.faces) - MapWidth * 3
                                or ShellFaces[v].index % MapWidth == MapWidth - 1
                                or ShellFaces[v].index % MapWidth == MapWidth - 2
                                or ShellFaces[v].index % MapWidth == MapWidth - 3
                            ):
                                ShellFaces[v].select = False
                        ShellFaces = [v for v in PolySelect.faces if v.select]

                        # Isolate ONLY faces on corners. We do this by determining whether or not a polygon has
                        # EXACTLY 2 neighboring polygons: 1 on the X axis, and 1 on the Y axis.
                        DeselectFaces = []
                        for x in range(0, len(ShellFaces)):
                            XDetect = 0
                            YDetect = 0
                            if PolySelect.faces[ShellFaces[x].index + 1].select == True:
                                XDetect += 1
                            if PolySelect.faces[ShellFaces[x].index - 1].select == True:
                                XDetect += 1

                            if (
                                PolySelect.faces[ShellFaces[x].index + MapWidth].select
                                == True
                            ):
                                YDetect += 1
                            if (
                                PolySelect.faces[ShellFaces[x].index - MapWidth].select
                                == True
                            ):
                                YDetect += 1

                            if XDetect != 1 and YDetect != 1:
                                DeselectFaces.append(ShellFaces[x])

                        for x in range(0, len(DeselectFaces)):
                            DeselectFaces[x].select = False

                        # The step above MOSTLY works, but we only want the INNER corners, the method above
                        # also selects OUTER corners. The following steps give us only corners reliably:
                        # 1) Re-select the original weight map polys.
                        # 2) de-select all polygons without any neighbors left or right.
                        # 3) de-select original weight map polys.

                        for x in range(0, len(SelectedFaces)):
                            SelectedFaces[x].select = True
                        bpy.ops.uv.select_all(action="SELECT")

                        # Eliminate faces without any selected neighbors.
                        DeselectFaces = []
                        for x in range(0, len(ShellFaces)):
                            XDetect = 0
                            if PolySelect.faces[ShellFaces[x].index + 1].select == True:
                                XDetect += 1
                            if PolySelect.faces[ShellFaces[x].index - 1].select == True:
                                XDetect += 1

                            if XDetect == 0:
                                DeselectFaces.append(ShellFaces[x])

                        for x in range(0, len(DeselectFaces)):
                            DeselectFaces[x].select = False

                        for x in range(0, len(SelectedFaces)):
                            SelectedFaces[x].select = False

                        # At this point, we have every corner tile selected and stored.
                        # Now, process every single corner tile individually.
                        CornerFaces = [v for v in PolySelect.faces if v.select]
                        bpy.ops.mesh.select_all(action="DESELECT")

                        CornerTilesA = []
                        CornerTilesB = []
                        CornerTilesC = []
                        CornerTilesD = []

                        # This segment collects, then sorts each corner tile into 1 of 4 groups. Again, python is too slow to
                        # process tiles 1 by 1, so we have to prep everything for batch operations.
                        for x in range(0, len(CornerFaces)):
                            bpy.ops.mesh.select_all(action="DESELECT")
                            CornerFaces[x].select = True
                            bpy.ops.mesh.select_linked(delimit={"SEAM"})
                            bpy.ops.uv.select_all(action="SELECT")

                            # We can detect the rotation of the corner
                            # by checking neighboring Left right up and down polygons after
                            # select-linked fills the whole tile.
                            XPosDetect = 0
                            YPosDetect = 0
                            XNegDetect = 0
                            YNegDetect = 0

                            if (
                                PolySelect.faces[CornerFaces[x].index + 1].select
                                == True
                            ):
                                XPosDetect = 1
                            if (
                                PolySelect.faces[CornerFaces[x].index - 1].select
                                == True
                            ):
                                XNegDetect = 1
                            if (
                                PolySelect.faces[CornerFaces[x].index + MapWidth].select
                                == True
                            ):
                                YPosDetect = 1
                            if (
                                PolySelect.faces[CornerFaces[x].index - MapWidth].select
                                == True
                            ):
                                YNegDetect = 1

                            # Detect and sort the direction of the tiles into 4 groups.

                            # Diagonal left-down = 0 1 0 1
                            if XNegDetect == 1 and YNegDetect == 1:
                                CornerTilesA.append(CornerFaces[x])

                            # Diagonal left-up = 0 1 1 0
                            if XNegDetect == 1 and YPosDetect == 1:
                                CornerTilesB.append(CornerFaces[x])

                            # Diagonal right-up = 1 0 1 0
                            if XPosDetect == 1 and YPosDetect == 1:
                                CornerTilesC.append(CornerFaces[x])

                            # Diagonal right-down = 1 0 0 1
                            if XPosDetect == 1 and YNegDetect == 1:
                                CornerTilesD.append(CornerFaces[x])

                    if EnableDiagonals == True:
                        # Unwrap template corner tiles meant for copy pasting to other tiles.
                        def UnwrapCornerTiles(CornerTileList):

                            bpy.ops.uv.select_all(action="DESELECT")
                            bpy.ops.mesh.select_all(action="DESELECT")
                            CornerTileList[0].select = True
                            bpy.ops.mesh.select_linked(delimit={"SEAM"})
                            bpy.ops.uv.select_all(action="SELECT")
                            bpy.ops.uv.smart_project(
                                island_margin=0.001,
                                correct_aspect=False,
                                scale_to_bounds=True,
                            )

                        if len(CornerTilesA) != 0:
                            UnwrapCornerTiles(CornerTilesA)
                        if len(CornerTilesB) != 0:
                            UnwrapCornerTiles(CornerTilesB)
                        if len(CornerTilesC) != 0:
                            UnwrapCornerTiles(CornerTilesC)
                        if len(CornerTilesD) != 0:
                            UnwrapCornerTiles(CornerTilesD)

                        # Desperate for performance, we multi-select the tiles prior
                        # to running a UV alignment on them because the operation is 4x
                        # slower if its used on each tile individually. While there are
                        # faster methods, those methods require packing. Packing
                        # crashes Blender when called from a python script. This makes me sad.
                        if len(CornerTilesA) != 0:
                            CornerTilesA[0].select = True
                        if len(CornerTilesB) != 0:
                            CornerTilesB[0].select = True
                        if len(CornerTilesC) != 0:
                            CornerTilesC[0].select = True
                        if len(CornerTilesD) != 0:
                            CornerTilesD[0].select = True
                        bpy.ops.mesh.select_linked(delimit={"SEAM"})
                        bpy.ops.uv.select_all(action="SELECT")
                        bpy.ops.uv.align_rotation(
                            method="GEOMETRY", axis="X"
                        )  # Extremely inefficient, but the only option.

                        # Now we can finally process the corner tiles. A single tile is reset, unwrapped
                        # then rotated, then the result is copied onto all of the other tiles.
                        def ApplyCornerTiles(
                            CornerTileList, TargetCornerTile, RotationValue
                        ):
                            bpy.ops.uv.select_all(action="DESELECT")
                            bpy.ops.mesh.select_all(action="DESELECT")
                            for z in range(0, len(CornerTileList)):
                                # Process first tile.
                                if z == 0:
                                    CornerTileList[z].select = True
                                    bpy.ops.mesh.select_linked(delimit={"SEAM"})
                                    bpy.ops.uv.select_all(action="SELECT")

                                    # Rotate tile
                                    bpy.ops.transform.rotate(
                                        value=RotationValue,
                                        orient_axis="Z",
                                        orient_type="VIEW",
                                        orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                        orient_matrix_type="VIEW",
                                        mirror=False,
                                        use_proportional_edit=False,
                                        proportional_edit_falloff="SMOOTH",
                                        proportional_size=1,
                                        use_proportional_connected=False,
                                        use_proportional_projected=False,
                                        snap=False,
                                        snap_elements={"INCREMENT"},
                                        use_snap_project=False,
                                        snap_target="CENTER",
                                        use_snap_self=True,
                                        use_snap_edit=True,
                                        use_snap_nonedit=True,
                                        use_snap_selectable=False,
                                    )

                                    # Scale uvs to match size of tile.
                                    bpy.ops.transform.resize(
                                        value=(
                                            float(TargetCornerTile[4]),
                                            float(TargetCornerTile[4]),
                                            float(TargetCornerTile[4]),
                                        ),
                                        orient_type="GLOBAL",
                                        orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                        orient_matrix_type="GLOBAL",
                                        constraint_axis=(True, True, True),
                                        mirror=False,
                                        use_proportional_edit=False,
                                        proportional_edit_falloff="SMOOTH",
                                        proportional_size=1,
                                        use_proportional_connected=False,
                                        use_proportional_projected=False,
                                        snap=False,
                                        snap_elements={"INCREMENT"},
                                        use_snap_project=False,
                                        snap_target="CENTER",
                                        use_snap_self=True,
                                        use_snap_edit=True,
                                        use_snap_nonedit=True,
                                        use_snap_selectable=False,
                                        release_confirm=True,
                                    )

                                    # Place tile
                                    bpy.ops.uv.snap_cursor(target="ORIGIN")
                                    bpy.ops.uv.snap_selected(target="CURSOR_OFFSET")
                                    bpy.ops.transform.translate(
                                        value=(
                                            (float(TargetCornerTile[4]) / 2)
                                            + float(TargetCornerTile[2]),
                                            (float(TargetCornerTile[4]) / 2)
                                            + 1
                                            - (float(TargetCornerTile[3]))
                                            - float(TargetCornerTile[4]),
                                            0,
                                        )
                                    )
                                    bpy.ops.uv.copy()
                                else:
                                    CornerTileList[z].select = True

                            bpy.ops.mesh.select_linked(delimit={"SEAM"})
                            bpy.ops.uv.select_all(action="SELECT")
                            # Unwrap needed prior to pasting uvs because it fails otherwise. No idea why.
                            bpy.ops.uv.smart_project(
                                island_margin=0.001, correct_aspect=False
                            )
                            bpy.ops.uv.paste()

                        bpy.ops.uv.select_all(action="DESELECT")
                        bpy.ops.mesh.select_all(action="DESELECT")
                        ApplyCornerTiles(CornerTilesA, TargetCornerTile, 1.56426)
                        ApplyCornerTiles(CornerTilesB, TargetCornerTile, 0)
                        ApplyCornerTiles(CornerTilesC, TargetCornerTile, 4.71239)
                        ApplyCornerTiles(CornerTilesD, TargetCornerTile, -3.14159)

            ##################################################################
            ##################################################################
            ####                         CAPS                             ####
            ##################################################################
            ##################################################################

            # Reset UV selection.
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.select_all(action="DESELECT")
            bpy.ops.mesh.select_all(action="DESELECT")

            # First determine whether or not the user defined a cap.
            TargetCap = None
            TargetCapTile = None

            # determine which weight value it is.
            if TargetTile[5] == "1.00":
                TargetCap = "1.0C"
            if TargetTile[5] == "0.75":
                TargetCap = "0.7C"
            if TargetTile[5] == "0.50":
                TargetCap = "0.5C"
            if TargetTile[5] == "0.25":
                TargetCap = "0.2C"

            # Obtain the name of the TileSelector_Tile object containing the coordinate data for the user CAP tile.
            for x in range(0, len(TileSelector_Tiles)):
                if TileSelector_Tiles[x].name[-4:] == TargetCap:
                    TargetCapTile = TileSelector_Tiles[x]
                    break

            # PROCESS CAPS IF CAP TILE IS ASSIGNED!
            if TargetCapTile != None and EnableCaps == True:

                # Obtain the name of the TileSelector_Tile object containing the coordinate data for the user corner tile.
                for x in range(0, len(TileSelector_Tiles)):
                    if TileSelector_Tiles[x].name[-4:] == TargetCap:
                        TargetCapTile = TileSelector_Tiles[x]
                        break

                if TargetCapTile != None:
                    TargetCapTile = TargetCapTile.name.split(":")

                    # Go into edit mode, select vertices form user weight map.
                    bpy.ops.object.mode_set(mode="EDIT")
                    bpy.ops.mesh.select_mode(
                        use_extend=False, use_expand=False, type="FACE"
                    )
                    for x in range(0, len(SelectedFaces)):
                        SelectedFaces[x].select = True
                    bpy.ops.uv.select_all(action="SELECT")
                    bpy.ops.mesh.select_more()

                    # DE-select faces, leaving only the outer shell.
                    for x in range(0, len(SelectedFaces)):
                        SelectedFaces[x].select = False

                    ShellFaces = [v for v in PolySelect.faces if v.select]

                    # Modify ShellFaces so that the outermost edges of the map
                    # are de-selected. Due to an oversight on my part the editor
                    # can't paint these outermost tiles.
                    for v in range(0, len(ShellFaces)):
                        if (
                            ShellFaces[v].index > len(PolySelect.faces) - MapWidth * 3
                            or ShellFaces[v].index % MapWidth == MapWidth - 1
                            or ShellFaces[v].index % MapWidth == MapWidth - 2
                            or ShellFaces[v].index % MapWidth == MapWidth - 3
                        ):
                            ShellFaces[v].select = False
                    ShellFaces = [v for v in PolySelect.faces if v.select]

                    # Ok, here's the method I use to detect corners...
                    # its cro-magnon-level coding but it works:
                    # If the shell selection has neighboring
                    # selected polygons both on the flat X and Y axis,
                    # Flag that polygon and the nearest 3 polygons
                    # in the X and Y axis. This guarantees we'll
                    # be left with ONLY valid cap tiles.
                    # It is important the detection happens before
                    # de-selection.

                    # Then we have to process the selection to deselect
                    # all but 1 polygon on each cap... we do this by eliminating
                    # selected polygons which have 2 adjacent X neighbors,
                    # then eliminate polygons with 2 semi-adjacent X neighbors.
                    DeselectFaces = []

                    # Eliminate selection of all corner polygons
                    for x in range(0, len(ShellFaces)):
                        XDetect = 0
                        YDetect = 0
                        XNegSelect = PolySelect.faces[ShellFaces[x].index + 1].select
                        XPosSelect = PolySelect.faces[ShellFaces[x].index - 1].select
                        YNegSelect = PolySelect.faces[
                            ShellFaces[x].index - MapWidth
                        ].select
                        YPosSelect = PolySelect.faces[
                            ShellFaces[x].index + MapWidth
                        ].select

                        if XNegSelect == True:
                            XDetect += 1
                        if XPosSelect == True:
                            XDetect += 1
                        if YNegSelect == True:
                            YDetect += 1
                        if YPosSelect == True:
                            YDetect += 1

                        # If a corner is detected, de-select 3 polygons in every direction.
                        if XDetect != 0 and YDetect != 0:
                            DeselectFaces.append(ShellFaces[x].index)

                    # Remove all selected polygons which are within a
                    # 3x3 grid of all detected corners.
                    for x in range(0, len(DeselectFaces)):
                        PolySelect.faces[DeselectFaces[x]].select = False
                        PolySelect.faces[DeselectFaces[x] - 1].select = False
                        PolySelect.faces[DeselectFaces[x] - 2].select = False
                        PolySelect.faces[DeselectFaces[x] - 3].select = False
                        PolySelect.faces[DeselectFaces[x] + 1].select = False
                        PolySelect.faces[DeselectFaces[x] + 2].select = False
                        PolySelect.faces[DeselectFaces[x] + 3].select = False
                        PolySelect.faces[DeselectFaces[x] - MapWidth].select = False
                        PolySelect.faces[
                            DeselectFaces[x] - MapWidth - MapWidth
                        ].select = False
                        PolySelect.faces[
                            DeselectFaces[x] - MapWidth - MapWidth - MapWidth
                        ].select = False
                        PolySelect.faces[DeselectFaces[x] + MapWidth].select = False
                        PolySelect.faces[
                            DeselectFaces[x] + MapWidth + MapWidth
                        ].select = False
                        PolySelect.faces[
                            DeselectFaces[x] + MapWidth + MapWidth + MapWidth
                        ].select = False

                    # Now we need to cut down the selection further. Remove all polygons which have
                    # two adjacent neighbors.

                    for x in range(0, len(ShellFaces)):
                        XDetect = 0
                        YDetect = 0
                        XNegSelect = PolySelect.faces[ShellFaces[x].index + 1].select
                        XPosSelect = PolySelect.faces[ShellFaces[x].index - 1].select
                        YNegSelect = PolySelect.faces[
                            ShellFaces[x].index - MapWidth
                        ].select
                        YPosSelect = PolySelect.faces[
                            ShellFaces[x].index + MapWidth
                        ].select

                        if XNegSelect == True:
                            XDetect += 1
                        if XPosSelect == True:
                            XDetect += 1
                        if YNegSelect == True:
                            YDetect += 1
                        if YPosSelect == True:
                            YDetect += 1

                        if XDetect == 2 or YDetect == 2:
                            PolySelect.faces[ShellFaces[x].index].select = False

                    # Final pass decimates selection to the point to where every cap tile consists of
                    # a single polygon selection adjacent to user paint selection, making it easy
                    # to determine which direction the cap should be oriented.

                    for x in range(0, len(ShellFaces)):
                        XDetect = 0
                        YDetect = 0
                        XNegSelect = PolySelect.faces[ShellFaces[x].index + 2].select
                        XPosSelect = PolySelect.faces[ShellFaces[x].index - 2].select
                        YNegSelect = PolySelect.faces[
                            ShellFaces[x].index - MapWidth - MapWidth
                        ].select
                        YPosSelect = PolySelect.faces[
                            ShellFaces[x].index + MapWidth + MapWidth
                        ].select

                        if XNegSelect == True:
                            XDetect += 1
                        if XPosSelect == True:
                            XDetect += 1
                        if YNegSelect == True:
                            YDetect += 1
                        if YPosSelect == True:
                            YDetect += 1
                        if XDetect == 2 or YDetect == 2:
                            PolySelect.faces[ShellFaces[x].index].select = False

                    # Finally, every polygon singular polygon represents 1 cap tile. We can
                    # get the orientation by checking against the user's paint selection.

                    CapFaces = [v for v in PolySelect.faces if v.select]
                    bpy.ops.mesh.select_all(action="DESELECT")

                    CapTilesA = []
                    CapTilesB = []
                    CapTilesC = []
                    CapTilesD = []

                    # This segment collects, then sorts each Cap tile into 1 of 4 groups. Again, python is too slow to
                    # process tiles 1 by 1, so we have to prep everything for batch operations.
                    for x in range(0, len(CapFaces)):
                        bpy.ops.mesh.select_all(action="DESELECT")
                        CapFaces[x].select = True

                        # Select the original user inner selection.
                        for q in range(0, len(SelectedFaces)):
                            SelectedFaces[q].select = True

                        # We can detect the rotation of the Cap
                        # by checking neighboring Left right up and down polygons after
                        # select-linked fills the whole tile.
                        XPosDetect = 0
                        YPosDetect = 0
                        XNegDetect = 0
                        YNegDetect = 0

                        if PolySelect.faces[CapFaces[x].index + 1].select == True:
                            XPosDetect = 1
                        if PolySelect.faces[CapFaces[x].index - 1].select == True:
                            XNegDetect = 1
                        if (
                            PolySelect.faces[CapFaces[x].index + MapWidth].select
                            == True
                        ):
                            YPosDetect = 1
                        if (
                            PolySelect.faces[CapFaces[x].index - MapWidth].select
                            == True
                        ):
                            YNegDetect = 1

                        # Detect and sort the direction of the tiles into 4 groups.

                        # Caps are slightly simpler as far as detecting which direction they are facing...
                        if XPosDetect == 1:
                            CapTilesA.append(CapFaces[x])
                        if XNegDetect == 1:
                            CapTilesB.append(CapFaces[x])
                        if YPosDetect == 1:
                            CapTilesC.append(CapFaces[x])
                        if YNegDetect == 1:
                            CapTilesD.append(CapFaces[x])

                    # Unwrap template corner tiles meant for copy pasting to other tiles.
                    def UnwrapCapTiles(CornerTileList):

                        bpy.ops.uv.select_all(action="DESELECT")
                        bpy.ops.mesh.select_all(action="DESELECT")
                        CornerTileList[0].select = True
                        bpy.ops.mesh.select_linked(delimit={"SEAM"})
                        bpy.ops.uv.select_all(action="SELECT")
                        bpy.ops.uv.smart_project(
                            island_margin=0.001,
                            correct_aspect=False,
                            scale_to_bounds=True,
                        )

                    if len(CapTilesA) != 0:
                        UnwrapCapTiles(CapTilesA)
                    if len(CapTilesB) != 0:
                        UnwrapCapTiles(CapTilesB)
                    if len(CapTilesC) != 0:
                        UnwrapCapTiles(CapTilesC)
                    if len(CapTilesD) != 0:
                        UnwrapCapTiles(CapTilesD)

                    # Desperate for performance, we multi-select the tiles prior
                    # to running a UV alignment on them because the operation is 4x
                    # slower if its used on each tile individually. While there are
                    # faster methods, those methods require packing. Packing
                    # crashes Blender when called from a python script. This makes me sad.
                    if len(CapTilesA) != 0:
                        CapTilesA[0].select = True
                    if len(CapTilesB) != 0:
                        CapTilesB[0].select = True
                    if len(CapTilesC) != 0:
                        CapTilesC[0].select = True
                    if len(CapTilesD) != 0:
                        CapTilesD[0].select = True
                    bpy.ops.mesh.select_linked(delimit={"SEAM"})
                    bpy.ops.uv.select_all(action="SELECT")
                    bpy.ops.uv.align_rotation(
                        method="GEOMETRY", axis="X"
                    )  # Extremely inefficient, but the only option.

                    # Now we can finally process the Cap tiles. A single tile is reset, unwrapped
                    # then rotated, then the result is copied onto all of the other tiles.
                    def ApplyCapTiles(CapTileList, TargetCapTile, RotationValue):
                        bpy.ops.mesh.select_all(action="DESELECT")
                        for z in range(0, len(CapTileList)):
                            # Process first tile.
                            if z == 0:
                                CapTileList[z].select = True
                                bpy.ops.mesh.select_linked(delimit={"SEAM"})
                                bpy.ops.uv.select_all(action="SELECT")

                                # Although this operation is really slow, it guarantees the rotation of the tiles will
                                # always be the same start orientation.
                                bpy.ops.uv.align_rotation(method="GEOMETRY", axis="X")

                                # Rotate tile
                                bpy.ops.transform.rotate(
                                    value=RotationValue,
                                    orient_axis="Z",
                                    orient_type="VIEW",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="VIEW",
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=True,
                                    use_snap_edit=True,
                                    use_snap_nonedit=True,
                                    use_snap_selectable=False,
                                )

                                # Scale uvs to match size of tile.
                                bpy.ops.transform.resize(
                                    value=(
                                        float(TargetCapTile[4]),
                                        float(TargetCapTile[4]),
                                        float(TargetCapTile[4]),
                                    ),
                                    orient_type="GLOBAL",
                                    orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                    orient_matrix_type="GLOBAL",
                                    constraint_axis=(True, True, True),
                                    mirror=False,
                                    use_proportional_edit=False,
                                    proportional_edit_falloff="SMOOTH",
                                    proportional_size=1,
                                    use_proportional_connected=False,
                                    use_proportional_projected=False,
                                    snap=False,
                                    snap_elements={"INCREMENT"},
                                    use_snap_project=False,
                                    snap_target="CENTER",
                                    use_snap_self=True,
                                    use_snap_edit=True,
                                    use_snap_nonedit=True,
                                    use_snap_selectable=False,
                                    release_confirm=True,
                                )

                                # Place tile
                                bpy.ops.uv.snap_cursor(target="ORIGIN")
                                bpy.ops.uv.snap_selected(target="CURSOR_OFFSET")
                                bpy.ops.transform.translate(
                                    value=(
                                        (float(TargetCapTile[4]) / 2)
                                        + float(TargetCapTile[2]),
                                        (float(TargetCapTile[4]) / 2)
                                        + 1
                                        - (float(TargetCapTile[3]))
                                        - float(TargetCapTile[4]),
                                        0,
                                    )
                                )
                                bpy.ops.uv.copy()
                            else:
                                CapTileList[z].select = True

                        bpy.ops.mesh.select_linked(delimit={"SEAM"})
                        bpy.ops.uv.select_all(action="SELECT")
                        bpy.ops.uv.smart_project(
                            island_margin=0.001, correct_aspect=False
                        )
                        bpy.ops.uv.paste()

                    bpy.ops.uv.select_all(action="DESELECT")
                    bpy.ops.mesh.select_all(action="DESELECT")
                    ApplyCapTiles(CapTilesA, TargetCapTile, 1.56426)
                    ApplyCapTiles(CapTilesB, TargetCapTile, 4.71239)
                    ApplyCapTiles(CapTilesC, TargetCapTile, -3.14159)
                    ApplyCapTiles(CapTilesD, TargetCapTile, 0)

        bpy.context.area.ui_type = "VIEW_3D"
        bpy.ops.object.mode_set(mode="OBJECT")

        # Select user terrain
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = UserTerrain
        bpy.data.objects[UserTerrain.name].select_set(True)

        # The user terrain must be almost completely flat to avoid trashy
        # uv maps when unwrapping. We change this back at the end of the operation.
        bpy.context.object.scale[2] = 1000
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

        # If the context changed for any reason, change it back.
        bpy.context.area.ui_type = OriginalContext

        if UIWeightSwitch == True:
            bpy.ops.paint.weight_paint_toggle()

        return {"FINISHED"}


####################################################
####################################################
####################################################
####           EXPORT TEXTURES BUTTON          #####
####################################################
####################################################
####################################################


class bzbutton_exportmat(bpy.types.Operator):
    bl_idname = "button.bzexportmat"
    bl_label = "Export Textures (.mat)"
    bl_description = "Saves a .MAT file for the current map."

    def execute(self, context):

        RandomizeSolidRotationCheckbox = (
            context.scene.BZMapIO_Toggles.RandomizeSolidRotation
        )

        # Get user terrain object
        bpy.ops.object.mode_set(mode="OBJECT")
        for ob in bpy.data.objects:
            if ".hg2_" in ob.name.lower():
                UserTerrain = ob
                break

        # Collect all TileSelector objects
        TileSelector_Tiles = []
        for ob in bpy.data.objects:
            if "tileselector_tile" in ob.name.lower():
                TileSelector_Tiles.append(ob.name.split(":"))

        # Select user terrain
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = UserTerrain
        bpy.data.objects[UserTerrain.name].select_set(True)

        #  Instead of providing a means to acquire the uv coordinates of the user's geometry selection
        # easily like literally every other (better) 3d application, we instead have to use a big,
        # steaming pile of crap code and look through the entire data set every single time we want
        # to get information. At the very least, I can read through all polygons sequentially but
        # its still total unrelenting cancer knowing this.

        # Gets every vertex of every face... there's a lot of em!
        ob = bpy.context.active_object
        AllUVData = []
        # Loops per face
        for face in ob.data.polygons:
            for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
                uv_coords = ob.data.uv_layers.active.data[loop_idx].uv
                AllUVData.append([face.index, uv_coords.x, uv_coords.y])

        # We use this modified UV data list because I only need
        # 1 vertex from each polygon, and I don't care which vertex it is.
        UVData = []
        # UVData[x][0] = Polygon Index
        # UVData[x][1] = UV vertex coord X
        # UVData[x][2] = UV vertex coord Y

        UVCounter = 0
        UVStoreX = []
        UVStoreY = []

        # We want the average value of all UV vertex coordinates which will give us
        # the center of every polygon. If we didn't do this, it would be impossible
        # to tell which tile is which.
        # for x in range(0, len(AllUVData)):
        # 	if AllUVData[x][0] == UVCounter:
        # 		UVData.append([AllUVData[x][0],AllUVData[x][1] ,AllUVData[x][2]])
        # 		UVCounter += 1

        # We only care about the averaged center of each polygon's UV vertex coordinates.
        # this is neccecary otherwise coordinates would overlap and make it impossible
        # to tell which tile is which.

        for x in range(0, len(AllUVData)):
            if AllUVData[x][0] == UVCounter:
                UVStoreX.append(AllUVData[x][1])
                UVStoreY.append(AllUVData[x][2])
            if AllUVData[x][0] != UVCounter:
                UVData.append(
                    [
                        UVCounter,
                        sum(UVStoreX) / len(UVStoreX),
                        sum(UVStoreY) / len(UVStoreY),
                    ]
                )
                UVStoreX = []
                UVStoreY = []
                UVCounter += 1

        # Blender doesn't provide the user with an obvious way to completely erase
        # weight maps (it leaves values of "0" everywhere), so we clean up before operating.
        # to prevent large computations from happening unneccecarily.
        bpy.ops.object.vertex_group_clean(limit=0.1)

        MapWidth = (
            bpy.data.node_groups["Geometry Nodes"].nodes["Grid"].inputs[3].default_value
            - 1
        )

        # Go into edit mode, select polygons.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.area.ui_type = "UV"
        bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")
        bpy.ops.mesh.select_all(action="DESELECT")

        PolySelect = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        PolySelect.faces.ensure_lookup_table()
        PolySelect.verts.ensure_lookup_table()

        # Select every lower-left polygon on every 4x4 tile
        # and gather information about it. We need to know:

        # 1) Tile Texture
        # 2) Tile Rotation
        # 3) Tile Mirror

        # NOTE: as of this writing Sept 3rd 2023), the upper/right outer
        # edges of the map texture grid are truncated by 1 polygon due to an oversight on my part. Sorry!
        MapRow = 1
        MapTileOutput = []
        output = b""
        for x in range(0, len(PolySelect.faces)):
            if x % 4 == True and MapRow < MapWidth:
                PolySelect.faces[x - 1].select = True

                UVDist = 9999  # Needed to detect what the operating tile is.
                OperatingTile = TileSelector_Tiles[
                    0
                ]  # fallback value if nothing is found.
                for q in range(0, len(TileSelector_Tiles)):

                    # We add the [q][4] bit is to make evaluation happen from
                    # the center of the tile not the corner of it because corners overlap.
                    UVTileX = round(
                        float(TileSelector_Tiles[q][2])
                        + (float(TileSelector_Tiles[q][4]) / 2),
                        3,
                    )
                    UVTileY = round(
                        float(TileSelector_Tiles[q][3])
                        + (float(TileSelector_Tiles[q][4]) / 2),
                        3,
                    )

                    UserUVTileX = round(float(UVData[x][1]), 3)
                    UserUVTileY = round(float(1 - UVData[x][2]), 3)

                    # NOTE: Active selection is LOWER LEFT CORNER of all tiles.

                    # nextTile is the polygon directly in front of active selection
                    UserUVnextTileX = round(float(UVData[x + 1][1]), 3)
                    UserUVnextTileY = round(float(1 - UVData[x + 1][2]), 3)

                    # RowTile is the polygon directly above active selection.
                    UserUVRowTileX = round(float(UVData[x + MapWidth][1]), 3)
                    UserUVRowTileY = round(float(1 - UVData[x + MapWidth][2]), 3)

                    # This is needed to detect which texture tile was used.
                    UVDistOpX = math.dist([UVTileX], [UserUVTileX])
                    UVDistOpY = math.dist([UVTileY], [UserUVTileY])
                    UVDistOpXY = UVDistOpX + UVDistOpY

                    if UVDistOpXY < UVDist:
                        UVDist = UVDistOpXY
                        OperatingTile = TileSelector_Tiles[q][1]

                IsAlignedUp = True
                RotationIndex = 999
                # We must determine whether or
                # not UVTile is X or Y aligned. The comparisons we do
                # must be split because some of them overlap depending on alignment.
                if UserUVTileY == UserUVRowTileY:
                    IsAlignedUp = False

                # There are 8 possible configuations for tile rotation:
                # 4 standard rotations
                # 4 mirrored rotations

                # X-ALIGNED ROTATIONS
                if IsAlignedUp == False:
                    # Standard
                    if UserUVTileX < UserUVRowTileX and UserUVTileY < UserUVnextTileY:
                        RotationIndex = 1
                    if UserUVTileX > UserUVRowTileX and UserUVTileY > UserUVnextTileY:
                        RotationIndex = 3
                    # Mirrored
                    if UserUVTileX < UserUVRowTileX and UserUVTileY > UserUVnextTileY:
                        RotationIndex = 7
                    if UserUVTileX > UserUVRowTileX and UserUVTileY < UserUVnextTileY:
                        RotationIndex = 5

                # Y-ALIGNED ROTATIONS
                if IsAlignedUp == True:
                    # Standard
                    if UserUVTileX < UserUVnextTileX and UserUVTileY > UserUVRowTileY:
                        RotationIndex = 0
                    if UserUVTileX > UserUVnextTileX and UserUVTileY < UserUVRowTileY:
                        RotationIndex = 2
                    # Mirrored
                    if UserUVTileX > UserUVnextTileX and UserUVTileY > UserUVRowTileY:
                        RotationIndex = 4
                    if UserUVTileX < UserUVnextTileX and UserUVTileY < UserUVRowTileY:
                        RotationIndex = 6

                # print("_________________________________________")
                # print("--UserUVTileX-->>>" + str(UserUVTileX))
                # print("--UserUVRowTileX-->>>" + str(UserUVRowTileX))
                # print("--UserUVTileY-->>>" + str(UserUVTileY))
                # print("--UserUVnextTileY-->>>" + str(UserUVnextTileY))
                # print(str(RotationIndex) + "   " + str(IsAlignedUp))
                # print("_________________________________________")

                # At this point, we have all the critical information needed to apply a tile:
                # 1) OperatingTile[0] is Texture
                # 2) OperatingTile[1] is Texture
                # 3) OperatingTile[2] is Tile Type
                # 4) OperatingTile[3] is the Tile Variant
                # EXAMPLE: "00SA" is tile 00, a solid, and variant A
                # EXAMPLE: "05CB" is tile 05, a cap, and variant B

                #  0                                1                         2                    3
                # /\ Rotation + Corner info        /\  Variant                /\   Tile           /\  Tile-Transition-to (IE: "05" would be grass to sand on achilles)

                CapCornerID = RotationIndex
                VariantTable = [
                    ["A", 0],
                    ["B", 1],
                    ["C", 2],
                    ["D", 3],
                    ["E", 4],
                    ["F", 5],
                    ["G", 6],
                    ["H", 7],
                    ["I", 8],
                ]
                Variant = 0
                for s in range(0, len(VariantTable)):
                    if OperatingTile[3] == VariantTable[s][0]:
                        Variant = VariantTable[s][1]

                # If its a diagonal tile, we need to increase the rotation index by 8
                # so the game identifies it as a diagonal.
                if OperatingTile[2] == "D":
                    CapCornerID = RotationIndex + 8

                    # Because corners are different when mirrored,
                    # we need to swap around some numbers to make
                    # it look correct in the game.
                    if CapCornerID == 12:
                        CapCornerID = 15
                    elif CapCornerID == 13:
                        CapCornerID = 12
                    elif CapCornerID == 14:
                        CapCornerID = 13
                    elif CapCornerID == 15:
                        CapCornerID = 14

                # Default to all zeroes if the plugin can't figure out what the tile is.
                if RotationIndex != 999:

                    if (
                        RandomizeSolidRotationCheckbox == True
                        and OperatingTile[2].lower() == "s"
                    ):
                        TileAssembly = struct.pack(
                            ">BB",
                            random.randint(0, 7) << 4 | Variant,
                            int(OperatingTile[0]) << 4 | int(OperatingTile[1]),
                        )
                    else:
                        TileAssembly = struct.pack(
                            ">BB",
                            int(CapCornerID) << 4 | Variant,
                            int(OperatingTile[0]) << 4 | int(OperatingTile[1]),
                        )
                else:
                    TileAssembly = struct.pack(">BB", 0 << 4 | 0, 0 << 4 | 0)

                MapTileOutput.append(TileAssembly)

            MapRow += 1
            if MapRow > MapWidth * 4:
                MapRow = 1

        # Assemble the entire map's texturing.
        # Because the map tiles are collected sequentially left-to-right, we need
        # to structure the application so that it adheres to the zone quadrant setup
        # that the game expects.

        MapWidth = (
            int(bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string)
            * 1280
        )
        MapDepth = int(
            bpy.data.node_groups["Geometry Nodes"].nodes["String.003"].string
        )
        Counter = 0

        # We need 3 loops to pull this off. Using a 3840x3840 map as an example, it has 9 zones.
        # Loop 1) Q runs 3 times to cycle columns starting from bottom of map
        # Loop 2) T runs 3 times per Q loop, iterating through horizontal zones
        # Loop 3) R runs 12288 times (4096x3) to and iterate over every single tile within the zone row.
        #         in all zones in a row, isolating application of 1 zone in each pass.
        StartTile = 0
        QuadrantCount = 0
        QuadrantCheck = (64 * MapDepth) - 1

        for q in range(0, MapDepth):  # This controls the column to start on.
            for t in range(0, MapDepth):  # This is number of horizontal zones
                for r in range(
                    0, MapDepth * 4096
                ):  # This is number of horizontal tiles to process an entire row
                    if QuadrantCount <= 63:
                        output += MapTileOutput[r + StartTile]
                    if QuadrantCount >= QuadrantCheck:
                        QuadrantCount = -1
                    QuadrantCount += 1
                QuadrantCount = (64 * MapDepth - (64 * t)) - 64
            StartTile += MapDepth * 4096

        with open(context.scene.BZMapFile.lower().replace(".hg2", ".mat"), "wb") as fp:
            fp.write(output)

        # Return user to viewport.
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.area.ui_type = "VIEW_3D"
        self.report(
            {"INFO"},
            "BZMapIO: "
            + os.path.basename(bpy.context.scene.BZMapFile.replace(".hg2", ".mat"))
            + " textures saved/updated.",
        )

        return {"FINISHED"}


# All checkboxes and text fields go here
class BZMapIO_Toggles(PropertyGroup):
    ImportBZN: BoolProperty(
        name="Import Objects (.BZN)",
        description=" The BZN file contains objects such as scrap, tanks, spawnpoints, etc. Enables or disables IMPORT of these elements.",
        default=True,
    )

    ExportBZN: BoolProperty(
        name="Export Objects (.BZN)",
        description=" The BZN file contains objects such as scrap, tanks, spawnpoints, etc. Enables or disables export of these elements.",
        default=True,
    )

    RespawnTime: StringProperty(
        name="", description=" How many seconds before respawn?", default="20"
    )

    # TEXTURE TOOLS OPTIONS #

    RandomizeSolidRotation: BoolProperty(
        name="Randomize Solid Rotation on Export",
        description=" During export, randomly mirror and rotate solid tiles. You will need to reload the map textures to see changes made by this option.",
        default=True,
    )

    EnableSolids: BoolProperty(
        name="Apply Solid",
        description=" Enable/disable painting of solid tiles.",
        default=True,
    )

    EnableDiagonals: BoolProperty(
        name="Apply Diagonals",
        description=" Enable/disable painting of diagonal tiles.",
        default=True,
    )

    EnableCaps: BoolProperty(
        name="Apply Caps",
        description=" Enable/disable painting of cap tiles.",
        default=True,
    )


class BZMAPIO_OT_import_custom_world(Operator):
    bl_idname = "bzmapio.import_custom_world"
    bl_label = "Import Custom World"
    bl_description = "Load a custom world folder containing a .trn, *_detail_atlas.csv, material, and atlas texture"

    filepath: StringProperty(
        name="World Folder",
        subtype="DIR_PATH",
        default="",
    )

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = getattr(context.scene, "BZCustomWorldFolder", "")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        world_folder = self.filepath
        if world_folder and os.path.isfile(world_folder):
            world_folder = os.path.dirname(world_folder)

        try:
            definition = _load_custom_world_definition(world_folder)
        except ValueError as exc:
            self.report({"ERROR"}, f"BZMapIO: {exc}")
            return {"CANCELLED"}

        context.scene.BZCustomWorldFolder = definition["folder"]
        context.scene.BZCustomWorldAtlasCSV = definition["csv_path"]
        context.scene.BZCustomWorldMaterial = definition["material_name"]
        context.scene.BZCustomWorldTexture = definition["texture_path"]
        context.scene.BZCustomWorldStatus = (
            f"{definition['material_name']} - {len(definition['csv_data'])} tiles"
        )

        try:
            bpy.data.node_groups["Geometry Nodes"].nodes["String.005"].string = (
                definition["material_name"]
            )
        except KeyError:
            pass

        _ensure_custom_world_material(
            definition["material_name"], definition["texture_path"]
        )
        self.report(
            {"INFO"},
            f"BZMapIO: Imported custom world '{definition['material_name']}' with {len(definition['csv_data'])} atlas tiles.",
        )
        return {"FINISHED"}


######################################################################################################
######################################################################################################
#####  USER INTERFACE ################################################################################
######################################################################################################
######################################################################################################
class BZMAPIO_OT_open_template(bpy.types.Operator):
    bl_idname = "bzmapio.open_template"
    bl_label = "Open Map Template"
    bl_description = (
        "Open the bundled BZMapIO template scene required by the Battlezone map tools"
    )

    def execute(self, context):
        if not MAP_TEMPLATE_PATH.exists():
            self.report({"ERROR"}, f"Map template not found: {MAP_TEMPLATE_PATH}")
            return {"CANCELLED"}
        bpy.ops.wm.open_mainfile(filepath=str(MAP_TEMPLATE_PATH))
        return {"FINISHED"}


mystr: StringProperty(name="Enter something:")


class BZMAPIO_PT_map_import(Panel):
    bl_label = "Map Terrain"
    bl_idname = "BZMAPIO_PT_map_import"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"

    @classmethod
    def poll(cls, context):
        return context.mode in {"OBJECT", "EDIT_MESH", "SCULPT"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        template_box = layout.box()
        template_box.label(text="Template")
        template_box.operator("bzmapio.open_template", icon="FILE_BLEND")
        if not MAP_TEMPLATE_PATH.exists():
            template_box.label(text="Bundled map template is missing.", icon="ERROR")

        file_box = layout.box()
        file_box.label(text="Terrain File")
        file_box.prop(scene, "BZMapFile", text="File")
        file_box.operator("bzmapimport.data", icon="IMPORT")
        row = file_box.row()
        row.prop(scene.BZMapIO_Toggles, "ImportBZN")
        row.prop(scene.BZMapIO_Toggles, "ExportBZN")
        file_box.operator("button.bzmapexport", icon="OUTPUT")

        size_box = layout.box()
        size_box.label(text="Terrain Size")
        row = size_box.row()
        row.operator("button.bzsizeup")
        row.operator("button.bzsizedn")


class BZMAPIO_PT_map_object_tools(Panel):
    bl_label = "Map Object Tools"
    bl_idname = "BZMAPIO_PT_map_object_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"

    @classmethod
    def poll(cls, context):
        return context.mode in {"OBJECT", "EDIT_MESH", "SCULPT"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        workspace_box = layout.box()
        workspace_box.label(text="Workspaces")
        row = workspace_box.row()
        row.scale_y = 1.5
        row.operator("button.bzgosculpt")
        row.operator("button.bzgopaint")

        selection_box = layout.box()
        selection_box.label(text="Selection Tools")
        row = selection_box.row()
        row.scale_y = 1.5
        row.operator("button.bzshrinkwrap", icon="MOD_SHRINKWRAP")
        row.operator("button.bzinvertshrinkwrap", icon="PROP_CON")
        row = selection_box.row()
        row.operator("button.bzpaintshrinkwrap", icon="OVERLAY")
        row.operator("button.bzshrinkwrapbake", icon="MOD_SHRINKWRAP")
        selection_box.operator("button.bztransform", icon="FACESEL")

        respawn_box = layout.box()
        respawn_box.label(text="Respawning")
        respawn_box.label(text="Multiplayer only.", icon="INFO")
        respawn_box.operator("button.bzsetrespawning", icon="PLUS")
        respawn_box.prop(scene.BZMapIO_Toggles, "RespawnTime")


class BZMAPIO_PT_texture_tools(Panel):
    bl_label = "Map Texture Painting"
    bl_idname = "BZMAPIO_PT_texture_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Battlezone"
    # bl_context = "objectmode"

    @classmethod
    def poll(cls, context):
        return context.mode in {"OBJECT", "EDIT_MESH", "SCULPT"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        file_box = layout.box()
        file_box.label(text="Texture File")
        file_box.operator("bzmapio.import_custom_world", icon="FILE_FOLDER")
        if scene.BZCustomWorldStatus:
            file_box.label(text=scene.BZCustomWorldStatus, icon="INFO")
        file_box.operator("button.bzloadtextures")
        file_box.operator("button.bzexportmat")

        brush_box = layout.box()
        brush_box.label(text="Tile Brush")
        row = brush_box.row()
        row.operator("button.bzsettile1a", icon="SNAP_FACE")
        row.operator("button.bzsettile1b", icon="IMAGE_ALPHA")
        row.operator("button.bzsettile1c", icon="NOCURVE")
        row = brush_box.row()
        row.prop(scene.BZMapIO_Toggles, "EnableSolids")
        row.prop(scene.BZMapIO_Toggles, "EnableDiagonals")
        row.prop(scene.BZMapIO_Toggles, "EnableCaps")
        brush_box.prop(scene.BZMapIO_Toggles, "RandomizeSolidRotation")

        paint_box = layout.box()
        paint_box.label(text="Apply")
        paint_box.operator("button.bzapplytilepaint")
        paint_box.operator("button.bzclearpaint")


CLASSES = (
    bzmapimport,
    bzmapexport,
    bzbutton_transform,
    bzbutton_mapsizeup,
    bzbutton_mapsizedn,
    bzshrinkwrap,
    bzshrinkwrapinvert,
    bzshrinkwrapbake,
    bzpaintshrinkwrap,
    bzgosculpt,
    bzgopaint,
    bzbutton_setrespawning,
    bzbutton_loadtextures,
    bzbutton_settile1a,
    bzbutton_settile1b,
    bzbutton_settile1c,
    bzbutton_clearpaint,
    bzbutton_applytilepaint,
    bzbutton_exportmat,
    BZMapIO_Toggles,
    BZMAPIO_OT_import_custom_world,
    BZMAPIO_OT_open_template,
    BZMAPIO_PT_map_import,
    BZMAPIO_PT_map_object_tools,
    BZMAPIO_PT_texture_tools,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.BZMapFile = StringProperty(name="")
    bpy.types.Scene.BZCustomWorldFolder = StringProperty(
        name="Custom World Folder", subtype="DIR_PATH"
    )
    bpy.types.Scene.BZCustomWorldAtlasCSV = StringProperty(
        name="Custom World Atlas CSV", subtype="FILE_PATH"
    )
    bpy.types.Scene.BZCustomWorldMaterial = StringProperty(name="Custom World Material")
    bpy.types.Scene.BZCustomWorldTexture = StringProperty(
        name="Custom World Atlas Texture", subtype="FILE_PATH"
    )
    bpy.types.Scene.BZCustomWorldStatus = StringProperty(name="Custom World Status")
    bpy.types.Scene.BZMapIO_Toggles = PointerProperty(type=BZMapIO_Toggles)


def unregister():
    for attr in (
        "BZMapIO_Toggles",
        "BZMapFile",
        "BZCustomWorldFolder",
        "BZCustomWorldAtlasCSV",
        "BZCustomWorldMaterial",
        "BZCustomWorldTexture",
        "BZCustomWorldStatus",
    ):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
