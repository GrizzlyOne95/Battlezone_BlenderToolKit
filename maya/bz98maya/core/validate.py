"""Run the toolkit's own legacy-export validation on a neutral scene (Maya-free).

``bz98tools/validation.py`` is bpy-free and duck-typed: it only reads attributes of
Blender-like objects (``name``, ``type``, ``parent``, ``data.vertices`` /
``polygons`` / ``materials``, ``GEOPropertyGroup``, ``matrix_world`` ...). This
module builds such objects from the neutral scene, so every check Blender runs is
the same code here, with the same wording. Geometry is presented in Blender's
axes (Z up, nose +Y), which is what the checks assume.

Deliberate differences from running inside Blender:

* Animation checks are left out (scope ``Animation`` / ``Armature``): animation
  is a later tier.
* A node without a mesh whose GEO type is one of the invisible classes
  (38, 40, 70-77: hardpoints, eyepoint, emitters) is presented as an eyepoint-style
  helper empty. Blender authors hardpoints as meshes; in Maya they are locators,
  and stock VDFs carry them without any .geo.
* VLOC and damage-band stores are empty, so their informational notes are not shown.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import numpy as np

from . import authoring, axes, schema
from .model import Node, Scene
from .toolkit import ensure_toolkit

SKIPPED_SCOPES = {"Animation", "Armature"}
ORIENTATION_MESSAGE = (
    "Legacy VDF/SDF/GEO vehicle export expects the nose/front toward Maya -Z (Battlezone +Z). "
    "Direct Redux .mesh export uses the same Maya axes: nose toward -Z, Y up."
)
# Maya (x, y, z) -> Blender (x, -z, y): Maya Y-up right handed to Blender Z-up right handed.
_T = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
_T4 = np.identity(4)
_T4[:3, :3] = _T


class Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def array(self):
        return np.array([self.x, self.y, self.z])

    @property
    def length(self):
        return float(np.linalg.norm(self.array()))

    def normalized(self):
        n = self.length
        return Vec(*(self.array() / n)) if n > 0 else Vec(0, 0, 0)

    def __sub__(self, other):
        return Vec(*(self.array() - other.array()))

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __getitem__(self, i):
        return (self.x, self.y, self.z)[i]

    def __matmul__(self, matrix):  # row vector times matrix, as mathutils does
        return Vec(*(self.array() @ matrix.m))


class Mat3:
    def __init__(self, m):
        self.m = np.array(m, dtype=float).reshape(3, 3)

    def to_3x3(self):
        return self


class Quat:
    def __init__(self, rotation):
        r = np.array(rotation, dtype=float)
        scale = np.cbrt(abs(np.linalg.det(r))) or 1.0
        r = r / scale
        t = np.trace(r)
        if t > 0:
            s = np.sqrt(t + 1.0) * 2
            w, x, y, z = 0.25 * s, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s
        else:
            i = int(np.argmax(np.diag(r)))
            j, k = (i + 1) % 3, (i + 2) % 3
            s = np.sqrt(1.0 + r[i, i] - r[j, j] - r[k, k]) * 2
            q = [0.0] * 4
            q[0] = (r[k, j] - r[j, k]) / s
            q[i + 1] = 0.25 * s
            q[j + 1] = (r[j, i] + r[i, j]) / s
            q[k + 1] = (r[k, i] + r[i, k]) / s
            w, x, y, z = q
        self.q = np.array([w, x, y, z])

    def rotation_difference(self, other):
        a, b = self.q, other.q
        conj = np.array([a[0], -a[1], -a[2], -a[3]])
        w = conj[0] * b[0] - conj[1] * b[1] - conj[2] * b[2] - conj[3] * b[3]
        return SimpleNamespace(angle=float(2.0 * np.arccos(min(1.0, abs(w)))))


class Mat4:
    def __init__(self, m):
        self.m = np.array(m, dtype=float).reshape(4, 4)

    def to_translation(self):
        return Vec(*self.m[:3, 3])

    def to_quaternion(self):
        return Quat(self.m[:3, :3])

    def to_3x3(self):
        return Mat3(self.m[:3, :3])


def _blender_matrix(maya_matrix) -> np.ndarray:
    return _T4 @ np.array(maya_matrix, dtype=float) @ np.linalg.inv(_T4)


def _euler_xyz(r) -> List[float]:
    r = np.array(r, dtype=float)
    norms = np.linalg.norm(r, axis=0)
    r = r / np.where(norms > 0, norms, 1.0)
    sy = -r[2, 0]
    y = float(np.arcsin(max(-1.0, min(1.0, sy))))
    if abs(sy) < 0.999999:
        return [float(np.arctan2(r[2, 1], r[2, 2])), y, float(np.arctan2(r[1, 0], r[0, 0]))]
    return [float(np.arctan2(-r[1, 2], r[1, 1])), y, 0.0]


def _geo_props(node: Node, has_mesh: bool) -> SimpleNamespace:
    values, _extra = authoring.part_attr_values(node.props or {})
    ns = SimpleNamespace()
    for fld in schema.part_fields():
        value = values[fld.attr]
        if fld.children:
            for child, component in zip(fld.children, value):
                setattr(ns, child, component)
        elif fld.blender == "BoundsMode":
            setattr(ns, "BoundsMode", schema.BOUNDS_MODES[int(value)])
        elif fld.kind == "floatarray":
            setattr(ns, fld.blender, tuple(value))
        else:
            setattr(ns, fld.blender, value)
    if not has_mesh and int(ns.GEOType) in _invisible_types():
        ns.IsPOVHelper = True
    return ns


def _invisible_types():
    ensure_toolkit()
    from bz98tools import semantics

    return semantics.VEHICLE_INVISIBLE_CLASSES


def _mesh_data(node: Node):
    mesh = node.mesh
    materials: List[SimpleNamespace] = []
    index_of: Dict[str, int] = {}
    for name in mesh.face_materials:
        if name and name not in index_of:
            index_of[name] = len(materials)
            materials.append(
                SimpleNamespace(
                    name=name,
                    MaterialPropertyGroup=SimpleNamespace(MapTexture=name),
                    node_tree=None,
                )
            )
    vertices = [SimpleNamespace(co=Vec(*(_T @ np.array(p))), groups=[]) for p in mesh.positions]
    polygons = []
    for i, face in enumerate(mesh.faces):
        normal = _T @ np.array(axes.newell_normal([mesh.positions[v] for v in face]))
        polygons.append(
            SimpleNamespace(
                index=i,
                vertices=tuple(face),
                normal=Vec(*normal),
                material_index=index_of.get(mesh.face_materials[i], 0),
            )
        )
    return SimpleNamespace(vertices=vertices, polygons=polygons, materials=materials, shape_keys=None)


class BlenderLikeObject:
    def __init__(self, node: Node, world: np.ndarray):
        self.name = node.name
        has_mesh = node.mesh is not None and bool(node.mesh.positions)
        self.type = "MESH" if node.mesh is not None else "EMPTY"
        self.parent: Optional["BlenderLikeObject"] = None
        self.data = _mesh_data(node) if node.mesh is not None else None
        self.GEOPropertyGroup = _geo_props(node, has_mesh)
        local = _blender_matrix(node.matrix)
        self.matrix_world = Mat4(_blender_matrix(world))
        self.scale = tuple(float(v) for v in np.linalg.norm(local[:3, :3], axis=0))
        self.rotation_mode = "XYZ"
        self.rotation_euler = tuple(_euler_xyz(local[:3, :3]))
        self.animation_data = None
        self.modifiers: list = []
        self.constraints: list = []
        self.vertex_groups: list = []


def build_objects(scene: Scene) -> Dict[str, BlenderLikeObject]:
    by_name = {n.name: n for n in scene.nodes}
    worlds: Dict[str, np.ndarray] = {}

    def world(node: Node, depth: int = 0) -> np.ndarray:
        if node.name not in worlds:
            m = np.array(node.matrix, dtype=float)
            if node.parent in by_name and depth < 256:
                m = world(by_name[node.parent], depth + 1) @ m
            worlds[node.name] = m
        return worlds[node.name]

    objects = {n.name: BlenderLikeObject(n, world(n)) for n in scene.nodes}
    for node in scene.nodes:
        if node.parent in objects:
            objects[node.name].parent = objects[node.parent]
    return objects


class _FakeMathutils(types.ModuleType):
    """The one mathutils call validation.py makes (Matrix.Identity(3))."""

    class Matrix:
        @staticmethod
        def Identity(size):
            return Mat3(np.identity(size))


def validate_scene(
    scene: Scene,
    export_mode: str = "ALL",
    preset: str = "AUTO",
    active: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Issues exactly as Blender's ``collect_legacy_validation_issues`` would report them."""
    ensure_toolkit()
    from bz98tools import validation

    objects = build_objects(scene)
    context = SimpleNamespace(
        scene=SimpleNamespace(
            objects=list(objects.values()),
            AnimationCollection=[],
            bz_vloc_chunks=[],
            bz_damage_band_records=[],
        ),
        view_layer=SimpleNamespace(objects=SimpleNamespace(active=objects.get(active or ""))),
    )
    previous = sys.modules.get("mathutils")
    if previous is None:
        sys.modules["mathutils"] = _FakeMathutils("mathutils")
    try:
        issues = validation.collect_legacy_validation_issues(
            context, export_mode=export_mode, validation_preset=preset
        )
    finally:
        if previous is None:
            sys.modules.pop("mathutils", None)
    out = [i for i in issues if i.get("scope") not in SKIPPED_SCOPES]
    for issue in out:
        if issue.get("scope") == "Orientation":
            issue["message"] = ORIENTATION_MESSAGE
    return out


