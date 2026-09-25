from typing import Any

# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 “GrizzlyOne95” and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

import os
import sys
import math
import io
from enum import Enum
from collections import namedtuple
import traceback
import numpy as np
import itertools
from .utils import remap_range_normal

from pathlib import Path

# Shared palette cache: keys are ("act", act_path) or ("geo", map_dir)
_color_palette_cache = {}


import struct
from pathlib import Path

# Optional Pillow dependency: only needed for writing DDS textures
try:
    from PIL import Image

    HAVE_PIL = True
except ImportError:
    Image = None
    HAVE_PIL = False

from .spacial import (
    UV,
    Color3,
    Vector3,
    Quaternion,
    Transform,
)

from .exceptions import (
    ModelPorterError,
    AnimationAlreadyLoadedError,
    InvalidBoneTypeError,
    MaterialConflictError,
    HierarchyError,
    UnsupportedFileTypeError,
    InvalidSettingError,
    POVNotFoundError,
)

# These are all sibling modules in bzrmodelporter/
from .bzgeo import Geo
from .bzgeo_serializer import GeoSerializer
from .bzbwd2 import (
    VDF,
    SDF,
    VGEO,
    SGEO,
    ClassID,
)

from .bzbwd2_serializer import VDFSerializer, SDFSerializer
from ..bzmap import (
    BZMap,
    BZMapFormat,
)
from ..bzmap_serializer import BZMapSerializer
from ..bzact_serializer import BZActSerializer

from .ogremesh import (
    Mesh,
    VET,
    VES,
)
from .ogremesh_serializer import MeshSerializer
from .ogreskeleton import Skeleton
from .ogreskeleton_serializer import SkeletonSerializer

INF = float("inf")


class NormalMode(Enum):
    CORRECT = 0
    NONE = 1
    FLIP = 2


# "cvartl" and "cvturr" are deliberately missing.
# These specific strings can be found in the .exe if you search for text.
# All other names use the "_cockpit" suffix instead.
cockpit_suffix_map = {
    "avartl": "_c",
    "bvartl": "_c",
    "svartl": "_c",
    "avturr": "_c",
    "bvturr": "_c",
    "svturr": "_c",
    "avwalk": "_c",
    "bvwalk": "_c",
    "cvwalk": "_c",
    "svwalk": "_c",
    "aspilo": "_fp",
    "bspilo": "_fp",
    "cspilo": "_fp",
    "sspilo": "_fp",
    "bsheav": "_fp",
}

MaterialInfo = namedtuple("MaterialInfo", "name super_name tex_name")


