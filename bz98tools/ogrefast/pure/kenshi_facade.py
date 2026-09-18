from __future__ import annotations

"""Drop-in pure-Python surface for ``kenshi_blender_tool``.

The historical Blender importer/exporter uses ``from kenshi_blender_tool import *``.
This module deliberately mirrors that public surface closely enough that those
collectors do not need to know whether they are backed by the old CPython
extension or the new pure serializers.
"""

import math
from enum import IntEnum

from .animation_compat import AnimatedSkeletonData, AnimationData, BlenderAnimationTrack
from .kenshi_compat import (
    BoneAssignmentData,
    BoneData,
    GeometryData,
    Matrix3,
    MeshVersion,
    OgreQuaternion,
    OperationType,
    SkeletonVersion,
    Vector3,
)
from .pose_compat import MeshData, SubMeshData
from .rigged_import_compat import (
    KenshiObjectSerializer as RiggedImportKenshiObjectSerializer,
    VertexGroupData,
)


class KenshiObjectSerializer(RiggedImportKenshiObjectSerializer):
    """One serializer object supporting mesh + skeleton import/export."""

    is_pure_python = True

    def create_mesh(self, filename):
        # Use the pose-aware export model. ``load_mesh`` is inherited from the
        # rigged importer and ``create_skeleton``/skeleton I/O from its parent.
        return MeshData(filename, "General")


SkeletonData = AnimatedSkeletonData


class CollisionMesh:
    def __init__(self):
        self.points = []
        self.triangles = []


class KenshiPhysXSerializer:
    """Explicit placeholder for the native PhysX-only part of the old module."""

    def _unsupported(self, *args, **kwargs):
        raise NotImplementedError(
            "the pure kenshi_blender_tool facade does not implement PhysX collision serialization"
        )

    export_convex_hull = _unsupported
    export_triangle_mesh = _unsupported
    import_convex_hull = _unsupported
    import_triangle_mesh = _unsupported


class Radian(float):
    def __new__(cls, r=0.0):
        return float.__new__(cls, float(r))

    def valueRadians(self):
        return float(self)


def _quaternion_to_angle_axis(self):
    """Match Ogre::Quaternion::ToAngleAxis for importer compatibility.

    Skeleton quaternions are expected to be unit length, but normalising here
    makes the pure facade robust to small serializer/float drift and mirrors the
    practical behaviour the Blender importer expects from the native helper.
    """

    w = float(self.w)
    x = float(self.x)
    y = float(self.y)
    z = float(self.z)
    length = math.sqrt(w * w + x * x + y * y + z * z)
    if length > 1.0e-12:
        inv_length = 1.0 / length
        w *= inv_length
        x *= inv_length
        y *= inv_length
        z *= inv_length
    else:
        w, x, y, z = 1.0, 0.0, 0.0, 0.0

    w = max(-1.0, min(1.0, w))
    sqr_axis = x * x + y * y + z * z
    if sqr_axis > 1.0e-12:
        inv_axis = 1.0 / math.sqrt(sqr_axis)
        axis = Vector3(x * inv_axis, y * inv_axis, z * inv_axis)
        angle = 2.0 * math.acos(w)
    else:
        # Ogre chooses the X axis for the identity/zero-angle case.
        axis = Vector3(1.0, 0.0, 0.0)
        angle = 0.0
    return Radian(angle), axis


# The compiled module exposes this method on OgreQuaternion.  The compatibility
# class lives in kenshi_compat because it is also used by the wire adapters, so
# bind the higher-level helper here where Radian is part of the public facade.
OgreQuaternion.to_angle_axis = _quaternion_to_angle_axis


class SkeletonAnimationBlendMode(IntEnum):
    average = 0
    cumulative = 1


# Match the convenience constants exported by the compiled module.  The
# native stub exposes V_1_0/V_1_8/V_Latest as skeleton-version constants and
# the remaining historical values as mesh-version constants.
V_1_0 = SkeletonVersion.V_1_0
V_1_10 = MeshVersion.V_1_10
V_1_4 = MeshVersion.V_1_4
V_1_7 = MeshVersion.V_1_7
V_1_8 = SkeletonVersion.V_1_8
V_Latest = SkeletonVersion.V_Latest
V_Legacy = MeshVersion.V_Legacy
triangle_list = OperationType.triangle_list
average = SkeletonAnimationBlendMode.average
cumulative = SkeletonAnimationBlendMode.cumulative

# Marker used by the backend probe so a fallback module is never mistaken for
# a newly available native extension in the same interpreter session.
__bz98_pure_backend__ = True

__all__ = [
    "AnimationData",
    "BlenderAnimationTrack",
    "BoneAssignmentData",
    "BoneData",
    "CollisionMesh",
    "GeometryData",
    "KenshiObjectSerializer",
    "KenshiPhysXSerializer",
    "Matrix3",
    "MeshData",
    "MeshVersion",
    "OgreQuaternion",
    "OperationType",
    "Radian",
    "SkeletonAnimationBlendMode",
    "SkeletonData",
    "SkeletonVersion",
    "SubMeshData",
    "V_1_0",
    "V_1_10",
    "V_1_4",
    "V_1_7",
    "V_1_8",
    "V_Latest",
    "V_Legacy",
    "Vector3",
    "VertexGroupData",
    "average",
    "cumulative",
    "triangle_list",
]