def counts(issues: List[Dict[str, Any]], export_mode: str = "ALL") -> Dict[str, int]:
    """Blender's ``_get_validation_counts``: issues that apply to ``export_mode``."""
    out = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for issue in issues:
        modes = issue.get("export_modes", {"ALL"})
        if "ALL" in modes or export_mode in modes:
            out[issue["severity"]] = out.get(issue["severity"], 0) + 1
    return out


def fix_legacy_name(name: str, existing: List[str]) -> str:
    """Blender's "Fix Legacy Name" (BZ98TOOLS_OT_fix_validation_name), as a pure function."""
    source = "".join(ch for ch in name.lower().replace(" ", "_") if ch.isalnum() or ch == "_") or "geo"
    lod = int(name[3]) if len(name) >= 4 and name[3] in "123" else 3
    min_len = max(5, min(8, len(source)))
    chars = list(source[:8].ljust(min_len, "x"))
    chars[3] = str(lod)
    chars[4] = "1"
    candidate = "".join(chars[:8])
    taken = {n.lower() for n in existing}
    if candidate.lower() in taken:
        base = candidate[:7]
        for suffix in "0123456789abcdefghijklmnopqrstuvwxyz":
            alt = (base + suffix)[:8]
            if alt.lower() not in taken:
                return alt
    return candidate


def quick_vehicle_specs():
    """Blender's QUICK_VEHICLE_GEO_SPECS: (suffix, geo type, label, (x, y, z) Blender-space, size)."""
    return [tuple(s) for s in (schema.blender_constant("QUICK_VEHICLE_GEO_SPECS") or [])]