class InterModel:
    """Intermediary model."""

    settings = None
    name = None
    material_filename = None
    use_cockpit = False  # Will this model use a separate cockpit mesh/skeleton?
    # igeom_list = None
    # ibone_list = None
    # iobject_list = None
    # igroup_map = None  # Maps a submesh name onto a group of geometry.
    #   All rendering geometry should be in exactly one of these groups.
    # mat_map = None  # Dict mapping material names to material info to be written to file.
    #   Stock materials that shouldn't be written to file don't go here.

    is_skeletally_animated = False
    # ianim_list = None
    # ianim_index_map = None
    # ianim_loaded_list = None
    # ianim_name_map = None
    is_pilot = False
    use_scope = False
    scope_info = None

    use_flat_colors = False
    flat_name = None
    # flat_igeom_list = None

    def __init__(self, settings):
        self.settings = settings
        self.igeom_list = []
        self.ibone_list = []
        self.iobject_list = []
        self.igroup_map = {}
        self.mat_map = {}

        self.headlight_mask_list = []
        self.eyepoint_list = []
        self.hardpoint_list = []

        self.ianim_list = []
        self.ianim_index_map = {}
        self.ianim_loaded_list = []
        self.ianim_name_map = {}

        self.flat_igeom_list = []

    def create_igeom(self, name):
        igeom = InterGeometry(name)
        self.igeom_list.append(igeom)
        return igeom

    def create_igeom_from_geo(self, name, geo):
        igeom = self.create_igeom(name)
        igeom.add_geo_faces(geo)
        igeom.apply_geometry()

        normal_mode = getattr(self.settings, "normal_mode", NormalMode.CORRECT)
        if normal_mode == NormalMode.CORRECT:
            igeom.correct_normals()
        elif normal_mode == NormalMode.FLIP:
            igeom.vertex_array["normal"] *= -1

        # igeom.restore_normals()
        # igeom.normalize_normals()
        return igeom

    def iobject_igeoms(self):
        return itertools.chain(
            (
                ("primary", iobject, iobject.geometry_primary)
                for iobject in self.iobject_list
                if iobject.geometry_primary is not None
            ),
            (
                ("cockpit", iobject, iobject.geometry_cockpit)
                for iobject in self.iobject_list
                if iobject.geometry_cockpit is not None
            ),
        )

    def create_ibone(self, name="", type="primary", is_required=True):
        ibone = InterBone(len(self.ibone_list), name, type, is_required)
        self.ibone_list.append(ibone)
        return ibone

    def create_iobject(self, name, class_id, transform, parent):
        iobject = InterObject()
        iobject.name = name
        iobject.set_class_id(class_id)
        iobject.transform = transform
        iobject.parent_name = parent
        self.add_iobject(iobject)
        return iobject

    def add_iobject(self, iobject):
        self.iobject_list.append(iobject)

        if iobject.is_headlight_mask:
            self.headlight_mask_list.append(iobject)
        if iobject.is_eyepoint:
            self.eyepoint_list.append(iobject)
        if iobject.is_hardpoint:
            self.hardpoint_list.append(iobject)

    def process_animation(self, ianim):
        # forward
        # start_frame
        # framecount
        # index
        # ibone_tracks

        forward = ianim.forward
        backward = not forward
        dir = 1 if forward else -1
        if ianim.framecount > 0:
            end_frame = ianim.start_frame + (ianim.framecount - 1) * dir
        else:
            end_frame = ianim.start_frame

        # print("\n________________________")
        # print(ianim.index, ianim.start_frame, end_frame, ianim.forward, ianim.framecount)

        if ianim.framecount == 0:
            return

        # Slice iobject single-timeline track into individual animation tracks
        translation_slices = {}
        rotation_slices = {}

        for iobject in self.iobject_list:
            # print(f"  {iobject.name} ({len(iobject.anim_translations)}, {len(iobject.anim_rotations)})")
            # # # # # # # # # # #
            # # Translations  # #
            track = []

            # Initialize tkf_front to be immediately in front of (or equal to) start_frame
            #   and tkf_back to be immediately behind start_frame.
            try:
                tkf_front = iobject.anim_translations[0 if forward else -1]
            except IndexError:
                tkf_front = None
            tkf_back = None
            while tkf_front is not None and not lte(
                ianim.start_frame, tkf_front.frame, forward
            ):
                tkf_back = tkf_front
                tkf_front = tkf_front.move(forward)

            # Create an initial keyframe if one doesn't already exist at start_frame
            if tkf_front is None:
                if tkf_back is None:  # No animation data at all - append 0 translation
                    track.append(
                        InterObjectTranslationKeyframe(ianim.start_frame, Vector3())
                    )
                else:  # Start frame is OoB forward - append back-keyframe translation
                    track.append(
                        InterObjectTranslationKeyframe(
                            ianim.start_frame, tkf_back.translation
                        )
                    )
            elif tkf_front.frame != ianim.start_frame:
                if (
                    tkf_back is None
                ):  # Start frame is OoB backward - append front-keyframe translation
                    track.append(
                        InterObjectTranslationKeyframe(
                            ianim.start_frame, tkf_front.translation
                        )
                    )
                else:  # Start frame is between keyframes - append interpolated translation
                    time_ratio = remap_range_normal(
                        tkf_back.frame, tkf_front.frame, ianim.start_frame
                    )
                    translation = Vector3.lerp(
                        tkf_back.translation, tkf_front.translation, time_ratio
                    )
                    track.append(
                        InterObjectTranslationKeyframe(ianim.start_frame, translation)
                    )
            # else:
            # print("skip A")
            # Otherwise start frame is front-keyframe and is about to be pushed

            # Push all keyframes within this animation's section of the timeline
            while tkf_front is not None and lte(tkf_front.frame, end_frame, forward):
                track.append(
                    InterObjectTranslationKeyframe(
                        tkf_front.frame, tkf_front.translation
                    )
                )
                tkf_back = tkf_front
                tkf_front = tkf_front.move(forward)

            # Create a terminating keyframe if one doesn't already exist at end_frame
            if tkf_back is None:
                if tkf_front is None:  # No animation data at all - append 0 translation
                    track.append(InterObjectTranslationKeyframe(end_frame, Vector3()))
                else:  # End frame is OoB backward - append front-keyframe translation
                    track.append(
                        InterObjectTranslationKeyframe(end_frame, tkf_front.translation)
                    )
            elif tkf_back.frame != end_frame:
                if (
                    tkf_front is None
                ):  # End frame is OoB forward - append back-keyframe translation
                    track.append(
                        InterObjectTranslationKeyframe(end_frame, tkf_back.translation)
                    )
                else:  # End frame is between keyframes - append interpolated translation
                    time_ratio = remap_range_normal(
                        tkf_back.frame, tkf_front.frame, end_frame
                    )
                    translation = Vector3.lerp(
                        tkf_back.translation, tkf_front.translation, time_ratio
                    )
                    track.append(InterObjectTranslationKeyframe(end_frame, translation))
            # else:
            # print("skip B")
            # Otherwise end frame is back-keyframe and was already pushed

            prev_tkf = None
            for tkf in track:
                tkf.frame -= (
                    ianim.start_frame
                )  # Adjust frame numbers so start of animation is 0
                tkf.frame *= (
                    dir  # and frame numbers increase with the keyframes' indices
                )
                if prev_tkf is not None:  # and link up adjacent keyframes.
                    prev_tkf.next = tkf
                    tkf.prev = prev_tkf
                prev_tkf = tkf

            translation_slices[iobject] = track
            # print(f"    {len(track)}")

            # # # # # # # # #
            # # Rotations # #
            track = []

            if (
                iobject.is_eyepoint
                and self.settings.pov_movement_anim_rotations_disabled()
                and ianim.index
                in {
                    PERSON_ANIM_RUN_FORWARD,
                    PERSON_ANIM_RUN_BACKWARD,
                    PERSON_ANIM_STRAFE_LEFT,
                    PERSON_ANIM_STRAFE_RIGHT,
                }
            ):
                rotation_slices[iobject] = track
                continue

            # Initialize rkf_front to be immediately in front of (or equal to) start_frame
            #   and rkf_back to be immediately behind start_frame.
            try:
                rkf_front = iobject.anim_rotations[0 if forward else -1]
            except IndexError:
                rkf_front = None
            rkf_back = None
            while rkf_front is not None and not lte(
                ianim.start_frame, rkf_front.frame, forward
            ):
                rkf_back = rkf_front
                rkf_front = rkf_front.move(forward)

            # Create an initial keyframe if one doesn't already exist at start_frame
            if rkf_front is None:
                if rkf_back is None:  # No animation data at all - append 0 rotation
                    track.append(
                        InterObjectRotationKeyframe(ianim.start_frame, Quaternion())
                    )
                else:  # Start frame is OoB forward - append back-keyframe rotation
                    track.append(
                        InterObjectRotationKeyframe(
                            ianim.start_frame, rkf_back.rotation
                        )
                    )
            elif rkf_front.frame != ianim.start_frame:
                if (
                    rkf_back is None
                ):  # Start frame is OoB backward - append front-keyframe rotation
                    track.append(
                        InterObjectRotationKeyframe(
                            ianim.start_frame, rkf_front.rotation
                        )
                    )
                else:  # Start frame is between keyframes - append interpolated rotation
                    time_ratio = remap_range_normal(
                        rkf_back.frame, rkf_front.frame, ianim.start_frame
                    )
                    rotation = Quaternion.slerp(
                        rkf_back.rotation, rkf_front.rotation, time_ratio
                    )
                    track.append(
                        InterObjectRotationKeyframe(ianim.start_frame, rotation)
                    )
            # Otherwise start frame is front-keyframe and is about to be pushed

            # Push all keyframes within this animation's section of the timeline
            while rkf_front is not None and lte(rkf_front.frame, end_frame, forward):
                track.append(
                    InterObjectRotationKeyframe(rkf_front.frame, rkf_front.rotation)
                )
                rkf_back = rkf_front
                rkf_front = rkf_front.move(forward)

            # Create a terminating keyframe if one doesn't already exist at end_frame
            if rkf_back is None:
                if rkf_front is None:  # No animation data at all - append 0 rotation
                    track.append(InterObjectRotationKeyframe(end_frame, Quaternion()))
                else:  # End frame is OoB backward - append front-keyframe rotation
                    track.append(
                        InterObjectRotationKeyframe(end_frame, rkf_front.rotation)
                    )
            elif rkf_back.frame != end_frame:
                if (
                    rkf_front is None
                ):  # End frame is OoB forward - append back-keyframe rotation
                    track.append(
                        InterObjectRotationKeyframe(end_frame, rkf_back.rotation)
                    )
                else:  # End frame is between keyframes - append interpolated rotation
                    time_ratio = remap_range_normal(
                        rkf_back.frame, rkf_front.frame, end_frame
                    )
                    rotation = Quaternion.slerp(
                        rkf_back.rotation, rkf_front.rotation, time_ratio
                    )
                    track.append(InterObjectRotationKeyframe(end_frame, rotation))
            # Otherwise end frame is back-keyframe and was already pushed

            prev_rkf = None
            for rkf in track:
                rkf.frame -= (
                    ianim.start_frame
                )  # Adjust frame numbers so start of animation is 0
                rkf.frame *= (
                    dir  # and frame numbers increase with the keyframes' indices
                )
                if prev_rkf is not None:  # and link up adjacent keyframes.
                    prev_rkf.next = rkf
                    rkf.prev = prev_rkf
                prev_rkf = rkf

            rotation_slices[iobject] = track
            # print(f"    {len(track)}")
        # print("  ================")
        # Merge animation data (merge translation and rotation keyframes into one) and put into ibones
        for iobject in self.iobject_list:
            # print(f"  {iobject.name}")
            if iobject.no_bone_transform:
                continue
            ibone = iobject.bone_primary
            if ibone is None:
                continue

            track = InterAnimTrack()

            try:
                tkf = translation_slices[iobject][0]
            except (KeyError, IndexError):
                tkf = None
            try:
                rkf = rotation_slices[iobject][0]
            except (KeyError, IndexError):
                rkf = None

            while tkf is not None and rkf is not None:
                # print(tkf.frame, rkf.frame, not not tkf.prev, not not rkf.prev)
                if tkf.frame < rkf.frame:
                    time_ratio = remap_range_normal(
                        rkf.prev.frame, rkf.frame, tkf.frame
                    )
                    rotation = Quaternion.slerp(
                        rkf.prev.rotation, rkf.rotation, time_ratio
                    )
                    ikf = InterAnimKeyframe(tkf.frame, tkf.translation.copy(), rotation)
                    tkf = tkf.next
                elif rkf.frame < tkf.frame:
                    time_ratio = remap_range_normal(
                        tkf.prev.frame, tkf.frame, rkf.frame
                    )
                    translation = Vector3.lerp(
                        tkf.prev.translation, tkf.translation, time_ratio
                    )
                    ikf = InterAnimKeyframe(rkf.frame, translation, rkf.rotation.copy())
                    rkf = rkf.next
                else:
                    ikf = InterAnimKeyframe(
                        tkf.frame, tkf.translation.copy(), rkf.rotation.copy()
                    )
                    tkf = tkf.next
                    rkf = rkf.next
                track.append(ikf)

            # print()

            if iobject.anim_reverse_pitch:
                track.flip_pitch()

            ianim.ibone_tracks[ibone] = track

    def register_anims(self, bwd2):
        if bwd2.anim_obj is None:
            return

        for anim in bwd2.anim_obj.animations():
            ianim = InterAnim()
            ianim.index = (
                anim.index
            )  # BZ accesses the animation not by a name but by an index. This is not necessarily the same as the anim's index in the bwd2's animation array.
            ianim.start_frame = (
                anim.start
            )  # Where in the timeline this animation begins.
            ianim.signed_framecount = (
                anim.length
            )  # Signed number of frames in the animation. Sign dictates playback direction.
            ianim.framecount = abs(
                anim.length
            )  # Number of frames in the animation. Duration in frames is one less than this value.
            ianim.forward = anim.length >= 0  # Playback direction.
            ianim.speed = (
                anim.speed
            )  # Playback rate in frames per second. There is sub-frame interpolation.
            ianim.loop = anim.loop

            self.ianim_list.append(ianim)
            self.ianim_index_map[ianim.index] = ianim

    def process_animations(self):
        for ianim in self.ianim_list:
            self.process_animation(ianim)

    def load_empty_animation(self, name, play_duration=1.0):
        ianim = InterAnim()
        ianim.name = name
        ianim.play_duration = play_duration
        self.ianim_list.append(ianim)
        self.ianim_name_map[name] = ianim
        self.ianim_loaded_list.append(ianim)
        return ianim

    def load_animation_by_index(self, index, name, play_duration, anim_duration=None):
        if index not in self.ianim_index_map:
            self.load_empty_animation(name, play_duration)
            return
        ianim = self.ianim_index_map[index]
        if ianim.name is not None:
            raise AnimationAlreadyLoadedError(f"Animation {index} already loaded!")
        if anim_duration is None:
            anim_duration = play_duration
        ianim.name = name
        ianim.play_duration = play_duration
        ianim.anim_duration = anim_duration
        self.ianim_name_map[name] = ianim
        self.ianim_loaded_list.append(ianim)
        return ianim

    def link_iobject_geometry(self, iobject, igeom, type):
        if type == "primary":
            iobject.geometry_primary = igeom
        elif type == "cockpit":
            iobject.geometry_cockpit = igeom
        else:
            raise ValueError(f"intergeometry type '{type}' is not valid")
        igeom.iobject = iobject

    def link_iobject_ibone(self, iobject, ibone, type):
        if type == "primary":
            iobject.bone_primary = ibone
        elif type == "cockpit":
            iobject.bone_cockpit = ibone
        else:
            raise InvalidBoneTypeError(f"Bone type {type} is not allowed")
        ibone.iobject = iobject

    def get_iobject_by_name(self, name):
        name = name.lower()
        for iobject in self.iobject_list:
            if iobject.name.lower() == name:
                return iobject
        return None
        ibone.iobject = iobject

    def get_ibone_by_name(self, name):
        name = name.lower()
        for ibone in self.ibone_list:
            if ibone.name.lower() == name:
                return ibone
        return None

    def get_igeom_by_name(self, name):
        name = name.lower()
        for igeom in self.igeom_list:
            if igeom.name.lower() == name:
                return igeom
        return None

    def load_from_object(self, bwd2, obj, asset_resolver):
        if obj.name.lower() == "null":
            return

        # Build the intermediary object
        pr_obj = obj.get_primary(raw=True)
        iobject = self.get_iobject_by_name(pr_obj.name)
        if iobject is None:
            iobject = self.create_iobject(
                name=obj.name,
                class_id=pr_obj.class_id,
                transform=pr_obj.transform,
                parent=pr_obj.parent,
            )

            if self.is_skeletally_animated:
                # Normally the bone transform doesn't matter for hardpoint objects
                #   but it does for pilots. The transform offsets where the
                #   projectile shoots from.
                # This is true of the bone's animation as well.
                if iobject.is_hardpoint:
                    iobject.no_bone_transform = True

                if iobject.is_eyepoint:
                    iobject.anim_reverse_pitch = True

            # Take in animations iff this is an animated primary object.
            #   Cockpit animations have no effect anyway, as the cockpit
            #   model is always locked to the primary position.

            if (
                bwd2.anim_obj is not None
                and obj.is_primary()
                and bwd2.anim_obj.animation_mesh_exists(obj.name)
            ):
                anim_mesh = bwd2.anim_obj.get_animation_mesh(obj.name)
                iobject.create_keyframes_from_anim_mesh(anim_mesh)

        # Load this object's geo
        filepath = asset_resolver.get_geo_path(obj.name)
        geo = None
        if filepath is not None:
            try:
                with open(filepath, "rb") as stream:
                    _geo = GeoSerializer(stream).read()
            except OSError as e:
                print(f"Failed to read .geo file {filepath} for object {obj.name}")
            except Exception as e:
                print(f"Failed to read .geo file {filepath} for object {obj.name}")
                print(repr(e))
            else:
                print(f"Geo {obj.name} - {ClassID.label(obj.class_id)}")
                geo = _geo

        igeom = None
        if geo is not None and len(geo.face_list) > 0:
            # Build intermediary geometry
            if class_id_renderable(obj.class_id):
                igeom = self.create_igeom_from_geo(
                    name=obj.name,
                    geo=geo,
                )
                # Assign the geometry to the object appropriately
                if obj.lod == 0 and obj.rep == 0:
                    type = "primary"
                elif obj.lod == 1 and obj.rep == 0:
                    type = "cockpit"
                self.link_iobject_geometry(iobject, igeom, type)

        # Build intermediary bone
        ibone = self.create_ibone(name=obj.name)
        ibone.set_geometry(igeom)
        if obj.lod == 0 and obj.rep == 0:
            ibone.type = "primary"
        elif obj.lod == 1 and obj.rep == 0:
            ibone.type = "cockpit"
        self.link_iobject_ibone(iobject, ibone, ibone.type)

    def generate_abs_transforms(self):
        for iobject in self.iobject_list:
            iobject.generate_abs_transform()

    def process_geometry(self):
        for iobject in self.iobject_list:
            iobject.process_geometry()

    def init_iobject_hierarchy(self):
        iobjects_map = {}
        for iobject in self.iobject_list:
            if iobject.name is not None:
                iobjects_map[iobject.name.lower()] = iobject

        for iobject in self.iobject_list:
            if iobject.parent_name is not None:
                parent_name = iobject.parent_name.lower()
                if (
                    parent_name != "world"
                    and parent_name != "null"
                    and parent_name in iobjects_map
                ):
                    iobjects_map[parent_name].add_child(iobject)

    def init_ibone_hierarchy_from_iobjects(self):
        for iobject in self.iobject_list:
            if iobject.parent is not None:
                if iobject.bone_primary is not None:
                    if iobject.parent.bone_primary is not None:
                        iobject.parent.bone_primary.add_child(iobject.bone_primary)
            if self.use_cockpit and iobject.bone_cockpit is not None:
                iobject.bone_primary.add_child(iobject.bone_cockpit)

        if not self.use_cockpit:
            return

        for eyepoint in self.eyepoint_list:
            if eyepoint.parent is None:
                continue
            if eyepoint.parent.bone_cockpit is None:
                continue
            eyepoint.bone_primary.parent.remove_child(eyepoint.bone_primary)
            eyepoint.parent.bone_cockpit.add_child(eyepoint.bone_primary)

    def add_material(self, mat_info):
        if mat_info.name in self.mat_map:
            if mat_info == self.mat_map[mat_info.name]:
                return
            else:
                raise MaterialConflictError(
                    f"Different material already exists by same name {mat_info.name}: {mat_info}, {self.mat_map[mat_info.name]}"
                )
        else:
            self.mat_map[mat_info.name] = mat_info

    def create_material(self, name, super_name, tex_name):
        mat_info = MaterialInfo(
            name=self.name + "_" + name,
            super_name=super_name,
            tex_name=tex_name,
        )
        self.add_material(mat_info)
        return mat_info

    def group_exists(self, name):
        return name in self.igroup_map

    def get_igroup(self, name, mat_name):
        if self.group_exists(name):
            g = self.igroup_map[name]
            if g.mat_name != mat_name:
                raise MaterialConflictError(
                    f"material name doesn't match for group {name}: {g.mat_name}, {mat_name}"
                )
            return g

        # Return new group when `name` doesn't map to an existing group
        g = InterGroup(name, mat_name)
        self.igroup_map[name] = g
        return g

    def group_igeom(self, igeom, name, mat_name):
        self.get_igroup(name, mat_name).add_igeom(igeom)

    def get_textures(self):
        tex_set = {}  # Actually a dict, mapping items to None
        for mat_info in self.mat_map.values():
            tex_set[mat_info.tex_name.lower()] = None
        # for igroup in self.igroup_map.values():
        # 	for igeom in igroup.igeom_list:
        # 		if(igeom.tex_name is not None):
        # 			tex_set[igeom.tex_name.lower()] = None
        return tex_set

    def get_materials(self):
        return list(self.mat_map.values())

    def get_suffix(self):
        if self.name in cockpit_suffix_map:
            return cockpit_suffix_map[self.name]
        else:
            return "_cockpit"

    def get_pov_ibone(self):
        pov_bones = [
            ibone
            for ibone in self.ibone_list
            if (ibone.iobject and ibone.iobject.is_eyepoint)
            or ibone.name.lower()[5:8] == "pov"
        ]

        if len(pov_bones) > 1:
            print(
                f"WARNING: Multiple POV bones detected: {[b.name for b in pov_bones]}. Using the first one: {pov_bones[0].name}"
            )

        return pov_bones[0] if pov_bones else None

    def compute_bounds(self):
        min_x = INF
        min_y = INF
        min_z = INF
        max_x = -INF
        max_y = -INF
        max_z = -INF
        sq_radius = 0.0
        print("Compute mesh bounds:")
        for igroup in self.igroup_map.values():
            for igeom in igroup.igeom_list:
                print(f"igeom: {igeom.name}")
                original_pos_list = igeom.vertex_array["pos"]
                pos_list = original_pos_list.copy()
                min_x = min(min_x, min(pos_list[:, 0]))
                min_y = min(min_y, min(pos_list[:, 1]))
                min_z = min(min_z, min(pos_list[:, 2]))
                max_x = max(max_x, max(pos_list[:, 0]))
                max_y = max(max_y, max(pos_list[:, 1]))
                max_z = max(max_z, max(pos_list[:, 2]))
                sq_radius = max(
                    sq_radius, max(p[0] ** 2 + p[1] ** 2 + p[2] ** 2 for p in pos_list)
                )
                # print(min_x, min_y, min_z, max_x, max_y, max_z, sq_radius)
                # print()

                # original_pos_list = original_pos_list.T
                # pos_list = pos_list.T
                # ibone = igeom.ibone
                # ibone_transform = ibone.get_absolute_inv_transform().to_nparray_xyzw_rufp()
                # ibone_inv_transform = ibone.get_absolute_inv_transform().to_nparray_xyzw_rufp()
                # for ianim in self.ianim_list:
                # 	print(f"  ianim: {ianim.index} - {ianim.name}")
                # 	if(ibone not in ianim.ibone_tracks):
                # 		print("    ibone isn't part of animation")
                # 		#print()
                # 		continue
                # 	track = ianim.ibone_tracks[ibone]
                # 	for kf in track.keyframe_list:
                # 		#print(kf)
                # 		kf_transform = kf.get_transform().to_nparray_xyzw_rufp()
                # 		np.matmul(kf_transform, ibone_inv_transform, out=kf_transform)
                # 		np.matmul(ibone_transform, kf_transform, out=kf_transform)
                # 		np.matmul(kf_transform[:3, :3], original_pos_list, out=pos_list)
                # 		np.add(kf_transform[:3, [3]], pos_list, out=pos_list)
                #
                # 		min_x = min(min_x, min(pos_list[:, 0]))
                # 		min_y = min(min_y, min(pos_list[:, 1]))
                # 		min_z = min(min_z, min(pos_list[:, 2]))
                # 		max_x = max(max_x, max(pos_list[:, 0]))
                # 		max_y = max(max_y, max(pos_list[:, 1]))
                # 		max_z = max(max_z, max(pos_list[:, 2]))
                # 		sq_radius = max(sq_radius, max(p[0]**2 + p[1]**2 + p[2]**2 for p in pos_list))
                # 		#print(f"    {min_x}, {min_y}, {min_z}, {max_x}, {max_y}, {max_z}, {sq_radius}")
                # 	#print()

        radius = math.sqrt(sq_radius)
        return (-max_x, min_y, min_z, -min_x, max_y, max_z, radius)

    def build_submesh(self, mesh, igeom_list, material_name, submesh_name):
        if len(igeom_list) == 0:
            return

        vertex_count = sum(len(igeom.vertex_array) for igeom in igeom_list)
        tri_count = sum(len(igeom.tri_array) for igeom in igeom_list)

        print(f"\nBuild submesh {submesh_name}; Material: {material_name}")
        print(f"Vertex count: {vertex_count}, Tri count: {tri_count}")

        if vertex_count == 0 or tri_count == 0:
            return

        # Init the data buffers
        buf0 = np.empty(
            vertex_count, dtype=np.dtype([("pos", "<3f"), ("normal", "<3f")])
        )
        buf1 = np.empty(vertex_count, dtype=np.dtype([("color", "4B"), ("uv", "<2f")]))

        pos_array = buf0["pos"]
        normal_array = buf0["normal"]
        color_array = buf1["color"]
        uv_array = buf1["uv"]
        # Decide if we need 32-bit indices
        if vertex_count > 65535:
            tri_dtype = ("<I", 3)
            use_32_bit = True
        else:
            tri_dtype = ("<H", 3)
            use_32_bit = False

        tri_array = np.empty((tri_count,), dtype=tri_dtype)

        # Fill the vertex data buffers and the triangle buffer
        v0, v1 = 0, 0
        t0, t1 = 0, 0
        for igeom in igeom_list:
            v1 = v0 + len(igeom.vertex_array)
            t1 = t0 + len(igeom.tri_array)
            pos_array[v0:v1] = igeom.vertex_array["pos"]
            normal_array[v0:v1] = igeom.vertex_array["normal"]
            color_array[v0:v1] = igeom.vertex_array["color"]
            uv_array[v0:v1] = igeom.vertex_array["uv"]

            # vert indices need to be offset by v0 to avoid collision with each other
            np.add(igeom.tri_array, v0, out=tri_array[t0:t1])
            v0 = v1
            t0 = t1

        # Put vertex data into OGRE format
        pos_array[:, 0] *= -1  # flip x axis
        normal_array[:, 1:] *= -1  # flip y and z axes

        # Create the submesh object
        submesh = mesh.create_submesh(submesh_name)
        submesh.material_name = material_name

        submesh.index_count = 3 * tri_count
        submesh.indices_32_bit = use_32_bit
        submesh.set_index_buffer(tri_array)

        # Create the geometry for the submesh (VertexData object)
        vd = submesh.create_vertex_data(vertex_count)

        vd.vertex_declaration.add_element(0, VET.FLOAT3, VES.POSITION)
        vd.vertex_declaration.add_element(0, VET.FLOAT3, VES.NORMAL)
        vd.vertex_declaration.add_element(1, 10, VES.COLOUR)  # VET.UBYTE4_NORM
        vd.vertex_declaration.add_element(1, VET.FLOAT2, VES.TEXTURE_COORDINATES)

        # Add the vertex buffers to the submesh VertexData object
        vd.create_vertex_buffer(buf0, 0, 24)
        vd.create_vertex_buffer(buf1, 1, 12)

        # Add bone assignments
        v0 = 0
        for igeom in igeom_list:
            if igeom.ibone is not None:
                for i in range(len(igeom.vertex_array)):
                    submesh.create_bone_assignment(v0 + i, igeom.ibone.index, 1.0)
            v0 += len(igeom.vertex_array)

        return submesh

    def build_skeleton_animations(self, skeleton):
        if not self.is_skeletally_animated:
            return
        for ianim in self.ianim_loaded_list:
            if ianim.framecount <= 1:
                timescale = 0.0
            else:
                timescale = ianim.anim_duration / (ianim.framecount - 1)

            skel_anim = skeleton.create_animation(ianim.name, ianim.play_duration)
            print(
                f"{ianim.name}[{ianim.index}]: {ianim.play_duration} ({ianim.anim_duration}) {ianim.start_frame} {ianim.signed_framecount} {ianim.loop} {ianim.speed}"
            )
            for ibone, ib_track in ianim.ibone_tracks.items():
                ogre_bone = skeleton.get_bone(ibone.index)
                track = skel_anim.create_track(ogre_bone)
                for kf in ib_track:
                    time = kf.frame * timescale
                    ogre_kf = track.create_keyframe(
                        time,
                        trans=kf.translation.copy(),
                        rot=kf.rotation.copy(),
                    )


