"""Neutral scene model shared by the VDF/GEO and Ogre .mesh paths.

All coordinates stored here are already in Maya space (Y up, right handed,
counter-clockwise front faces, outward normals, V up). See ``axes.py`` for
how the two Battlezone formats map into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]
UV = Tuple[float, float]

KIND_PART = "part"
KIND_HARDPOINT = "hardpoint"
KIND_LOD = "lod"
KIND_EMPTY = "empty"


@dataclass
class Mesh:
    positions: List[Vec3] = field(default_factory=list)
    # Each face is a tuple of indices into ``positions`` (CCW seen from outside).
    faces: List[Tuple[int, ...]] = field(default_factory=list)
    # One material / texture name per face ("" when unassigned).
    face_materials: List[str] = field(default_factory=list)
    # Per face-vertex ("loop") data, flattened in face order. None / [] when absent.
    normals: Optional[List[Vec3]] = None
    uv_sets: List[List[UV]] = field(default_factory=list)
    # Skin weights per position: [(bone id, weight), ...] (Redux skinned meshes), else None.
    weights: Optional[List[List[Tuple[int, float]]]] = None
    # Legacy GEO per-face words (unknown, shade, texture, xlu, parent, node), one list entry per face.
    face_data: Optional[Dict[str, List[int]]] = None

    def loop_count(self) -> int:
        return sum(len(face) for face in self.faces)


@dataclass
class Node:
    name: str
    parent: Optional[str] = None
    # 4x4, column-vector convention (translation in the last column), parent space.
    matrix: List[List[float]] = field(
        default_factory=lambda: [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    kind: str = KIND_PART
    mesh: Optional[Mesh] = None
    # VDF only: type, flags, geocenter, sphereradius, boxhalfheight, spin (see vdf_io).
    props: Dict[str, object] = field(default_factory=dict)
    # Legacy ANIM keys in Maya space: {"rot": [(frame, (w, x, y, z))], "pos": [(frame, xyz)], "scale": [...]}.
    anim: Optional[Dict[str, list]] = None


@dataclass
class Bone:
    """An Ogre bone: local TRS relative to its parent, which is also the bind pose."""

    name: str
    id: int
    parent: Optional[str] = None
    position: Vec3 = (0.0, 0.0, 0.0)
    orientation: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)  # w, x, y, z
    scale: Vec3 = (1.0, 1.0, 1.0)


@dataclass
class Track:
    """Keys of one bone in one clip. Values are Ogre's: offsets from the bind pose."""

    bone: str
    times: List[float] = field(default_factory=list)  # seconds from the clip start
    translations: List[Vec3] = field(default_factory=list)
    rotations: List[Tuple[float, float, float, float]] = field(default_factory=list)  # w, x, y, z
    scales: List[Vec3] = field(default_factory=list)


@dataclass
class Clip:
    name: str
    length: float = 0.0
    tracks: List[Track] = field(default_factory=list)


@dataclass
class Skeleton:
    name: str = ""
    bones: List[Bone] = field(default_factory=list)
    clips: List[Clip] = field(default_factory=list)
    blendmode: int = 0


@dataclass
class Scene:
    nodes: List[Node] = field(default_factory=list)
    # VDF only: vehicle header values and collision box (see vdf_io).
    meta: Dict[str, object] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    # Material name -> info dict (ambient / diffuse / specular / emissive RGB, texture, texture_path),
    # the same keys Blender's bCollectMaterialData fills; used for the .material file.
    materials: Dict[str, dict] = field(default_factory=dict)
    skeleton: Optional[Skeleton] = None

    def node_by_name(self, name: str) -> Optional[Node]:
        for node in self.nodes:
            if node.name == name:
                return node
        return None


def sequence_to_vec3(values: Sequence[float]) -> Vec3:
    return (float(values[0]), float(values[1]), float(values[2]))