class InterGroup:
    """Intermediary Group (Analog to OGRE Submesh)"""

    def __init__(self, name, mat_name):
        self.name = name
        self.mat_name = mat_name
        self.igeom_list = []

    def add_igeom(self, igeom):
        self.igeom_list.append(igeom)

    def of_type(self, type):
        return tuple(igeom for igeom in self.igeom_list if igeom.ibone.type == type)


class InterObject:
    """Intermediary object."""

    name = None
    class_id = None
    transform = None
    parent_name = None
    parent = None
    abs_transform = None
    children = None
    bone_primary = None
    bone_cockpit = None
    geometry_primary = None
    geometry_cockpit = None
    is_headlight_mask = None
    is_eyepoint = None
    is_hardpoint = None

    no_bone_transform = False
    anim_reverse_pitch = False

    anim_translations = None
    anim_rotations = None

    def __init__(self):
        self.children = []
        self.geometry_primary = None
        self.geometry_cockpit = None

        self.anim_translations = []
        self.anim_rotations = []

    def geometry(self):
        return (
            igeom
            for igeom in (self.geometry_primary, self.geometry_cockpit)
            if igeom is not None
        )

    def set_class_id(self, class_id):
        self.class_id = class_id
        if class_id == ClassID.HEADLIGHT_MASK:
            self.is_headlight_mask = True
        elif class_id == ClassID.EYEPOINT:
            self.is_eyepoint = True
        elif class_id in ClassID.hardpoint_set:
            self.is_hardpoint = True

    def add_child(self, child):
        if child.parent is not None:
            raise HierarchyError(
                f"Child object {child.name} already has parent object {child.parent.name}; Cannot parent to {self.name}"
            )

        child.parent = self
        self.children.append(child)

    def set_bone(self, ibone):
        if ibone.type == 0:
            self.bone_primary = ibone
        elif (ibone.type) == 1:
            self.bone_cockpit = ibone
        else:
            raise InvalidBoneTypeError(f"Bone type {ibone.type} is not allowed")

        if ibone.igeom is not None:
            ibone.igeom.ibone = ibone

    def generate_abs_transform(self):
        if self.parent is not None:
            if self.parent.abs_transform is None:
                self.parent.generate_abs_transform()
            self.abs_transform = self.parent.abs_transform @ self.transform
        else:
            self.abs_transform = self.transform

        return self.abs_transform

    def create_translation_keyframe(self, frame, translation):
        ikf = InterObjectTranslationKeyframe(frame, translation)

        # Find where to put this keyframe in sorted order
        #   and set references to next and previous keyframes.
        ikf_prev = None
        ikf_next = None
        kf_count = len(self.anim_translations)
        i = 0
        for i in range(kf_count - 1, -1, -1):
            ikf_prev = self.anim_translations[i]
            if ikf_prev.frame <= frame:
                i += 1
                break
        else:
            ikf_prev = None
        if i + 1 < kf_count:
            ikf_next = self.anim_translations[i + 1]
        # `i` is the target index of the new keyframe
        # `ikf_prev` and `ikf_next` are set correctly

        # Insert new keyframe and link adjacent keyframes
        self.anim_translations.insert(i, ikf)
        if ikf_prev is not None:
            ikf_prev.next = ikf
            ikf.prev = ikf_prev
        if ikf_next is not None:
            ikf_next.prev = ikf
            ikf.next = ikf_next
        return ikf

    def create_rotation_keyframe(self, frame, rotation):
        ikf = InterObjectRotationKeyframe(frame, rotation)

        # Find where to put this keyframe in sorted order
        #   and set references to next and previous keyframes.
        ikf_prev = None
        ikf_next = None
        kf_count = len(self.anim_rotations)
        i = 0
        for i in range(kf_count - 1, -1, -1):
            ikf_prev = self.anim_rotations[i]
            if ikf_prev.frame <= frame:
                i += 1
                break
        else:
            ikf_prev = None
        if i + 1 < kf_count:
            ikf_next = self.anim_rotations[i + 1]
        # `i` is the target index of the new keyframe
        # `ikf_prev` and `ikf_next` are set correctly

        # Insert new keyframe and link adjacent keyframes
        self.anim_rotations.insert(i, ikf)
        if ikf_prev is not None:
            ikf_prev.next = ikf
            ikf.prev = ikf_prev
        if ikf_next is not None:
            ikf_next.prev = ikf
            ikf.next = ikf_next
        return ikf

    def create_keyframes_from_anim_mesh(self, anim_mesh):
        # Get frame-sorted lists of positions and orientations
        vdf_positions = anim_mesh.get_positions()
        vdf_orientations = anim_mesh.get_orientations()

        # The vdf keyframe positions and orientations are relative to the parent object
        #   while the intermediary keyframe translations and rotations are relative to the object itself
        for p_kf in vdf_positions:
            # Convert parent-relative to object-relative
            translation = p_kf.position.copy().antitranslate(self.transform.posit())
            self.create_translation_keyframe(
                frame=p_kf.frame,
                translation=translation,
            )

        for o_kf in vdf_orientations:
            # Convert parent-relative to object-relative
            rotation = o_kf.orientation.copy().antirotate(
                self.transform.compute_orientation()
            )
            self.create_rotation_keyframe(
                frame=o_kf.frame,
                rotation=rotation,
            )

    def process_geometry(self):
        # Process the geometry vertices so as to
        #   transform them by the `abs_transform` matrix.
        for igeom in self.geometry():
            if igeom is None:
                continue
            igeom.transform_geometry(self.abs_transform)


class InterGeometry:
    """Intermediary geometry data."""

    def __init__(self, name):
        self.name = name
        self.tex_name = None
        self.face_list = []
        self.vertex_list = []
        self.vertex_map = {}
        self._face = None

        self.vertex_array = None
        self.tri_array = None

        self.ibone = None
        self.iobject = None

    def begin_face(self, tex_name=None):
        """Begin construction of a face.

        Use add_vertex to add vertices to this face, then call end_face when you are done.

        Parameters
        ----------
        tex_name : str, optional
                Texture name of this face (default is None)
        """

        if self._face is not None:
            raise ModelPorterError(
                f"begin_face called before previous face was ended: {self.name}"
            )
        self._face = []
        if self.tex_name is None and tex_name != "":
            self.tex_name = tex_name

    def add_vertex(self, position, normal, uv, color):
        """Add a vertex to the current face

        Can only be used after calling begin_face.

        Parameters
        ----------
        position : list
                3D position of this vertex
        normal : list
                3D normal of this vertex
        uv : list
                2D uv coordinates of this vertex
        color : list
                3D rgb color of this vertex
        """

        vdata = (
            tuple(position),
            tuple(normal),
            (0xFF, color[0], color[1], color[2]),
            (uv[0], uv[1]),
        )
        index = -1
        if vdata not in self.vertex_map:
            index = len(self.vertex_list)
            self.vertex_map[vdata] = index
            self.vertex_list.append(vdata)
        else:
            index = self.vertex_map[vdata]
        self._face.append(index)

    def end_face(self):
        """End construction of a face.

        Call this after you're done adding vertices to finalize the face.
        """

        # Throw out polygons with fewer than 3 sides
        if len(self._face) >= 3:
            self.face_list.append(self._face)
        self._face = None

    def apply_geometry(self):
        """Finalize completed geometry.

        Call this once you're done adding faces. This will generate an array of
        positions and normals, as well as triangulate all the faces.
        These are accessed from `vertex_array` and `tri_array` respectively.
        """

        if self._face is not None:
            raise ModelPorterError(
                f"apply_geometry called before previous face was ended: {self.name}"
            )

        self.vertex_array = np.array(
            self.vertex_list,
            dtype=[
                ("pos", "<3f"),
                ("normal", "<3f"),
                ("color", "4B"),
                ("uv", "<2f"),
            ],
        )

        # Tri array:
        # Triangulation of faces is done by fanning out of the first face vertex
        # Note: All these faces are guaranteed to have at least 3 vertices
        tri_count = sum(len(face) - 2 for face in self.face_list)
        self.tri_array = np.empty(tri_count, dtype="<3I")
        next_tri_index = 0
        for face in self.face_list:
            face_ortho = Vector3()  # This will be the face's "orthogonal" vector
            fn0 = face[
                0
            ]  # This is the first point of the face (fanning is done from here)

            # Calculate the face's orthogonal vector
            for i in range(2, len(face)):  # For each tri of the face
                fn1 = face[i - 1]
                fn2 = face[i]
                # Add this tri's orthogonal vector to the face's
                face_ortho += Vector3.triangle_cross(
                    *map(
                        Vector3.from_array_ruf,
                        self.vertex_array["pos"][[fn0, fn1, fn2]],
                    )
                )

            # Now triangulate the face
            for i in range(2, len(face)):  # For each tri of the face
                fn1 = face[i - 1]
                fn2 = face[i]

                # If this tri is pointing the opposite direction of the whole face, flip this tri
                d = face_ortho.dot(
                    Vector3.triangle_cross(
                        *map(
                            Vector3.from_array_ruf,
                            self.vertex_array["pos"][[fn0, fn1, fn2]],
                        )
                    )
                )
                if d < 0:
                    fn1, fn2 = fn2, fn1

                # Add triangle
                self.tri_array[next_tri_index] = (fn0, fn1, fn2)
                next_tri_index += 1

    def correct_normals(self):
        # If the normals are mostly flipped the wrong way around,
        #   flip them to the correct direction.
        # I've encountered a custom sdf with inverted normals, which caused
        #   faces to not light up correctly. This procedure fixes that issue.

        inverted_count = 0

        # Loop through triangles and count the number of inverted normals
        for i0, i1, i2 in self.tri_array:
            v0, v1, v2 = (
                *map(Vector3.from_array_ruf, self.vertex_array["pos"][[i0, i1, i2]]),
            )  # vertex positions
            n0, n1, n2 = (
                *map(Vector3.from_array_ruf, self.vertex_array["normal"][[i0, i1, i2]]),
            )  # vertex normals

            # Vector orthogonal to the triangle, representing the face normal
            face_normal = Vector3.triangle_cross(v0, v1, v2)

            # Positive dot product means the vertex normal points in roughly
            #   the same direction as the triangle's true normal.
            # Negative dot product means the opposite.
            if face_normal.dot(n0) < 0:
                inverted_count += 1
            if face_normal.dot(n1) < 0:
                inverted_count += 1
            if face_normal.dot(n2) < 0:
                inverted_count += 1

        if inverted_count > 0:
            print(
                f"{inverted_count} inverted face-vertex normals detected out of {len(self.tri_array)*3} possible"
            )
        if inverted_count * 2 > len(self.tri_array) * 3:
            print(f" > Correcting normals")
            self.vertex_array["normal"] *= -1

    def restore_normals(self):
        # Fix broken normals
        # The stock Command Tower (abhqcp.sdf) has a triangle with NaN normals.
        #   That triangle visibly flickers without this fix.

        # NOTE: This is a bit of a naive solution, but it does get rid of NaNs.
        #   Each NaN normal is replaced with the face normal of the first triangle
        #   to use that vertex normal.

        # Loop through triangles
        for i0, i1, i2 in self.tri_array:
            n0, n1, n2 = (
                *map(Vector3.from_array_ruf, self.vertex_array["normal"][[i0, i1, i2]]),
            )  # vertex normals
            if not (n0.isNaN() or n1.isNaN() or n2.isNaN()):
                continue

            # The triangle's true normal vector
            norm = Vector3.triangle_cross(n0, n1, n2).normalize()

            # Set any NaN normals to the triangle normal
            if n0.isNaN():
                print(f"NaN vertex [{i0}]: {n0}")
                self.vertex_array["normal"][i0] = norm.to_ruf()
            if n1.isNaN():
                print(f"NaN vertex [{i1}]: {n1}")
                self.vertex_array["normal"][i1] = norm.to_ruf()
            if n2.isNaN():
                print(f"NaN vertex [{i2}]: {n2}")
                self.vertex_array["normal"][i2] = norm.to_ruf()

    def normalize_normals(self):
        normals = self.vertex_array["normal"]
        # print(normals.shape)
        # print(normals)
        normals /= np.linalg.norm(normals, axis=1)[:, None]

    # def add_test_faces(self):
    # 	positions = [
    # 		(-0.15, 0.392349, 0.066774),
    # 		(-0.15, -0.035988, -0.118699),
    # 		(-0.15, 0.246922, 0.358152),
    # 		(-0.15, -0.013126, 0.507736),
    # 		(-0.15, -0.242971, 0.1024),
    # 		(-0.15, -0.21927, 0.646345),
    # 		(-0.15, -0.519271, 0.646345),
    # 		(-0.15, -0.392349, -0.646345),
    # 		(-0.15, 0.523054, -0.203256),
    # 	]
    #
    # 	self.begin_face("svtank00")
    # 	for pos in positions:
    # 		self.add_vertex(
    # 			position=pos,
    # 			normal=(-1.0, 0.0, 0.0),
    # 			uv=(0.0, 0.0),
    # 			color=(255, 0, 0),
    # 		)
    # 	self.end_face()

    def add_geo_faces(self, geo):
        # if(geo.name == "SCZ11MOR"):
        # 	self.add_test_faces()
        # 	return

        # Create numpy array views of the buffer data
        vpos_array = np.frombuffer(
            geo.vertex_pos_buffer, dtype="<3f", count=geo.vert_count
        )
        vnormal_array = np.frombuffer(
            geo.vertex_normal_buffer, dtype="<3f", count=geo.vert_count
        )

        # Go through the polygons of this geo and add their face-vertex data
        for face in geo.face_list:
            self.begin_face(face.texture_name)
            for facenode in face.wireframe:
                self.add_vertex(
                    position=vpos_array[facenode.vertex_index],
                    normal=vnormal_array[facenode.vertex_index],
                    uv=(facenode.uv.u, facenode.uv.v),
                    color=(face.color.r, face.color.g, face.color.b),
                )
            self.end_face()

    def transform_geometry(self, transform):
        pos_array = self.vertex_array[
            "pos"
        ].T  # Transpose so vectors fill the columns of the 2d array
        norm_array = self.vertex_array["normal"].T  # |

        nptransform3x3 = transform.to_nparray_xyz_ruf()

        # Multiply in place each column-vector pos in pos array by the transform
        # Then add the position part of the transform to each pos
        np.matmul(nptransform3x3, pos_array, out=pos_array)
        np.add(pos_array, transform.posit().to_nparray_vector_ruf(), out=pos_array)

        # Multiply normals by inverse transpose (normals are pseudo-vectors)
        inv_transform = np.linalg.inv(nptransform3x3).T
        np.matmul(inv_transform, norm_array, out=norm_array)


class InterBone:
    """Intermediary bone."""

    def __init__(self, index, name="", type="primary", is_required=True):
        # print(type)
        self.index = index
        self.name = name
        self.type = type  # primary or cockpit bone
        self.is_required = is_required  # is this bone forced to exist in both primary and cockpit skeletons, if two are used?
        self.igeom = None  # Optional igeom attached to this ibone
        self.iobject = None  # Optional iobject this ibone corresponds with
        self.parent = None
        self.children = []
        self.position = Vector3()
        self.orientation = Quaternion()
        self.size = Vector3(1.0, 1.0, 1.0)

    def add_child(self, child):
        if child.parent is not None:
            raise ValueError(
                f"ibone {child.name} cannot be added to ibone {self.name} - already a child of {child.parent.name}!"
            )
        child.parent = self
        self.children.append(child)

    def remove_child(self, child):
        self.children.remove(child)
        child.parent = None

    def set_geometry(self, igeom):
        if igeom is not None:
            self.igeom = igeom
            igeom.ibone = self

    def set_transform(self, transform):
        self.position = transform.posit()
        self.orientation = transform.compute_orientation()
        self.size = transform.compute_scale()

    def get_transform(self):
        return Transform.from_quaternion_translation(self.orientation, self.position)

    def get_inv_transform(self):
        return Transform.inv_from_quaternion_translation(
            self.orientation, self.position
        )

    def get_absolute_transform(self):
        t = self.get_transform()
        b = self.parent
        while b is not None:
            t.transform(b.get_transform())
            b = b.parent
        return t

    def get_absolute_inv_transform(self):
        t = self.get_inv_transform()
        b = self.parent
        while b is not None:
            t.pretransform(b.get_inv_transform())
            b = b.parent
        return t

    def get_absolute_position(self):
        pos = self.position
        b = self.parent
        while b is not None:
            pos.rotate(b.orientation).translate(b.position)
            b = b.parent
        return pos

    def get_absolute_orientation(self):
        ori = self.orientation
        b = self.parent
        while b is not None:
            ori.rotate(b.orientation)
            b = b.parent
        return ori


class InterAnim:
    def __init__(self):
        self.index = -1
        self.start_frame = 0
        self.signed_framecount = 0
        self.framecount = 0
        self.forward = True
        self.speed = 1.0
        self.loop = 0
        self.anim_duration = 0.0

        self.name = None
        self.play_duration = 0.0
        self.ibone_tracks = {}


class InterObjectTranslationKeyframe:
    def __init__(self, frame, translation):
        self.frame = frame
        self.translation = translation
        self.prev = None
        self.next = None

    def move(self, forward):
        return self.next if forward else self.prev


class InterObjectRotationKeyframe:
    def __init__(self, frame, rotation):
        self.frame = frame
        self.rotation = rotation
        self.prev = None
        self.next = None

    def move(self, forward):
        return self.next if forward else self.prev


class InterAnimTrack:
    def __init__(self):
        self.keyframe_list = []

    def __iter__(self):
        return iter(self.keyframe_list)

    def copy(self):
        track = InterAnimTrack()
        track.keyframe_list = [kf.copy() for kf in self.keyframe_list]
        return track

    def append(self, kf):
        self.keyframe_list.append(kf)

    def flip_pitch(self):
        for kf in self.keyframe_list:
            kf.rotation.x *= -1

    def __str__(self):
        return "[" + ", ".join((str(kf) for kf in self.keyframe_list)) + "]"


class InterAnimKeyframe:
    def __init__(self, frame, translation, rotation):
        self.frame = frame
        self.translation = translation
        self.rotation = rotation

    def get_transform(self):
        return Transform.from_quaternion_translation(self.rotation, self.translation)

    def move(self, forward):
        return self.next if forward else self.prev

    def copy(self):
        return InterAnimKeyframe(
            self.frame, self.translation.copy(), self.rotation.copy()
        )

    def __str__(self):
        return f"<{self.frame}: {self.translation}, {self.rotation}>"


color_palette_map = {}


def get_color_palette(asset_resolver):
    act_path = asset_resolver.get_act_path()
    if act_path is None:
        print("No .act filepath provided")
        return None
    if act_path not in color_palette_map:
        try:
            with open(act_path, "rb") as stream:
                bzact_serializer = BZActSerializer()
                x = bzact_serializer.deserialize(stream)
                color_palette_map[act_path] = np.array(x, dtype="B")
        except OSError:
            print(f"Failed to read .act palette file {act_path}")
            print(traceback.format_exc())
            return None

    return color_palette_map[act_path]


# Construct the DDS textures
def bzmap_to_pilimage(bzmap, asset_resolver):
    # If Pillow isn’t available, we can’t build an Image object
    if not HAVE_PIL:
        return None

    width, height = bzmap.get_size()

    # asset_resolver is only used if a color palette is needed
    if bzmap.pixel_format == BZMapFormat.INDEXED:
        color_palette = get_color_palette(asset_resolver)
        if color_palette is None:
            return None
        ar = np.frombuffer(bzmap.get_buffer(), dtype="B")
        ar2 = color_palette[ar]
        ar2.shape = (height, width, 3)

        return Image.fromarray(ar2, mode="RGB")

    elif bzmap.pixel_format == BZMapFormat.ARGB4444:
        ar = np.frombuffer(bzmap.get_buffer(), dtype="<H")
        ar.shape = (height, width)

        ar2 = np.zeros((height, width, 4), dtype="B")
        ar2[:, :, 0] = np.interp(  # Red
            np.bitwise_and(np.right_shift(ar[:][:], 8), 15),
            (0, 15),
            (0, 255),
        ).astype("B")
        ar2[:, :, 1] = np.interp(  # Green
            np.bitwise_and(np.right_shift(ar[:][:], 4), 15),
            (0, 15),
            (0, 255),
        ).astype("B")
        ar2[:, :, 2] = np.interp(  # Blue
            np.bitwise_and(ar[:][:], 15),
            (0, 15),
            (0, 255),
        ).astype("B")
        ar2[:, :, 3] = np.interp(  # Alpha
            np.bitwise_and(np.right_shift(ar[:][:], 12), 15),
            (0, 15),
            (0, 255),
        ).astype("B")

        return Image.fromarray(ar2, mode="RGBA")

    elif bzmap.pixel_format == BZMapFormat.RGB565:
        ar = np.frombuffer(bzmap.get_buffer(), dtype="<H")
        ar.shape = (height, width)
        ar2 = np.zeros((height, width, 3), dtype="B")
        ar2[:, :, 0] = np.interp(
            np.right_shift(ar[:][:], 11), (0, 31), (0, 255)
        ).astype("B")
        ar2[:, :, 1] = np.interp(
            np.bitwise_and(np.right_shift(ar[:][:], 5), 0b111111), (0, 63), (0, 255)
        ).astype("B")
        ar2[:, :, 2] = np.interp(
            np.bitwise_and(ar[:][:], 0b11111), (0, 31), (0, 255)
        ).astype("B")

        return Image.fromarray(ar2, mode="RGB")

    elif bzmap.pixel_format == BZMapFormat.ARGB8888:
        ar = np.frombuffer(bzmap.get_buffer(), dtype="B")
        ar.shape = (height, width, 4)
        ar = ar[:, :, [2, 1, 0, 3]]
        return Image.fromarray(ar, mode="RGBA")

    elif bzmap.pixel_format == BZMapFormat.XRGB8888:
        ar = np.frombuffer(bzmap.get_buffer(), dtype="B")
        ar.shape = (height, width, 4)
        ar = ar[:, :, [2, 1, 0]]
        return Image.fromarray(ar, mode="RGB")
    else:
        raise UnsupportedFileTypeError(f"Unknown BZMapFormat {bzmap.pixel_format}")


def _get_palette_from_import_geo(map_dir):
    """
    Ask import_geo for the palette it would use for this directory,
    reusing its built-in ACT data and search logic.

    Returns a NumPy array of shape (256, 3) or None.
    """
    try:
        # bzrmodelporter is in a subpackage; '..' goes back to bz98tools
        from .. import import_geo as _imp
    except Exception:
        # Running outside Blender / module unavailable
        return None

    getter = getattr(_imp, "_get_palette_for_dir", None)
    if getter is None:
        return None

    try:
        pal_list = getter(map_dir)
        if not pal_list:
            return None
        return np.array(pal_list, dtype="B")
    except Exception:
        print(f"[bzrmodelporter] Failed to get palette from import_geo for {map_dir}")
        print(traceback.format_exc())
        return None


def _resolve_act_palette(asset_resolver, map_dir=None):
    """
    1) Try explicit ACT path from AssetResolver (CLI / config.cfg / UI).
    2) If none, fall back to import_geo's ACT / built-in palette logic
       based on the .map's directory.
    """
    # 1) Explicit ACT path (CLI-style)
    act_path = None
    if asset_resolver is not None:
        try:
            act_path = asset_resolver.get_act_path()
        except Exception:
            act_path = None

    if act_path:
        key = ("act", os.fspath(act_path))
        pal = _color_palette_cache.get(key)
        if pal is not None:
            return pal

        try:
            from ..bzact_serializer import BZActSerializer

            with open(act_path, "rb") as stream:
                bzact_serializer = BZActSerializer()
                pal_list = bzact_serializer.deserialize(stream)
            pal = np.array(pal_list, dtype="B")
            _color_palette_cache[key] = pal
            return pal
        except OSError:
            print(f"[bzrmodelporter] Failed to read .act palette file {act_path}")
            print(traceback.format_exc())

    # 2) Fallback: same ACT/palette logic as import_geo (if available)
    if map_dir:
        key = ("geo", os.fspath(map_dir))
        pal = _color_palette_cache.get(key)
        if pal is not None:
            return pal

        pal = _get_palette_from_import_geo(os.fspath(map_dir))
        if pal is not None:
            _color_palette_cache[key] = pal
            return pal

    return None


def _bzmap_to_rgba_bytes(map_source, asset_resolver, map_dir=None):
    """
    Convert a BZMap (or a .map filepath) to raw RGBA8 bytes.

    map_source:
        - Either a BZMap instance
        - Or a path-like pointing at a .map file
    map_dir:
        - Directory to search for ACT palettes (optional, but recommended
          when map_source is a filepath).

    Returns (width, height, rgba_bytes) or (None, None, None) on failure.
    """
    # Normalise map_source -> BZMap instance
    if isinstance(map_source, BZMap):
        bzmap = map_source
        map_path = None
    else:
        map_path = Path(map_source)
        if not map_path.exists():
            print(f"[bzrmodelporter] Map file not found: {map_path}")
            return None, None, None

        if map_dir is None:
            map_dir = os.fspath(map_path.parent)

        bzmap = BZMap()
        serializer = BZMapSerializer()
        with open(map_path, "rb") as stream:
            serializer.deserialize(stream, bzmap)

    if map_dir is None and map_path is not None:
        map_dir = os.fspath(map_path.parent)

    width, height = bzmap.get_size()
    buf = bzmap.get_buffer()
    if buf is None:
        print("[bzrmodelporter] BZMap has no pixel buffer.")
        return None, None, None

    pixel_format = bzmap.pixel_format
    row_bytes = bzmap.row_byte_size

    # INDEXED → need palette
    if pixel_format == BZMapFormat.INDEXED:
        color_palette = _resolve_act_palette(asset_resolver, map_dir)
        if color_palette is None:
            print("[bzrmodelporter] No ACT palette available for INDEXED map.")
            return None, None, None

        ar = np.frombuffer(buf, dtype="B")
        if ar.size < width * height:
            print(
                f"[bzrmodelporter] Buffer too small for INDEXED map: "
                f"{ar.size}, expected {width*height}"
            )
            return None, None, None

        ar = ar[: width * height]
        rgb = color_palette[ar]  # shape (N,3)
        rgb = rgb.reshape((height, width, 3))

        rgba = np.zeros((height, width, 4), dtype="B")
        rgba[:, :, :3] = rgb
        rgba[:, :, 3] = 255
        return width, height, rgba.tobytes()

    # ARGB4444
    elif pixel_format == BZMapFormat.ARGB4444:
        ar = np.frombuffer(buf, dtype="<H")
        if ar.size < width * height:
            return None, None, None
        ar = ar[: width * height].reshape((height, width))

        rgba = np.zeros((height, width, 4), dtype="B")
        rgba[:, :, 0] = np.interp(
            np.bitwise_and(np.right_shift(ar, 8), 15), (0, 15), (0, 255)
        ).astype("B")
        rgba[:, :, 1] = np.interp(
            np.bitwise_and(np.right_shift(ar, 4), 15), (0, 15), (0, 255)
        ).astype("B")
        rgba[:, :, 2] = np.interp(
            np.bitwise_and(ar, 15), (0, 15), (0, 255)
        ).astype("B")
        rgba[:, :, 3] = np.interp(
            np.bitwise_and(np.right_shift(ar, 12), 15), (0, 15), (0, 255)
        ).astype("B")

        return width, height, rgba.tobytes()

    # RGB565
    elif pixel_format == BZMapFormat.RGB565:
        ar = np.frombuffer(buf, dtype="<H")
        if ar.size < width * height:
            return None, None, None
        ar = ar[: width * height].reshape((height, width))

        rgba = np.zeros((height, width, 4), dtype="B")
        rgba[:, :, 0] = np.interp(np.right_shift(ar, 11), (0, 31), (0, 255)).astype("B")
        rgba[:, :, 1] = np.interp(
            np.bitwise_and(np.right_shift(ar, 5), 0b111111), (0, 63), (0, 255)
        ).astype("B")
        rgba[:, :, 2] = np.interp(
            np.bitwise_and(ar, 0b11111), (0, 31), (0, 255)
        ).astype("B")
        rgba[:, :, 3] = 255

        return width, height, rgba.tobytes()

    # ARGB8888
    elif pixel_format == BZMapFormat.ARGB8888:
        ar = np.frombuffer(buf, dtype="B")
        if ar.size < width * height * 4:
            return None, None, None
        rgba = ar[: width * height * 4].reshape((height, width, 4))
        # BZ layout is likely BGRA (standard Windows 32bpp) but Pillow logic says [2,1,0,3] reorder.
        # Let's stick to the existing logic reordering BGRA -> RGBA for consistency
        rgba = rgba[:, :, [2, 1, 0, 3]]
        return width, height, rgba.tobytes()

    # XRGB8888
    elif pixel_format == BZMapFormat.XRGB8888:
        ar = np.frombuffer(buf, dtype="B")
        if ar.size < width * height * 4:
            return None, None, None
        xrgb = ar[: width * height * 4].reshape((height, width, 4))
        # Reorder BGRX -> RGBA
        rgba = np.zeros((height, width, 4), dtype="B")
        rgba[:, :, 0] = xrgb[:, :, 2]
        rgba[:, :, 1] = xrgb[:, :, 1]
        rgba[:, :, 2] = xrgb[:, :, 0]
        rgba[:, :, 3] = 255
        return width, height, rgba.tobytes()

    else:
        raise UnsupportedFileTypeError(f"Unknown BZMapFormat {pixel_format}")


def _dds_uncompressed_rgba_bytes(width, height, rgba_bytes):
    """
    Build uncompressed 32-bit RGBA DDS (A8R8G8B8 layout).
    - width, height: image size
    - rgba_bytes: len == width * height * 4, in RGBA order (R,G,B,A per pixel)
    """
    # DDS expects A8R8G8B8: little-endian dword = 0xAARRGGBB -> bytes: BB GG RR AA
    if len(rgba_bytes) != width * height * 4:
        raise ValueError(
            f"rgba_bytes has wrong size: {len(rgba_bytes)}; expected {width*height*4}"
        )

    # Reorder RGBA -> BGRA for A8R8G8B8 layout using numpy for performance
    rgba_array = np.frombuffer(rgba_bytes, dtype="B").reshape((-1, 4))
    bgra_array = rgba_array[:, [2, 1, 0, 3]]
    bgra = bgra_array.tobytes()

    def dword(x):
        return struct.pack("<I", x)

    flags = 0x00021007  # caps | height | width | pixelformat | pitch
    pitch = width * 4
    depth = 0
    mipmaps = 0

    # Pixel format for uncompressed A8R8G8B8
    pf_flags = 0x00000041  # DDPF_RGB | DDPF_ALPHAPIXELS
    pf_fourcc = 0
    pf_bpp = 32
    pf_rmask = 0x00FF0000
    pf_gmask = 0x0000FF00
    pf_bmask = 0x000000FF
    pf_amask = 0xFF000000

    caps1 = 0x00001000  # DDSCAPS_TEXTURE
    caps2 = 0
    caps3 = 0
    caps4 = 0

    header = bytearray()
    header.extend(b"DDS ")
    header.extend(dword(124))  # dwSize
    header.extend(dword(flags))  # dwFlags
    header.extend(dword(height))  # dwHeight
    header.extend(dword(width))  # dwWidth
    header.extend(dword(pitch))  # dwPitchOrLinearSize
    header.extend(dword(depth))  # dwDepth
    header.extend(dword(mipmaps))  # dwMipMapCount
    header.extend(b"\0" * (11 * 4))  # dwReserved1[11]

    # DDS_PIXELFORMAT
    header.extend(dword(32))  # dwSize
    header.extend(dword(pf_flags))  # dwFlags
    header.extend(dword(pf_fourcc))  # dwFourCC
    header.extend(dword(pf_bpp))  # dwRGBBitCount
    header.extend(dword(pf_rmask))  # dwRBitMask
    header.extend(dword(pf_gmask))  # dwGBitMask
    header.extend(dword(pf_bmask))  # dwBBitMask
    header.extend(dword(pf_amask))  # dwABitMask

    # caps
    header.extend(dword(caps1))
    header.extend(dword(caps2))
    header.extend(dword(caps3))
    header.extend(dword(caps4))
    header.extend(dword(0))  # dwReserved2

    return bytes(header) + bytes(bgra)


def _texture_source_exists(asset_resolver, tex_name):
    """True if a .png or .map for this texture can be found. Faces whose
    texture is missing (e.g. stock .map files Redux no longer ships) fall
    back to flat colours instead of pointing at a DDS that never gets written."""
    base = os.path.splitext(tex_name)[0]
    return (
        asset_resolver.get_resource_path(base + ".png") is not None
        or asset_resolver.get_map_path(base) is not None
    )


def _flat_color_rgba(color_list):
    """One texel per flat colour, as (width, height, RGBA bytes); no Pillow."""
    rgb = np.asarray(color_list, dtype=np.uint8).reshape(1, len(color_list), 3)
    alpha = np.full((1, len(color_list), 1), 255, np.uint8)
    return len(color_list), 1, np.concatenate([rgb, alpha], axis=2).tobytes()


def _write_dds_uncompressed_rgba(width, height, rgba_bytes, out_path):
    """
    Write uncompressed 32-bit RGBA DDS (A8R8G8B8 layout).
    """
    data = _dds_uncompressed_rgba_bytes(width, height, rgba_bytes)
    out_path = os.fspath(out_path)
    with open(out_path, "wb") as f:
        f.write(data)


def create_material_string(mat_name, super_mat_name, tex_name):
    # tex_name may be a plain BZ texture name ("avcarr") or a PNG-ish name ("MyTex.png").
    # Use the *base filename* for the DDS name, without extension.
    base = Path(tex_name).name  # drop any directories
    base = os.path.splitext(base)[0]  # drop .png/.tga/whatever
    dds_name = f"{base}_D.dds"

    return (
        f"material {mat_name} : {super_mat_name}\n"
        "{\n"
        f"\tset_texture_alias DiffuseMap {dds_name}\n"
        "\tset_texture_alias NormalMap flat_N.dds\n"
        "\tset_texture_alias SpecularMap black.dds\n"
        "\tset_texture_alias EmissiveMap black.dds\n"
        "\t\n"
        '\tset $diffuse "1 1 1"\n'
        '\tset $ambient "1 1 1"\n'
        '\tset $specular ".7 .7 .7"\n'
        '\tset $shininess "127"\n'
        '\tset $glow "1 1 1"\n'
        "}\n"
        "\n\n"
    )


hardpoint_class_id_set = {
    ClassID.WEAPON_HARDPOINT,
    ClassID.CANNON_HARDPOINT,
    ClassID.ROCKET_HARDPOINT,
    ClassID.MORTAR_HARDPOINT,
    ClassID.SPECIAL_HARDPOINT,
}

nonrendering_class_id_set = {
    ClassID.HEADLIGHT_MASK,
    ClassID.EYEPOINT,
    ClassID.WEAPON_HARDPOINT,
    ClassID.CANNON_HARDPOINT,
    ClassID.ROCKET_HARDPOINT,
    ClassID.MORTAR_HARDPOINT,
    ClassID.SPECIAL_HARDPOINT,
    ClassID.FLAME_EMITTER,
    ClassID.SMOKE_EMITTER,
    ClassID.DUST_EMITTER,
}


def class_id_hardpoint(class_id):
    return class_id in hardpoint_class_id_set


def class_id_renderable(class_id):
    return class_id not in nonrendering_class_id_set


def build_mesh(imodel, type="primary"):
    mesh = Mesh()
    name = imodel.name
    if type == "cockpit":
        name += imodel.get_suffix()
    mesh.skeleton_name = f"{name}.skeleton"
    print(f"Build mesh: {name}")

    # Build each submesh
    for igroup in imodel.igroup_map.values():
        if imodel.use_cockpit:
            imodel.build_submesh(
                mesh, igroup.of_type(type), igroup.mat_name, igroup.name
            )
        else:
            imodel.build_submesh(mesh, igroup.igeom_list, igroup.mat_name, igroup.name)

    # Set the mesh's bounding box
    aabb = mesh.aabb
    (
        aabb.min_x,
        aabb.min_y,
        aabb.min_z,
        aabb.max_x,
        aabb.max_y,
        aabb.max_z,
        mesh.bound_radius,
    ) = imodel.compute_bounds()
    if imodel.settings.boundingbox_scale_factors is not None:
        aabb.scale_from_midpoint(
            imodel.settings.boundingbox_scale_factors.x,
            imodel.settings.boundingbox_scale_factors.y,
            imodel.settings.boundingbox_scale_factors.z,
        )
    return mesh


def _stabilize_cockpit_bone_for_output(imodel, ibone, type):
    if not imodel.settings.walker_cockpit_stabilization_enabled():
        return False
    if type != "cockpit" or not imodel.use_cockpit:
        return False
    if ibone.type == "cockpit":
        return True
    iobject = getattr(ibone, "iobject", None)
    return bool(iobject is not None and iobject.is_eyepoint)


def build_skeleton(imodel, type="primary"):
    skeleton = Skeleton()

    # First pass: Create the ogre bones
    for ibone in imodel.ibone_list:
        name = ibone.name
        if skeleton.bone_name_exists(name):
            name = name + "_copy"
        ogre_bone = skeleton.create_bone(name, ibone.index)

        if _stabilize_cockpit_bone_for_output(imodel, ibone, type):
            absolute_transform = ibone.get_absolute_transform()
            ogre_bone.position = absolute_transform.posit()
            ogre_bone.orientation = absolute_transform.compute_orientation()
        else:
            ogre_bone.position = ibone.position
            ogre_bone.orientation = ibone.orientation
        ogre_bone.scale = ibone.size

    # Second pass: Parent the ogre bones
    for ibone in imodel.ibone_list:
        if _stabilize_cockpit_bone_for_output(imodel, ibone, type):
            continue
        if ibone.parent is None:
            continue
        ogre_bone = skeleton.get_bone(ibone.index)
        ogre_parent = skeleton.get_bone(ibone.parent.index)
        ogre_parent.add_child(ogre_bone)

    imodel.build_skeleton_animations(skeleton)

    return skeleton


def build_ogre(
    imodel,
    type="primary",
):
    # Construct the OGRE Mesh
    mesh = build_mesh(imodel, type)

    # Construct the OGRE Skeleton
    skeleton = build_skeleton(imodel, type)

    return mesh, skeleton


def write_ogre(mesh, skeleton, mesh_filename, asset_resolver, suppress_write=False):
    # Write the mesh file
    mesh_filepath = asset_resolver.get_output_mesh_path(mesh_filename)
    if mesh_filepath is not None:
        try:
            if suppress_write:
                print(f"*File write suppressed* {mesh_filepath}")
            else:
                stream = io.BytesIO()
                MeshSerializer(stream).write(mesh)
                asset_resolver.write_bytes_if_changed(
                    mesh_filepath,
                    stream.getvalue(),
                    label="mesh",
                )
        except OSError:
            print(f"Failed to write mesh file {mesh_filepath} for mesh {mesh_filename}")
            print(traceback.format_exc())

    # Write the skeleton file
    skeleton_filename = mesh.skeleton_name
    skeleton_filepath = asset_resolver.get_output_skeleton_path(skeleton_filename)
    if skeleton_filepath is not None:
        try:
            if suppress_write:
                print(f"*File write suppressed* {skeleton_filepath}")
            else:
                stream = io.BytesIO()
                SkeletonSerializer(stream).write(skeleton)
                asset_resolver.write_bytes_if_changed(
                    skeleton_filepath,
                    stream.getvalue(),
                    label="skeleton",
                )
        except OSError:
            print(
                f"Failed to write skeleton file {skeleton_filepath} for skeleton {skeleton_filename}"
            )
            print(traceback.format_exc())


def lt(x, y, forward=True):
    return x < y if forward else x > y


def lte(x, y, forward=True):
    return x <= y if forward else x >= y


class FixedScopeInfo:
    def __init__(self):
        self.scope_x = 0.0  # Camera-relative coordinates of
        self.scope_y = 0.0  #   the center of the scope square
        self.scope_z = 0.0  #   when the sniper scope is visible
        self.scope_scale = 1.0  # Size of the scope square
        self.behind_dist = 1.0  # How far behind the camera the scope is when hidden
        self.transform = Transform()

    def set_fixed_transform(self, x, y, z, scale, behind_dist):
        self.scope_x = x
        self.scope_y = y
        self.scope_z = z
        self.scope_scale = scale
        self.behind_dist = behind_dist
        self.transform = Transform(
            scale, 0, 0, 0, scale, 0, 0, 0, scale, x, y, -behind_dist
        )

    def set_transform_american(self):
        z = 0.1
        scale = z / 6.0
        self.set_fixed_transform(
            x=2.975 * scale,
            y=0.23 * scale,
            z=z,
            scale=scale,
            behind_dist=1.0,
        )

    def set_transform_soviet(self):
        z = 0.1
        scale = z / 6.0
        self.set_fixed_transform(
            x=2.58 * scale,
            y=0.27 * scale,
            z=z,
            scale=scale,
            behind_dist=1.0,
        )

    def get_transform(self):
        return self.transform.copy()


class AttachedScopeInfo:
    def __init__(self):
        self.gun_name = None
        self.transform = Transform()

    def set_attached_transform(self, trans, gun_name=None):
        self.transform = trans
        self.gun_name = gun_name

    def get_transform(self):
        return self.transform.copy()


class GeometryScopeInfo:
    def __init__(self):
        pass


PERSON_ANIM_CROUCH = 0
PERSON_ANIM_STAND = 1
PERSON_ANIM_STANDING = 2
PERSON_ANIM_CROUCHING = 3
PERSON_ANIM_RUN_FORWARD = 4
PERSON_ANIM_RUN_BACKWARD = 5
PERSON_ANIM_STRAFE_LEFT = 6
PERSON_ANIM_STRAFE_RIGHT = 7
PERSON_ANIM_FALL = 8  # Snipe death

# Redux specific
PERSON_ANIM_IDLE_PARACHUTE = 9
PERSON_ANIM_LAND_PARACHUTE = 10
PERSON_ANIM_JUMP = 11


def port_bwd2(target_filepath, asset_resolver, settings):
    print(f"Porting bwd2 model {target_filepath}")

    # Extract information from the bwd2 file path
    bwd2_dirpath, bwd2_basename = os.path.split(target_filepath)
    bwd2_stemname, bwd2_ext = os.path.splitext(bwd2_basename)
    bwd2_ext = bwd2_ext.lower()

    # Determine the name of the model
    model_name = settings.get_model_name()
    if model_name is None:
        model_name = bwd2_stemname
    print(f"Model Name: {model_name}")

    # Get file type (vdf or sdf)
    is_vdf = False
    if bwd2_ext == ".vdf":
        is_vdf = True
    elif bwd2_ext == ".sdf":
        is_vdf = False
    else:
        raise UnsupportedFileTypeError(
            f"Filetype {bwd2_ext} does not have the right extension (.vdf/.sdf)"
        )
    print(f"Is VDF: {is_vdf}")

    # Create the intermediary model
    imodel = InterModel(settings)
    imodel.name = model_name
    imodel.material_filename = model_name + settings.get_material_suffix()
    print(f"Material Filename: {imodel.material_filename}")

    if settings.person_enabled():
        imodel.is_pilot = True
    elif settings.person_disabled():
        imodel.is_pilot = False
    else:
        imodel.is_pilot = model_name[1] == "s"
    print(f"Is Pilot: {imodel.is_pilot}")

    if settings.person_anims_enabled():
        imodel.is_skeletally_animated = True
    elif settings.person_anims_disabled():
        imodel.is_skeletally_animated = False
    else:
        imodel.is_skeletally_animated = imodel.is_pilot
    print(f"Is Skeletally Animated: {imodel.is_skeletally_animated}")

    if settings.scope.scope_enabled():
        imodel.use_scope = True
    elif settings.scope.scope_disabled():
        imodel.use_scope = False
    else:
        imodel.use_scope = imodel.is_pilot
    print(f"Use Scope: {imodel.use_scope}")

    # Load the BWD2 from file
    with open(target_filepath, "rb") as stream:
        if is_vdf:
            bwd2 = VDFSerializer(stream).read()
        else:
            bwd2 = SDFSerializer(stream).read()

    # Load all relevant BWD2 objects and geos into the imodel
    print("Loading objects")
    for obj in bwd2.primary_objects():
        imodel.load_from_object(bwd2, obj, asset_resolver)
    if is_vdf:
        for obj in bwd2.cockpit_objects():
            imodel.load_from_object(bwd2, obj, asset_resolver)

    imodel.register_anims(bwd2)

    # Establish parent/child relationships among the intermediary objects
    imodel.init_iobject_hierarchy()

    # Initialize intermediary object abs transforms
    imodel.generate_abs_transforms()

    # Transform geometry from geo-relative space to model-relative space
    imodel.process_geometry()

    # Identify if separate cockpit files are required by examining animation data
    # Note: This works by checking if any objects are transformed differently across time or across animations.
    #    It does not currently compare against the object's given transform - only animated positions/rotations.
    if settings.separate_cockpit_auto():
        imodel.use_cockpit = False
        has_turret_rotator = any(
            iobject.class_id == ClassID.TURRET_GEOMETRY
            for iobject in imodel.iobject_list
        )
        has_cockpit_payload = any(
            iobject.geometry_cockpit is not None for iobject in imodel.iobject_list
        )
        has_eyepoint = any(iobject.is_eyepoint for iobject in imodel.iobject_list)
        if (
            is_vdf
            and has_turret_rotator
            and has_cockpit_payload
            and has_eyepoint
            and not settings.turret_disabled()
        ):
            print("Turret cockpit detected; forcing separate cockpit Redux output")
            imodel.use_cockpit = True

        for iobject in imodel.iobject_list:
            if imodel.use_cockpit:
                break
            if iobject.geometry_cockpit is None and not iobject.is_eyepoint:
                continue  # NO COCKPIT AND NOT EYEPOINT

            if iobject.anim_translations is None:
                pass  # NO TRANSLATIONS
            else:
                if len(iobject.anim_translations) < 2:
                    pass  # TO FEW TRANSLATIONS
                else:
                    t = iobject.anim_translations[0].translation
                    for tkf in iobject.anim_translations:
                        t2 = tkf.translation
                        if t != t2:
                            imodel.use_cockpit = True
                            break  # TRANSLATION CHANGE
                    if imodel.use_cockpit:
                        break

            if iobject.anim_rotations is None:
                pass  # NO ROTATIONS
            else:
                if len(iobject.anim_rotations) < 2:
                    pass  # TO FEW ROTATIONS
                else:
                    r = iobject.anim_rotations[0].rotation
                    for rkf in iobject.anim_rotations:
                        r2 = rkf.rotation
                        if r != r2:
                            imodel.use_cockpit = True
                            break  # ROTATION CHANGE
                    if imodel.use_cockpit:
                        break

    elif settings.separate_cockpit_enabled():
        imodel.use_cockpit = True
    elif settings.separate_cockpit_disabled():
        imodel.use_cockpit = False
    else:
        raise InvalidSettingError(f"Invalid cockpit setting")

    print(f"Seperate cockpit files: {imodel.use_cockpit}")

    # Establish parent/child relationships among the intermediary bones
    imodel.init_ibone_hierarchy_from_iobjects()

    # Set ibone transformations
    for ibone in imodel.ibone_list:
        if ibone.iobject is not None:
            if ibone.iobject.no_bone_transform:
                ibone.set_transform(Transform())
            else:
                if ibone.iobject.bone_cockpit == ibone:
                    if imodel.use_cockpit:
                        ibone.set_transform(Transform())
                    else:
                        ibone.set_transform(ibone.iobject.abs_transform)
                else:
                    ibone.set_transform(ibone.iobject.transform)

    imodel.process_animations()

    if imodel.is_skeletally_animated:
        # Add animations for export
        imodel.load_animation_by_index(
            PERSON_ANIM_CROUCHING, "fireRecoilSniper", 1.0 / 30.0
        )
        imodel.load_animation_by_index(PERSON_ANIM_STANDING, "idle", 1.5)
        imodel.load_animation_by_index(
            PERSON_ANIM_IDLE_PARACHUTE, "idleParachute", 59.0 / 30.0
        )
        imodel.load_animation_by_index(PERSON_ANIM_JUMP, "jump", 1.2)
        imodel.load_animation_by_index(
            PERSON_ANIM_LAND_PARACHUTE, "landParachute", 7.0 / 6.0
        )
        imodel.load_animation_by_index(PERSON_ANIM_FALL, "death1", 1.0)
        imodel.load_animation_by_index(PERSON_ANIM_RUN_FORWARD, "runForward", 0.63)
        imodel.load_animation_by_index(PERSON_ANIM_RUN_BACKWARD, "runBackward", 0.9)
        imodel.load_animation_by_index(PERSON_ANIM_STRAFE_LEFT, "runLeft", 0.9)
        imodel.load_animation_by_index(PERSON_ANIM_STRAFE_RIGHT, "runRight", 0.9)
        imodel.load_animation_by_index(
            PERSON_ANIM_CROUCH, "stand2Kneel", 1.0, 29.0 / 30.0
        )
        imodel.load_animation_by_index(
            PERSON_ANIM_STAND, "kneel2stand", 1.0, 29.0 / 30.0
        )

    # Determine if we should check for a special "scope" texture
    use_geometry_scope_type = settings.scope.type_geometry()

    scope_tex = None
    check_scope_tex = False
    if imodel.use_scope and (
        settings.scope.type_auto() or settings.scope.type_geometry()
    ):
        tex = settings.scope.texture
        if tex is not None:
            scope_tex = tex.lower()
            check_scope_tex = True

    # Establish geometry groups (corresponds to submeshes)
    for gtype, iobject, igeom in imodel.iobject_igeoms():
        if gtype == "cockpit":
            super_name = "BZBaseCockpit"
        else:
            super_name = "BZBase"
        if (
            igeom.tex_name is None
            or settings.only_flat_colors_enabled()
            or not _texture_source_exists(asset_resolver, igeom.tex_name)
        ):
            print(
                f"Use flat colors: {igeom.name}: {igeom.tex_name}, {settings.only_flat_colors_enabled()}"
            )
            imodel.use_flat_colors = True
            imodel.flat_igeom_list.append(igeom)
            imodel.flat_name = f"{imodel.name.lower()}_flat"
            tex_name = imodel.flat_name
        else:
            tex_name = igeom.tex_name.lower()

        if check_scope_tex and tex_name == scope_tex:
            use_geometry_scope_type = True
            name = "scope"
            print(f"Add {igeom.name} to scope group {name}:")
            imodel.group_igeom(
                igeom=igeom,
                name=name,
                mat_name=name,
            )
        else:
            name = super_name + "_" + tex_name
            mat_info = imodel.create_material(
                name=name,
                super_name=super_name,
                tex_name=tex_name,
            )
            print(f"Add {igeom.name} to group {name}:")
            imodel.group_igeom(
                igeom=igeom,
                name=name,
                mat_name=mat_info.name,
            )

    use_attached_scope_type = settings.scope.type_attached()
    use_fixed_scope_type = settings.scope.type_fixed() or (
        settings.scope.type_auto() and not use_geometry_scope_type
    )

    if imodel.use_scope:
        if settings.scope.type_auto():
            print(f"Auto Scope")

        if use_fixed_scope_type:
            print(f"Fixed Scope")
            imodel.scope_info = FixedScopeInfo()
            if settings.scope.position_american():
                imodel.scope_info.set_transform_american()
            elif settings.scope.position_soviet():
                imodel.scope_info.set_transform_soviet()
            elif settings.scope.position_auto():
                if model_name[0] == "s":
                    imodel.scope_info.set_transform_soviet()
                else:
                    imodel.scope_info.set_transform_american()
            else:
                imodel.scope_info.set_fixed_transform(*settings.scope.screen)
        elif use_attached_scope_type:
            print(f"Attached Scope")
            imodel.scope_info = AttachedScopeInfo()
            imodel.scope_info.gun_name = settings.scope.gun_name
            imodel.scope_info.transform = settings.scope.transform
        elif use_geometry_scope_type:
            print(f"Geometry Scope")
            imodel.scope_info = GeometryScopeInfo()
        else:
            raise InvalidSettingError("Invalid scope type")

    # Create flat color images and UVs if applicable
    if imodel.use_flat_colors:
        # Gather the colors
        color_set = set()
        for igeom in imodel.flat_igeom_list:
            color3_array = igeom.vertex_array["color"][:, 1:]
            color_set.update((tuple(c) for c in color3_array))

        color_list = tuple(color_set)
        color_map = {c: i for i, c in enumerate(color_list)}
        color_count = len(color_list)

        # Set the UVs to point at the colors
        for igeom in imodel.flat_igeom_list:
            color3_array = igeom.vertex_array["color"][:, 1:]
            uv_array = igeom.vertex_array["uv"]
            for i, (c, uv) in enumerate(zip(color3_array, uv_array)):
                ci = color_map[tuple(c)]
                # [*uv] = (ci + 0.5)/color_count, 0.5
                uv[0] = (ci + 0.5) / color_count
                uv[1] = 0.5

        # Generate the flat color texture
        imodel.flat_rgba = _flat_color_rgba(color_list)

        # ------------------#
    #    Create Extra    #
    # ------------------#

    # -----------------------------#
    # Create headlight ibones
    if settings.headlights_enabled():
        for i, iobject in enumerate(imodel.headlight_mask_list):
            ibone = imodel.create_ibone(
                name="HLGT" + str(i) + "_ff0000",
            )
            ibone.set_transform(iobject.abs_transform)

    # -----------------------------#
    # Create the sniper scope if applicable
    if imodel.use_scope:
        if use_geometry_scope_type:
            pass

        else:
            print("Creating sniper scope")
            igeom = imodel.create_igeom("scope")
            s0 = 0.001
            s1 = 1.0 - s0
            igeom.vertex_array = np.array(
                [
                    ((-1, -1, 0), (0, 0, 1), (0xFF, 0x00, 0x00, 0x00), (s0, s1)),
                    ((1, -1, 0), (0, 0, 1), (0xFF, 0x00, 0x00, 0x00), (s1, s1)),
                    ((1, 1, 0), (0, 0, 1), (0xFF, 0x00, 0x00, 0x00), (s1, s0)),
                    ((-1, 1, 0), (0, 0, 1), (0xFF, 0x00, 0x00, 0x00), (s0, s0)),
                ],
                dtype=[
                    ("pos", "<3f"),
                    ("normal", "<3f"),
                    ("color", "4B"),
                    ("uv", "<2f"),
                ],
            )

            igeom.tri_array = np.array(
                [(0, 1, 2), (0, 2, 3)],
                dtype="<3I",
            )

            ibone = imodel.create_ibone(
                name=igeom.name,
                type="cockpit",
            )
            ibone.set_geometry(igeom)

            if use_fixed_scope_type:
                # For some reason, if you copy all pov animations to the scope
                #   it will slightly jiggle on screen when you move.
                # I suspect there might be a one frame delay between the camera's
                #   position update due to the animation and the scope's position
                #   update, causing you to see the scope in the current location
                #   from the previous camera perspective, or vice versa.

                pov_ibone = imodel.get_pov_ibone()
                if pov_ibone is None:
                    raise POVNotFoundError("No POV found")
                parent_ibone = pov_ibone.parent

                for animname in imodel.ianim_name_map:
                    ianim = imodel.ianim_name_map[animname]
                    if pov_ibone not in ianim.ibone_tracks:
                        continue

                    # Copy the pov anims to the scope so the
                    #   scope animates the same as the pov
                    pov_track = ianim.ibone_tracks[pov_ibone]
                    scope_track = pov_track.copy()

                    # The POV x-axis rotation was flipped to fix the camera
                    #   up/down issue so we have to flip this copy back to normal
                    scope_track.flip_pitch()

                    if animname == "fireRecoilSniper":
                        # When fully crouched, shift the
                        #   scope forward into view
                        t = Vector3(
                            0,
                            0,
                            imodel.scope_info.scope_z + imodel.scope_info.behind_dist,
                        )
                        for kf in scope_track:
                            kf.translation.translate(t.copy().rotate(kf.rotation))
                    ianim.ibone_tracks[ibone] = scope_track

                transform = imodel.scope_info.get_transform()

                if pov_ibone is not None:
                    transform.rotate(pov_ibone.get_absolute_orientation()).translate(
                        pov_ibone.get_absolute_position()
                    )
                igeom.transform_geometry(transform)

                ibone.position.set_vector3(pov_ibone.position)
                ibone.orientation.set_quaternion(pov_ibone.orientation)
                if parent_ibone is not None:
                    parent_ibone.add_child(ibone)

            elif use_attached_scope_type:
                if imodel.scope_info.gun_name is None:
                    parent_ibone = None
                else:
                    parent_ibone = imodel.get_ibone_by_name(imodel.scope_info.gun_name)

                transform = imodel.scope_info.transform.copy()
                if parent_ibone is not None:
                    transform.rotate(parent_ibone.get_absolute_orientation()).translate(
                        parent_ibone.get_absolute_position()
                    )
                igeom.transform_geometry(transform)
                if parent_ibone is not None:
                    parent_ibone.add_child(ibone)

            print(f"Add {igeom.name} to group {name}:")
            imodel.group_igeom(
                igeom=igeom,
                name="scope",
                mat_name="scope",
            )
    print("Read complete.")

    # ------------------------#
    # Construct the OGRE model #
    # ------------------------#

    mesh_primary, skeleton_primary = build_ogre(imodel, type="primary")
    if imodel.use_cockpit:
        mesh_cockpit, skeleton_cockpit = build_ogre(imodel, type="cockpit")

        # ------------------#
    #     File Write     #
    # ------------------#
    suppress_write = settings.suppress_write()

    print("Writing texture files")
    # Write texture files
    write_textures(
        imodel,
        asset_resolver=asset_resolver,
        suppress_write=suppress_write,
    )

    # Write material file
    write_material(
        imodel,
        asset_resolver=asset_resolver,
        suppress_write=suppress_write,
    )

    # Write the OGRE mesh and skeleton files
    print("Writing mesh and skeleton files")
    write_ogre(
        mesh_primary,
        skeleton_primary,
        mesh_filename=imodel.name + ".mesh",
        asset_resolver=asset_resolver,
        suppress_write=suppress_write,
    )
    if imodel.use_cockpit:
        suffix = imodel.get_suffix()
        write_ogre(
            mesh_cockpit,
            skeleton_cockpit,
            mesh_filename=imodel.name + suffix + ".mesh",
            asset_resolver=asset_resolver,
            suppress_write=suppress_write,
        )

    print("Port complete")


def write_material(imodel, asset_resolver, suppress_write):
    material_filename = imodel.material_filename + ".material"
    material_filepath = asset_resolver.get_output_material_path(material_filename)
    if material_filepath is not None:
        print(f"Writing material file: {imodel.material_filename}")
        try:
            if suppress_write:
                for mat_info in imodel.get_materials():
                    print(
                        f"Material: {mat_info.name}, {mat_info.super_name}, {mat_info.tex_name}"
                    )
                print(f"*File write suppressed* {material_filepath}")
            else:
                material_text = 'import * from "BZBase.material"\n\n'
                for mat_info in imodel.get_materials():
                    print(
                        f"Material: {mat_info.name}, {mat_info.super_name}, {mat_info.tex_name}"
                    )
                    material_text += create_material_string(
                        mat_info.name, mat_info.super_name, mat_info.tex_name
                    )
                written = asset_resolver.write_text_if_changed(
                    material_filepath,
                    material_text,
                    label="material",
                )
                if written:
                    print(f"Written to {material_filepath}")
        except OSError:
            print(
                f"Failed to write material file {material_filepath} for material {material_filename}"
            )
            print(traceback.format_exc())
    else:
        print(f"Skipping material file: {imodel.material_filename}")


def _png_to_rgba_bytes(png_path):
    """
    Load a PNG via Blender's image API and return (width, height, rgba_bytes).

    Returns (None, None, None) if Blender is unavailable or loading fails.
    """
    try:
        import bpy
    except ImportError:
        print(
            f"[bzrmodelporter] Blender 'bpy' module not available; "
            f"cannot load PNG {png_path}"
        )
        return None, None, None

    print(f"[bzrmodelporter] Loading PNG via Blender: {png_path}")
    try:
        img = bpy.data.images.load(str(png_path))
    except Exception as e:
        print(f"[bzrmodelporter] Failed to load PNG '{png_path}': {e}")
        return None, None, None

    try:
        width, height = img.size
        # img.pixels is a flat sequence of floats [R, G, B, A, R, G, B, A, ...]
        pixels = list(img.pixels)
        expected_len = width * height * 4
        if len(pixels) != expected_len:
            print(
                f"[bzrmodelporter] Unexpected pixel length for '{png_path}': "
                f"{len(pixels)} (expected {expected_len})"
            )
            return None, None, None

        # Blender stores rows bottom-up; DDS (and the .map/PIL paths) are
        # top-down, so flip the rows or the texture lands upside down on
        # the exported UVs.
        arr = np.asarray(pixels, dtype=np.float32).reshape(height, width, 4)[::-1]
        buf = np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)

        return width, height, buf.tobytes()

    finally:
        # Clean up the temporary image from the .blend
        img.user_clear()
        bpy.data.images.remove(img)


def write_textures(imodel, asset_resolver, suppress_write):
    print("Writing texture files")
    if suppress_write:
        print("  [nowrite] Skipping texture export")
        return

    for tex_name in imodel.get_textures():
        if not tex_name:
            continue

        base_name = os.path.splitext(tex_name)[0]
        dds_name = base_name + "_D.dds"
        dds_path = asset_resolver.get_output_texture_path(dds_name)

        print(f"tex_name: {tex_name}")
        print(f"Texture: {base_name} -> {dds_name}")

        if dds_path is None:
            print(f"  [skip] Output path disabled or already exists for {dds_name}")
            continue

        # ----------------------------------------------------
        # 1) Prefer existing PNG (no ACT / palette needed)
        # ----------------------------------------------------
        png_basename = base_name + ".png"
        png_path = asset_resolver.get_resource_path(png_basename)
        if png_path is not None:
            print(f"  Using existing PNG '{png_path}' for '{tex_name}'")
            width, height, rgba_bytes = _png_to_rgba_bytes(png_path)
            if width is not None and rgba_bytes is not None:
                asset_resolver.write_bytes_if_changed(
                    dds_path,
                    _dds_uncompressed_rgba_bytes(width, height, rgba_bytes),
                    label="texture",
                )
                continue
            else:
                print(
                    f"  Failed to load PNG '{png_path}', "
                    f"falling back to .map if available."
                )

        # ----------------------------------------------------
        # 2) Flat-color synthetic texture (if present)
        # ----------------------------------------------------
        if (
            getattr(imodel, "flat_name", None) == tex_name
            and getattr(imodel, "flat_rgba", None) is not None
        ):
            width, height, rgba_bytes = imodel.flat_rgba
            asset_resolver.write_bytes_if_changed(
                dds_path,
                _dds_uncompressed_rgba_bytes(width, height, rgba_bytes),
                label="texture",
            )
            continue

        # ----------------------------------------------------
        # 3) Fallback: original .map → RGBA path (needs ACT for INDEXED)
        map_filepath = asset_resolver.get_map_path(base_name)
        if map_filepath is None:
            print(f"  Could not find map file for texture '{tex_name}'")
            continue

        print(f"  Loading .map: {map_filepath}")

        try:
            width, height, rgba_bytes = _bzmap_to_rgba_bytes(
                map_filepath,
                asset_resolver,
                map_dir=os.path.dirname(map_filepath),
            )
        except Exception as exc:
            print(
                f"  Failed to convert bzmap to RGBA bytes: "
                f"{map_filepath} ({tex_name})"
            )
            print(f"    {exc}")
            continue

        if width is None or rgba_bytes is None:
            print(
                f"  No RGBA data for {map_filepath} ({tex_name}); "
                f"skipping DDS write"
            )
            continue

        asset_resolver.write_bytes_if_changed(
            dds_path,
            _dds_uncompressed_rgba_bytes(width, height, rgba_bytes),
            label="texture",
        )


def port_geo(target_filepath, asset_resolver, settings):
    print(f"Porting geo mesh {target_filepath}")

    # Extract information from the bwd2 file path
    geo_stemname = target_filepath.stem

    # Determine the name of the model
    model_name = settings.get_model_name()
    if model_name is None:
        model_name = geo_stemname
    print(f"Model Name: {model_name}")

    # Create the intermediary model
    imodel = InterModel(settings)
    imodel.name = model_name
    imodel.material_filename = model_name + settings.get_material_suffix()
    print(f"Material Filename: {imodel.material_filename}")

    # Load the geo from file
    with open(target_filepath, "rb") as stream:
        geo = GeoSerializer(stream).read()

    # Create the intermediary geometry object
    igeom = None
    if len(geo.face_list) > 0:
        igeom = imodel.create_igeom_from_geo(
            name=imodel.name,
            geo=geo,
        )

    # Build intermediary bone
    ibone = imodel.create_ibone(name=imodel.name)
    ibone.set_geometry(igeom)

    # Establish geometry groups (corresponds to submeshes)
    if igeom is not None:
        super_name = "BZBase"
        if (
            igeom.tex_name is None
            or settings.only_flat_colors_enabled()
            or not _texture_source_exists(asset_resolver, igeom.tex_name)
        ):
            print(
                f"Use flat colors: {igeom.name}: {igeom.tex_name}, {settings.only_flat_colors_enabled()}"
            )
            imodel.use_flat_colors = True
            imodel.flat_igeom_list.append(igeom)
            imodel.flat_name = f"{imodel.name.lower()}_flat"
            tex_name = imodel.flat_name
        else:
            tex_name = igeom.tex_name.lower()
        name = super_name + "_" + tex_name
        mat_info = imodel.create_material(
            name=name,
            super_name=super_name,
            tex_name=tex_name,
        )
        print(f"Add {igeom.name} to group {name}:")
        imodel.group_igeom(
            igeom=igeom,
            name=name,
            mat_name=mat_info.name,
        )

        # Create flat color images and UVs if applicable
        if imodel.use_flat_colors:
            # Gather the colors
            color_set = set()
            for igeom in imodel.flat_igeom_list:
                color3_array = igeom.vertex_array["color"][:, 1:]
                color_set.update((tuple(c) for c in color3_array))

            color_list = tuple(color_set)
            color_map = {c: i for i, c in enumerate(color_list)}
            color_count = len(color_list)

            # Set the UVs to point at the colors
            for igeom in imodel.flat_igeom_list:
                color3_array = igeom.vertex_array["color"][:, 1:]
                uv_array = igeom.vertex_array["uv"]
                for i, (c, uv) in enumerate(zip(color3_array, uv_array)):
                    ci = color_map[tuple(c)]
                    # [*uv] = (ci + 0.5)/color_count, 0.5
                    uv[0] = (ci + 0.5) / color_count
                    uv[1] = 0.5

            # Generate the flat color texture
            imodel.flat_rgba = _flat_color_rgba(color_list)

        # ------------------------#
    # Construct the OGRE model #
    # ------------------------#
    mesh_primary, skeleton_primary = build_ogre(imodel, type="primary")

    # ------------------#
    #     File Write     #
    # ------------------#
    suppress_write = settings.suppress_write()

    print("Writing texture files")
    # Write texture files
    write_textures(
        imodel,
        asset_resolver=asset_resolver,
        suppress_write=suppress_write,
    )

    # Write material file
    write_material(
        imodel,
        asset_resolver=asset_resolver,
        suppress_write=suppress_write,
    )

    # Write the OGRE mesh and skeleton files
    print("Writing mesh and skeleton files")
    write_ogre(
        mesh_primary,
        skeleton_primary,
        mesh_filename=imodel.name + ".mesh",
        asset_resolver=asset_resolver,
        suppress_write=suppress_write,
    )

    print("Port complete")


def port_map(filepath, asset_resolver, settings):
    """
    Standalone .map -> uncompressed DDS (A8R8G8B8) converter.

    This uses the same BZMap decoding as the main pipeline and the
    pure-Python _bzmap_to_rgba_bytes / _write_dds_uncompressed_rgba helpers,
    so it does not depend on Pillow.
    """
    tex_name = filepath.stem  # name without .map
    map_filepath = asset_resolver.get_map_path(tex_name)
    if map_filepath is None:
        print(f"Skipping texture: {tex_name} (no .map found)")
        return

    output_filename = tex_name + "_D.dds"
    output_filepath = asset_resolver.get_output_texture_path(output_filename)
    if output_filepath is None:
        print(f"Skipping texture: {tex_name} (no output path)")
        return

    print(f"Texture: {tex_name}")
    try:
        bzmap = BZMap()
        map_serializer = BZMapSerializer()
        with open(map_filepath, "rb") as stream:
            map_serializer.deserialize(stream, bzmap)
    except OSError:
        print(f"Failed to read .map texture file {map_filepath} for texture {tex_name}")
        print(traceback.format_exc())
        return

    width, height, rgba = _bzmap_to_rgba_bytes(
        bzmap,
        asset_resolver,
        map_dir=os.path.dirname(map_filepath),
    )

    if width is None or rgba is None:
        print(f"Failed to convert bzmap to RGBA bytes: {map_filepath} ({tex_name})")
        return

    if settings.suppress_write():
        print(f"*File write suppressed* {output_filepath}")
    else:
        written = asset_resolver.write_bytes_if_changed(
            output_filepath,
            _dds_uncompressed_rgba_bytes(width, height, rgba),
            label="texture",
        )
        if written:
            print(f"Written to {output_filepath}")
