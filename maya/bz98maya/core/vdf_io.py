"""Legacy .vdf (+ .geo) <-> neutral scene. Maya-free.

Binary work is done by the toolkit: ``vdf_file.parse_vdf`` / ``serialize_vdf``
for the vehicle container and ``bzrmodelporter.bzgeo_serializer.GeoSerializer``
for the parts. This module only converts between those objects and the neutral
model (see ``axes.py`` for the coordinate conventions).

Per-part GEO / VDF / SDF fields live in ``Node.props`` (keys in ``schema.py``);
model header values in ``Scene.meta``. Data the neutral model has no field for is
kept as ``meta["preserved"]`` (base64): the section plan, unrecognised chunks, VLOC
chunks and damage-band records that are not simple name swaps.

Not round-tripped (dropped on read, rewritten with defaults on write): the ANIM
block (animation is a later tier), trailing bytes, per-face shade / texture /
translucency bytes, face colour (written 204,204,204), face parent / tree branch
(a single per-part default is kept instead), and the stored plane / plane
distance (recomputed from the winding).
"""

from __future__ import annotations

import base64
import os
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import anim as anim_io
from . import axes
from .model import (
    KIND_EMPTY,
    KIND_HARDPOINT,
    KIND_LOD,
    KIND_PART,
    Mesh,
    Node,
    Scene,
)
from .toolkit import ensure_toolkit

# GEO part classes that never load geometry on vehicles (semantics.VEHICLE_INVISIBLE_CLASSES).
HARDPOINT_TYPES = frozenset({38, 40, 70, 71, 72, 73, 74, 75, 76, 77})
SPINNER_TYPE = 15
EYEPOINT_TYPE = 40
DEFAULT_PART_TYPE = 60  # Blender's GEOType default (VEHICLE_GEOMETRY)
LOD_BANDS = (0, 4, 8)  # semantics.band_index(lod_slot, damage_state 0) for LOD1/2/3
BAND_COUNT = 28  # semantics.VGEO_BAND_COUNT
DEFAULT_FACE_COLOR = (204, 204, 204)
MAX_TEXTURE_CHARS = 8  # export_geo._derive_legacy_texture_name
INNER_COLLISION = "inner_col"
OUTER_COLLISION = "outer_col"
# Faces (as corner index quads) of a box whose corners are indexed x-major: i = 4x + 2y + z.
_BOX_QUADS = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))


def _modules():
    ensure_toolkit()
    from bz98tools import vdf_classes, vdf_file
    from bz98tools.bzrmodelporter import bzgeo, spacial
    from bz98tools.bzrmodelporter.bzgeo_serializer import GeoSerializer

    return vdf_classes, vdf_file, bzgeo, spacial, GeoSerializer


def _validation():
    ensure_toolkit()
    from bz98tools import validation

    return validation


def collision_helper_kind(name: str) -> Optional[str]:
    """"inner" / "outer" for the VDF collision helper meshes, else None."""
    lowered = (name or "").strip().lower()
    validation = _validation()
    if lowered in validation.INNER_COLLISION_NAMES:
        return "inner"
    if lowered in validation.OUTER_COLLISION_NAMES:
        return "outer"
    return None


def _find_case_insensitive(folder: str, filename: str) -> Optional[str]:
    exact = os.path.join(folder, filename)
    if os.path.isfile(exact):
        return exact
    try:
        wanted = filename.lower()
        for entry in os.listdir(folder):
            if entry.lower() == wanted:
                return os.path.join(folder, entry)
    except OSError:
        pass
    return None


# --- GEO <-> Mesh ----------------------------------------------------------

def geo_to_mesh(geo) -> Mesh:
    mesh = Mesh()
    positions = np.asarray(geo.vertex_pos_buffer, dtype=float).reshape(-1, 3)
    normals = np.asarray(geo.vertex_normal_buffer, dtype=float).reshape(-1, 3)
    mesh.positions = [axes.bz_point_to_maya(p) for p in positions]
    loop_normals = []
    loop_uvs = []
    for face in geo.face_list:
        nodes = face.wireframe
        if len(nodes) < 3:
            continue
        if any(
            n.vertex_index < 0
            or n.vertex_index >= len(positions)
            or n.vertex_normal_index < 0
            or n.vertex_normal_index >= len(normals)
            for n in nodes
        ):
            continue
        mesh.faces.append(tuple(int(n.vertex_index) for n in nodes))
        mesh.face_materials.append(face.texture_name or "")
        for n in nodes:
            loop_normals.append(axes.bz_normal_to_maya(normals[n.vertex_normal_index]))
            loop_uvs.append(axes.bz_uv_to_maya((n.uv.u, n.uv.v)))
    mesh.normals = loop_normals
    mesh.uv_sets = [loop_uvs]
    return mesh


def geo_part_props(geo) -> Dict[str, object]:
    """GEO header words and the first face's raw bytes, kept as the part's defaults."""
    props: Dict[str, object] = {
        "header_unknown": int(geo._checksum),
        "header_flags": int(geo.flags),
    }
    if geo.face_list:
        face = geo.face_list[0]
        props["face_unknown"] = struct.unpack("<i", struct.pack("<f", float(face.poly_area)))[0]
        props["face_shade"] = int(face.shade_type)
        props["face_texture"] = int(face.texture_type)
        props["face_xlu"] = int(face.xluscent_type)
        props["face_parent"] = int(face.parent_face_index)
        props["face_node"] = int(face.tree_branch)
    return props


def face_normal(points) -> Tuple[float, float, float]:
    """Unit CCW normal of a polygon (Maya space), by Newell's method."""
    return axes.newell_normal(points)


def with_normals(mesh: Mesh) -> List[Tuple[float, float, float]]:
    """Per-loop normals: the mesh's own, or flat face normals when it has none."""
    if mesh.normals is not None and len(mesh.normals) == mesh.loop_count():
        return list(mesh.normals)
    out = []
    for face in mesh.faces:
        n = face_normal([mesh.positions[i] for i in face])
        out.extend([n] * len(face))
    return out


def texture_name(material: str) -> str:
    base = os.path.splitext((material or "").strip())[0]
    return base.replace(" ", "_").lower()[:MAX_TEXTURE_CHARS]


# Maya (x, y, z) -> Blender (x, -z, y): Blender's face centres and plane maths use Blender axes.
_TO_BLENDER = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
FACE_PLANE_MODES = ("STOCK", "CURRENT", "PRESERVE", "RECOMPUTE", "DX_FIX")


def face_plane(mode: str, maya_points, bz_points) -> Tuple[Tuple[float, float, float], float]:
    """The four per-face GEO plane floats, by Blender's ``face_plane_mode`` (export_geo.py).

    CURRENT / PRESERVE (no imported plane data exists in Maya): the face centre in Blender
    axes and ``d = 1.0``. RECOMPUTE / DX_FIX: the normalised first-three-vertex normal in GEO
    coordinates and ``d = +n . p0`` (Blender's ``_compute_face_plane``). STOCK (the default
    here, and not a Blender option): stock files' own convention ``d = -n . p0`` with the
    winding-based normal.
    """
    if mode in ("CURRENT", "PRESERVE"):
        center = _TO_BLENDER @ np.mean(np.array(maya_points, dtype=float), axis=0)
        return (float(center[0]), float(center[1]), float(center[2])), 1.0
    pts = np.array(bz_points[:3], dtype=float)
    if mode in ("RECOMPUTE", "DX_FIX") and len(pts) == 3:
        cross = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        length = float(np.linalg.norm(cross))
        n = cross / length if length > 0.0 else np.array([0.0, 0.0, 1.0])
        return (float(n[0]), float(n[1]), float(n[2])), float(np.dot(n, pts[0]))
    n = np.array(axes.newell_normal(bz_points))
    return (float(n[0]), float(n[1]), float(n[2])), -float(np.dot(n, np.array(bz_points[0])))


def mesh_to_geo(name: str, mesh: Mesh, props: Optional[Dict[str, object]] = None, face_plane_mode: str = "STOCK"):
    """Build a bzgeo.Geo. Vertices are welded on (position, normal, UV): a GEO vertex carries one UV.

    ``props`` supplies the header words and the per-face defaults
    (``header_unknown`` ... ``face_node``); Blender's exporter writes the same defaults.
    ``face_plane_mode`` is one of ``FACE_PLANE_MODES``.
    """
    _, _, bzgeo, spacial, _ = _modules()
    props = props or {}
    normals = with_normals(mesh)
    uvs = mesh.uv_sets[0] if mesh.uv_sets else None

    vertex_ids: Dict[tuple, int] = {}
    out_positions: List[tuple] = []
    out_normals: List[tuple] = []

    geo = bzgeo.Geo()
    geo.name = name[:16]
    geo._checksum = int(props.get("header_unknown", 69))
    geo.flags = int(props.get("header_flags", 0))
    face_unknown = int(props.get("face_unknown", 0))
    poly_area = 0.0 if face_unknown == 0 else struct.unpack("<f", struct.pack("<i", face_unknown))[0]

    loop = 0
    for face_index, face in enumerate(mesh.faces):
        bz_points = [axes.maya_point_to_bz(mesh.positions[i]) for i in face]
        # Stock GEOs store plane normal = normalize(cross(b - a, c - a)) and d = -n . a
        # (checked on shipped files); Blender's own modes are in face_plane().
        plane_normal, plane_d = face_plane(face_plane_mode, [mesh.positions[i] for i in face], bz_points)

        gface = geo.create_face(face_index)
        gface.color = spacial.Color3(*DEFAULT_FACE_COLOR)
        gface.plane = bzgeo.SurfacePlane(spacial.Vector3(*plane_normal), plane_d)
        gface.poly_area = poly_area
        gface.shade_type = int(props.get("face_shade", 4)) & 0xFF
        gface.texture_type = int(props.get("face_texture", 0)) & 0xFF
        gface.xluscent_type = int(props.get("face_xlu", 0)) & 0xFF
        gface.texture_name = texture_name(mesh.face_materials[face_index])
        gface.parent_face_index = int(props.get("face_parent", 0))
        gface.tree_branch = int(props.get("face_node", 0))
        for vi in face:
            normal = axes.maya_normal_to_bz(normals[loop])
            u, v = axes.maya_uv_to_bz(uvs[loop]) if uvs else (0.0, 0.0)
            # Legacy GEO vertices carry a single UV (the porter and the toolkit's importers read it
            # per vertex), so a position shared by faces with different UVs must be split.
            key = (vi, tuple(round(c, 6) for c in normal), round(u, 6), round(v, 6))
            if key not in vertex_ids:
                vertex_ids[key] = len(out_positions)
                out_positions.append(axes.maya_point_to_bz(mesh.positions[vi]))
                out_normals.append(normal)
            index = vertex_ids[key]
            gface.create_face_node(index, index, spacial.UV(u, v))
            loop += 1

    geo.vert_count = len(out_positions)
    geo.vertex_pos_buffer = np.asarray(out_positions, dtype="<f4").reshape(-1, 3)
    geo.vertex_normal_buffer = np.asarray(out_normals, dtype="<f4").reshape(-1, 3)
    return geo


def _bounds_from_mesh(mesh: Optional[Mesh]):
    """(geocenter, sphereradius, boxhalfheight) in BZ local space, like GenerateGEOCollisions."""
    if mesh is None or not mesh.positions:
        return [0.0, 0.0, 0.0], 0.0, [0.0, 0.0, 0.0]
    pts = np.array([axes.maya_point_to_bz(p) for p in mesh.positions], dtype=float)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    center = (lo + hi) / 2.0
    half = np.maximum(np.abs(lo - center), np.abs(hi - center))
    radius = float(np.linalg.norm(pts - center, axis=1).max())
    return [float(v) for v in center], radius, [float(v) for v in half]


# --- standalone GEO --------------------------------------------------------

def read_geo(path: str, import_options: Optional[Dict[str, object]] = None) -> Scene:
    """A single .geo as a one-node scene named after the file."""
    _, _, _, _, GeoSerializer = _modules()
    with open(path, "rb") as handle:
        geo = GeoSerializer(handle).read()
    name = os.path.splitext(os.path.basename(path))[0]
    props = geo_part_props(geo)
    scene = Scene(nodes=[Node(name=name, mesh=geo_to_mesh(geo), props=props)])
    attach_map_textures(scene, os.path.dirname(os.path.abspath(path)), import_options)
    return scene


def write_geo(node: Node, path: str, face_plane_mode: str = "STOCK") -> List[str]:
    """Write one node's mesh as a .geo (local space, no hierarchy)."""
    _, _, _, _, GeoSerializer = _modules()
    if node.mesh is None or not node.mesh.faces:
        raise ValueError("%s has no mesh faces to export as GEO" % node.name)
    warnings = _geo_warnings(node.name, node.mesh)
    name = os.path.splitext(os.path.basename(path))[0]
    with open(path, "wb") as handle:
        GeoSerializer(handle).write(mesh_to_geo(name, node.mesh, node.props, face_plane_mode))
    return warnings


def _geo_warnings(name: str, mesh: Mesh) -> List[str]:
    bad = 0
    for face in mesh.faces:
        non_planar, concave = axes.polygon_issues([mesh.positions[i] for i in face])
        bad += int(non_planar or concave)
    if bad:
        return [f"{name}: {bad} non-planar or concave face(s); GEO needs convex planar polygons"]
    return []


# --- collision helper boxes --------------------------------------------------

def box_mesh(lo, hi) -> Mesh:
    """An outward-facing box in Maya space from two corner points."""
    corners = []
    for ix in (0, 1):
        for iy in (0, 1):
            for iz in (0, 1):
                corners.append((
                    (lo, hi)[ix][0], (lo, hi)[iy][1], (lo, hi)[iz][2],
                ))
    center = np.array([(lo[i] + hi[i]) / 2.0 for i in range(3)])
    faces = []
    for quad in _BOX_QUADS:
        pts = [np.array(corners[i]) for i in quad]
        normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        if float(np.dot(normal, sum(pts) / 4.0 - center)) < 0:
            quad = tuple(reversed(quad))
        faces.append(quad)
    return Mesh(positions=corners, faces=faces, face_materials=[""] * len(faces))


def _colp_boxes(colp) -> Tuple[Mesh, Mesh]:
    """Inner / outer helper meshes from the 12 COLP floats.

    The file's "Y" slots are BZ Z (forward) and its "Z" slots BZ Y (up); this is the
    toolkit's convention (import_vdf.py) and agrees with stock files.
    """
    (y_out_max, y_in_max, y_in_min, y_out_min,
     x_out_max, x_in_max, x_in_min, x_out_min,
     z_out_max, z_in_max, z_in_min, z_out_min) = [float(v) for v in colp]

    def box(x0, x1, up0, up1, fwd0, fwd1):
        a = axes.bz_point_to_maya((x0, up0, fwd0))
        b = axes.bz_point_to_maya((x1, up1, fwd1))
        lo = tuple(min(a[i], b[i]) for i in range(3))
        hi = tuple(max(a[i], b[i]) for i in range(3))
        return box_mesh(lo, hi)

    inner = box(x_in_min, x_in_max, z_in_min, z_in_max, y_in_min, y_in_max)
    outer = box(x_out_min, x_out_max, z_out_min, z_out_max, y_out_min, y_out_max)
    return inner, outer


def _extent(points_bz):
    """(min, max) per BZ axis (x, y, z) of a point list."""
    pts = np.array(points_bz, dtype=float)
    return pts.min(axis=0), pts.max(axis=0)


def colp_from_extents(inner, outer) -> List[float]:
    """COLP floats from (min, max) BZ extents; see ``_colp_boxes`` for the slot axes."""
    (ilo, ihi), (olo, ohi) = inner, outer
    return [
        ohi[2], ihi[2], ilo[2], olo[2],
        ohi[0], ihi[0], ilo[0], olo[0],
        ohi[1], ihi[1], ilo[1], olo[1],
    ]


# --- shared read (VDF and SDF) ---------------------------------------------

def build_nodes(records, folder: str, scene: Scene, extra_props=None, quiet_duplicates=False) -> None:
    """Fill ``scene.nodes`` from ``(lod, record, owner)`` triples, loading ``<name>.geo`` from ``folder``.

    ``record`` is a vdf_classes / sdf_classes GEOData and ``owner`` the (lower-case) name of the
    LOD1 record in the same slot, or None. ``extra_props(record)`` returns
    additional per-node props (SDF: ddr, x, y, z, time).
    """
    _, _, _, _, GeoSerializer = _modules()
    nodes: Dict[str, Node] = {}
    parents: Dict[str, str] = {}
    for lod, geo_data, owner in records:
        key = geo_data.name.lower()
        if key in nodes:
            # SDF files repeat LOD1 records in the LOD2/3 bands; only warn for real clashes.
            if quiet_duplicates:
                continue
            scene.warnings.append(f"duplicate part name {geo_data.name}; later record ignored")
            continue
        mesh = None
        geo_props: Dict[str, object] = {}
        geo_path = _find_case_insensitive(folder, geo_data.name + ".geo")
        if geo_path is not None:
            try:
                with open(geo_path, "rb") as handle:
                    geo = GeoSerializer(handle).read()
                mesh = geo_to_mesh(geo)
                geo_props = geo_part_props(geo)
            except Exception as exc:  # corrupt GEO: keep the part as an empty
                scene.warnings.append(f"could not read {os.path.basename(geo_path)}: {exc}")
        part_type = int(geo_data.type)
        matrix12 = [float(v) for v in geo_data.matrix]
        props = {
            "type": part_type,
            "flags": int(geo_data.geoflags),
            "geocenter": [float(v) for v in geo_data.geocenter],
            "sphereradius": float(geo_data.sphereradius),
            "boxhalfheight": [float(v) for v in geo_data.boxhalfheight],
            # Authored bounds survive round trips by default (Blender: BoundsMode PRESERVE).
            "bounds_mode": "PRESERVE",
            "authored_bounds": True,
            "raw_matrix": list(matrix12),
            "use_raw_matrix": False,
            # The record's band decides the LOD; some stock names (g1431024) disagree with it.
            "lod": int(lod),
        }
        props.update(geo_props)
        if lod > 1 and owner:
            props["slot"] = owner  # the LOD1 part this one shares its slot with
        if part_type == EYEPOINT_TYPE and mesh is None:
            props["pov_helper"] = True
        if extra_props is not None:
            props.update(extra_props(geo_data))
        if part_type == SPINNER_TYPE:
            # Spinner records store axis * speed in the first three floats, not a rotation.
            x, y, z = matrix12[0:3]
            speed = (x * x + y * y + z * z) ** 0.5
            props["is_spinner"] = True
            props["spinner_axis"] = [x / speed, y / speed, z / speed] if speed > 1e-8 else [1.0, 0.0, 0.0]
            props["spinner_speed"] = speed if speed > 1e-8 else 0.0
            if geo_data.parent.lower() != "world":
                props["spinner_target"] = geo_data.parent.lower()
            matrix12 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0] + matrix12[9:12]
        if part_type in HARDPOINT_TYPES:
            kind = KIND_HARDPOINT
        elif mesh is None:
            kind = KIND_EMPTY
        elif lod > 1:
            kind = KIND_LOD
        else:
            kind = KIND_PART
        nodes[key] = Node(
            name=geo_data.name,
            parent=None,
            matrix=axes.bz_matrix12_to_maya(matrix12),
            kind=kind,
            mesh=mesh,
            props=props,
        )
        parents[key] = geo_data.parent.lower()

    # Parent resolution, as import_vdf.py does it: WORLD / self / unknown -> root,
    # and a missing LOD2/3 parent falls back to its LOD1 name (character 3 -> '1').
    for key, node in nodes.items():
        parent_key = parents[key]
        if parent_key in ("world", "", key):
            continue
        if parent_key in nodes:
            node.parent = nodes[parent_key].name
        elif len(parent_key) > 3:
            fallback = parent_key[:3] + "1" + parent_key[4:]
            if fallback in nodes and fallback != key:
                node.parent = nodes[fallback].name
            else:
                scene.warnings.append(f"{node.name}: parent {parent_key} not found; made a root")
        else:
            scene.warnings.append(f"{node.name}: parent {parent_key} not found; made a root")

    scene.nodes = list(nodes.values())


# --- shared write layout (VDF and SDF) -------------------------------------

def _lod_of(name: str) -> Optional[int]:
    """The LOD digit: the 4th character of ``abc11bdy``-style names."""
    if len(name) >= 5 and name[3] in "123":
        return int(name[3])
    return None


def _slot_key(name: str) -> str:
    if _lod_of(name) is None:
        return name
    return name[:3] + name[5:]  # export_transforms.lod_slot


class Layout:
    """Which parts are written, in what slot order, with which parent / matrix."""

    def __init__(self):
        self.entries: Dict[str, Node] = {}
        self.lods: Dict[str, int] = {}
        self.by_lod: Dict[int, Dict[str, str]] = {1: {}, 2: {}, 3: {}}  # slot key -> name
        self.resolved: Dict[str, Tuple[Optional[str], list]] = {}
        self.placed: List[Tuple[str, str]] = []
        self.helpers: Dict[str, Node] = {}  # "inner" / "outer" collision helper nodes
        self.helper_worlds: Dict[str, list] = {}
        self.matrices: Dict[str, list] = {}  # record matrix (12 floats) written per part

    def lod(self, name: str) -> int:
        return self.lods[name]

    def parent_record_name(self, name: str) -> str:
        parent_name = self.resolved[name][0]
        if parent_name is None:
            return "WORLD"
        lod = self.lod(name)
        if self.lod(parent_name) != lod:
            candidate = parent_name[:3] + str(lod) + parent_name[4:]
            if candidate in self.entries:
                return candidate
        return parent_name

    def world(self, name: str):
        matrix = axes.identity4()
        seen = set()
        current: Optional[str] = name
        while current is not None and current not in seen:
            seen.add(current)
            parent, local = self.resolved[current]
            matrix = axes.matmul(local, matrix)
            current = parent
        return matrix

    def slot_of(self, lod1_name: str) -> Optional[int]:
        for index, (_key, name) in enumerate(self.placed):
            if name == lod1_name.lower():
                return index
        return None


def make_layout(scene: Scene, warnings: List[str]) -> Layout:
    """Decide what gets written.

    The LOD is the ``lod`` prop kept from import (the file's band decides, and some stock
    names such as ``g1431024`` disagree with it), else the 4th character of the name.
    Names without a LOD digit are accepted as LOD1 only when the node carries BZ props (it
    came from a BZ file or was tagged with a GEO type): stock files do contain free-form
    names such as ``op1bda01`` or ``g083700b``. ``inner_col`` / ``outer_col`` meshes are
    the VDF collision helpers, not parts.
    """
    layout = Layout()
    entries: Dict[str, Node] = {}
    lods: Dict[str, int] = {}
    for node in scene.nodes:
        helper = collision_helper_kind(node.name)
        if helper:
            layout.helpers[helper] = node
            continue
        name = node.name.lower()
        if len(name) > 8:
            warnings.append(f"{node.name}: name longer than 8 characters, truncated")
            name = name[:8]
        props = node.props or {}
        lod = int(props["lod"]) if props.get("lod") in (1, 2, 3) else _lod_of(name)
        if lod is None and "type" in props:
            lod = 1
        if lod is None:
            warnings.append(
                f"{node.name}: skipped, part names need a LOD digit (1-3) as the 4th character"
            )
            continue
        if name in entries:
            warnings.append(f"{node.name}: duplicate part name after truncation, skipped")
            continue
        entries[name] = node
        lods[name] = lod

    by_lod = layout.by_lod
    for name in entries:
        if lods[name] == 1:
            by_lod[1][_slot_key(name)] = name
    for name in entries:
        lod = lods[name]
        if lod == 1:
            continue
        owner = str((entries[name].props or {}).get("slot") or "")[:8]
        # Files pair LOD parts by slot position; stock names do not always show it.
        key = _slot_key(owner) if owner and _slot_key(owner) in by_lod[1] else _slot_key(name)
        by_lod[lod][key] = name
    for lod in (2, 3):
        for key, name in list(by_lod[lod].items()):
            if key not in by_lod[1]:
                warnings.append(f"{name}: skipped, no LOD1 part with the same name")
                del by_lod[lod][key]

    # Only parts that survived the LOD filtering are written. A skipped node (an
    # organising group, an orphaned LOD part) is removed from the hierarchy and its
    # matrix is composed into its children, so their world transforms stay put.
    layout.entries = entries = {
        n: entries[n] for lod_names in by_lod.values() for n in lod_names.values()
    }
    layout.lods = {n: lods[n] for n in entries}
    entry_of = {id(node): name for name, node in entries.items()}
    by_node_name = {node.name: node for node in scene.nodes}
    for name, node in entries.items():
        matrix = node.matrix
        parent_name = None
        seen = {node.name}
        walker = by_node_name.get(node.parent) if node.parent else None
        while walker is not None and walker.name not in seen:
            seen.add(walker.name)
            if id(walker) in entry_of:
                parent_name = entry_of[id(walker)]
                break
            matrix = axes.matmul(walker.matrix, matrix)
            walker = by_node_name.get(walker.parent) if walker.parent else None
        layout.resolved[name] = (parent_name, matrix)

    for kind, helper in layout.helpers.items():
        matrix = helper.matrix
        seen = {helper.name}
        walker = by_node_name.get(helper.parent) if helper.parent else None
        while walker is not None and walker.name not in seen:
            seen.add(walker.name)
            matrix = axes.matmul(walker.matrix, matrix)
            walker = by_node_name.get(walker.parent) if walker.parent else None
        layout.helper_worlds[kind] = matrix

    # Parents must precede children in the LOD1 slot order.
    pending = list(by_lod[1].items())
    placed_names = set()
    lod1_names = set(by_lod[1].values())
    while pending:
        progress = False
        for item in list(pending):
            key, name = item
            parent = layout.parent_record_name(name)
            if parent == "WORLD" or parent not in lod1_names or parent in placed_names:
                layout.placed.append(item)
                placed_names.add(name)
                pending.remove(item)
                progress = True
        if not progress:  # cycle: keep file order rather than loop forever
            warnings.append("parent cycle among LOD1 parts; slot order may be wrong")
            layout.placed.extend(pending)
            break
    _order_spinner_helpers(layout)
    return layout


def _order_spinner_helpers(layout: Layout) -> None:
    """Spinner helpers are written right after their target slot (export_vdf.py)."""
    slot_names = [name for _key, name in layout.placed]
    helpers: Dict[str, List[Tuple[str, str]]] = {}
    moved = set()
    for item in layout.placed:
        _key, name = item
        props = layout.entries[name].props or {}
        if int(props.get("type", -1)) != SPINNER_TYPE or not props.get("is_spinner"):
            continue
        target = str(props.get("spinner_target") or "").lower()[:8]
        if target and target != name and target in slot_names:
            helpers.setdefault(target, []).append(item)
            moved.add(name)
    if not moved:
        return
    ordered: List[Tuple[str, str]] = []
    for item in layout.placed:
        if item[1] in moved:
            continue
        ordered.append(item)
        ordered.extend(helpers.get(item[1], []))
    layout.placed = ordered


# --- shared write (VDF and SDF) --------------------------------------------

def fill_records(layout: Layout, folder: str, warnings: List[str], new_record, serialize,
                 null_record: bytes, bands, band_count: int, extras=None, empty_record: Optional[bytes] = None,
                 write_geos: bool = True, face_plane_mode: str = "STOCK"):
    """Serialised record grid (``band * geocount + slot``), writing ``.geo`` files.

    Returns ``(records, lod1_world_points_in_bz_space)``.
    """
    _, _, _, _, GeoSerializer = _modules()
    geocount = len(layout.placed)
    # Empty slots of the LOD bands are NULL records; other bands default to empty_record
    # (zeros for VDF, as Blender writes them; NULL records for SDF).
    records = [empty_record if empty_record is not None else null_record] * (band_count * geocount)
    for band in bands:
        records[band * geocount:(band + 1) * geocount] = [null_record] * geocount
    all_points = []
    for slot, (key, _lod1_name) in enumerate(layout.placed):
        for lod, band in zip((1, 2, 3), bands):
            name = layout.by_lod[lod].get(key)
            if name is None:
                continue
            node = layout.entries[name]
            record = new_record()
            record.name = name
            record.parent = layout.parent_record_name(name)
            props = node.props or {}
            part_type = props.get("type")
            if part_type is None:
                part_type = 70 if node.kind == KIND_HARDPOINT else DEFAULT_PART_TYPE
            record.type = int(part_type)
            record.geoflags = int(props.get("flags", 0))

            raw = props.get("raw_matrix")
            if props.get("use_raw_matrix") and raw is not None and len(raw) == 12:
                matrix12 = [float(v) for v in raw]
            else:
                matrix12 = axes.maya_matrix_to_bz12(layout.resolved[name][1])
                if record.type == SPINNER_TYPE and props.get("is_spinner"):
                    axis = props.get("spinner_axis") or [1.0, 0.0, 0.0]
                    speed = float(props.get("spinner_speed", 1.0))
                    matrix12[0:3] = [float(axis[0]) * speed, float(axis[1]) * speed, float(axis[2]) * speed]
            record.matrix = matrix12
            layout.matrices[name] = matrix12

            has_mesh = node.mesh is not None and bool(node.mesh.faces)
            mode = props.get("bounds_mode", "AUTO")
            generate = has_mesh and (
                mode == "RECALC"
                or (mode == "AUTO" and props.get("generate_collision", True))
                or (mode == "PRESERVE" and not props.get("authored_bounds", False))
            )
            if generate:
                record.geocenter, record.sphereradius, record.boxhalfheight = _bounds_from_mesh(node.mesh)
            else:
                record.geocenter = [float(v) for v in props.get("geocenter") or (0.0, 0.0, 0.0)]
                record.sphereradius = float(props.get("sphereradius", 3.0))
                record.boxhalfheight = [float(v) for v in props.get("boxhalfheight") or (0.0, 0.0, 0.0)]
            if extras is not None:
                extras(record, props)
            records[band * geocount + slot] = serialize(record)

            if has_mesh:
                warnings.extend(_geo_warnings(name, node.mesh))
                if write_geos:
                    geo = mesh_to_geo(name, node.mesh, props, face_plane_mode)
                    with open(os.path.join(folder, name + ".geo"), "wb") as handle:
                        GeoSerializer(handle).write(geo)
                if lod == 1:
                    world = layout.world(name)
                    all_points.extend(
                        axes.maya_point_to_bz(axes.transform_point(world, p))
                        for p in node.mesh.positions
                    )
    return records, all_points


# --- VDF -------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


DEFAULT_IMPORT_OPTIONS = {"ImportMapTextures": True, "MapTextureDirectory": "", "MapTextureZFS": ""}


def attach_map_textures(scene: Scene, folder: str, options: Optional[Dict[str, object]]) -> None:
    """Blender's "Auto-load .map textures": a PNG per face texture name found as a stock .map.

    Lookup follows Blender's rules (import folder, the user's folder, then the stock ZFS archive);
    the result is ``scene.materials[name]["texture_path"]``.
    """
    options = dict(DEFAULT_IMPORT_OPTIONS, **(options or {}))
    if not options["ImportMapTextures"]:
        return
    from . import mapload

    names = []
    for node in scene.nodes:
        if node.mesh is not None:
            names.extend(m for m in node.mesh.face_materials if m and m not in names)
    for name in names:
        png = mapload.texture_for_map_name(
            name, folder, str(options["MapTextureDirectory"] or ""), str(options["MapTextureZFS"] or "")
        )
        if png:
            scene.materials.setdefault(name, {})
            scene.materials[name].update({"texture": name + ".map", "texture_path": png})


def read_vdf(path: str, import_options: Optional[Dict[str, object]] = None) -> Scene:
    vdf_classes, vdf_file, _, _, _ = _modules()
    from bz98tools import semantics

    with open(path, "rb") as handle:
        parsed = vdf_file.parse_vdf(handle.read())

    scene = Scene()
    scene.meta = {
        "format": "vdf",
        "name": parsed.vdfc_name,
        "vehicletype": int(parsed.vdfc_vehicletype),
        "vehiclesize": int(parsed.vdfc_vehiclesize),
        "lod_dists": [float(v) for v in parsed.vdfc_lod_dists],
        "mass": float(parsed.vdfc_mass),
        "multiplyer": float(parsed.vdfc_multiplyer),
        "drag": float(parsed.vdfc_drag),
        "vdfc_null": int(parsed.vdfc_null),
        "use_scps": bool(parsed.scps_tag),
        "scps_data": [int(v) for v in (parsed.scps_data or [0, 0, 0])[:3]],
    }

    records = []  # (lod, GEOData, owner)
    owners: Dict[int, str] = {}
    for lod, band in enumerate(LOD_BANDS, start=1):
        for slot in range(parsed.geocount):
            index = band * parsed.geocount + slot
            if index >= len(parsed.records):
                break
            geo_data = vdf_classes.GEOData()
            geo_data.Read(parsed.records[index], 0)
            if geo_data.name[0:4].lower() == "null" or not geo_data.name:
                continue
            if lod == 1:
                owners[slot] = geo_data.name.lower()
            records.append((lod, geo_data, owners.get(slot)))

    build_nodes(records, os.path.dirname(os.path.abspath(path)), scene)
    nodes = {n.name.lower(): n for n in scene.nodes}
    if parsed.anim_present:
        anim_io.read_anim(scene, parsed.anim_header, parsed.anim_elements, parsed.anim_orientations,
                          parsed.anim_rotations, parsed.anim_translations2, parsed.anim_positions)

    # Damage bands: lod-slot-0 bands 1..3 are per-part mesh swaps; every other populated
    # band is kept verbatim (Blender stores them the same way).
    preserved = {"plan": [kind for kind, _ in parsed.plan], "chunks": [], "vlocs": [], "bands": []}
    for index, raw in enumerate(parsed.records):
        band, slot = divmod(index, max(1, parsed.geocount))
        if band in LOD_BANDS or len(raw) < 8:
            continue
        name = raw[:8].split(b"\0")[0].decode("ascii", "ignore").strip().lower()
        if not name or name.startswith("null"):
            continue
        lod_slot, state = semantics.band_coords(band)
        part = nodes.get(owners.get(slot, ""))
        if lod_slot == 0 and state in semantics.AUTHORED_DAMAGE_STATES and part is not None:
            damage = part.props.setdefault("damage", ["", "", ""])
            if not damage[state - 1] or damage[state - 1] == name:
                damage[state - 1] = name[:8]
                part.props["has_damage_variants"] = True
                continue
        preserved["bands"].append(
            {"part": owners.get(slot, ""), "slot": slot, "band": band, "data": _b64(raw)}
        )
    for chunk in parsed.raw_chunks:
        preserved["chunks"].append(_b64(chunk.full_bytes))
    for chunk in parsed.vlocs:
        raw = getattr(chunk, "raw_full_bytes", None)
        if raw:
            preserved["vlocs"].append(_b64(raw))
    scene.meta["preserved"] = preserved

    if parsed.colp_data is not None:
        inner, outer = _colp_boxes(parsed.colp_data)
        scene.nodes.append(Node(name=INNER_COLLISION, mesh=inner))
        scene.nodes.append(Node(name=OUTER_COLLISION, mesh=outer))
    attach_map_textures(scene, os.path.dirname(os.path.abspath(path)), import_options)
    return scene


def _clean_anim_from_plan(plan):
    """Drop the ANIM block and its terminating EXIT (animation is not modelled yet)."""
    cleaned = []
    skip_exit = False
    for kind, index in plan:
        if skip_exit:
            skip_exit = False
            if kind == "exit":
                continue
        if kind == "anim":
            skip_exit = True
            continue
        cleaned.append((kind, index))
    return cleaned


def write_vdf(scene: Scene, path: str, write_geos: bool = True, face_plane_mode: str = "STOCK",
              export_animations: bool = True) -> List[str]:
    """Write ``path`` and a ``<name>.geo`` beside it for every part with a mesh.

    Returns warnings (skipped parts etc.).
    """
    vdf_classes, vdf_file, _, _, _ = _modules()
    from bz98tools import semantics
    from . import authoring

    folder = os.path.dirname(os.path.abspath(path))
    warnings: List[str] = list(scene.warnings)
    layout = make_layout(scene, warnings)

    geocount = len(layout.placed)
    model = vdf_file.ParsedVDF()
    meta = dict(authoring.default_meta())
    meta.update(scene.meta or {})
    preserved = meta.get("preserved") or {}

    model.plan = list(vdf_file.new_empty_plan())
    if preserved.get("plan"):
        plan = []
        vloc_index = raw_index = 0
        for kind in preserved["plan"]:
            if kind == "vloc":
                plan.append(("vloc", vloc_index))
                vloc_index += 1
            elif kind == "raw":
                plan.append(("raw", raw_index))
                raw_index += 1
            else:
                plan.append((kind, None))
        model.plan = plan
    for data in preserved.get("chunks", []):
        model.raw_chunks.append(vdf_file.RawChunk(base64.b64decode(data)[:4], base64.b64decode(data)))
    for data in preserved.get("vlocs", []):
        chunk = semantics.VLOCChunk()
        chunk.raw_full_bytes = base64.b64decode(data)
        chunk.preserve_raw = True
        model.vlocs.append(chunk)
    while sum(1 for kind, _ in model.plan if kind == "vloc") < len(model.vlocs):
        model.plan.append(("vloc", sum(1 for kind, _ in model.plan if kind == "vloc")))
    while sum(1 for kind, _ in model.plan if kind == "raw") < len(model.raw_chunks):
        model.plan.append(("raw", sum(1 for kind, _ in model.plan if kind == "raw")))

    model.vdfc_name = str(meta.get("name") or os.path.splitext(os.path.basename(path))[0])[:16]
    model.vdfc_vehicletype = int(meta["vehicletype"])
    model.vdfc_vehiclesize = int(meta["vehiclesize"])
    model.vdfc_lod_dists = _lod_dists(meta)
    model.vdfc_mass = float(meta["mass"])
    model.vdfc_multiplyer = float(meta["multiplyer"])
    model.vdfc_drag = float(meta["drag"])
    model.vdfc_null = int(meta.get("vdfc_null", 0))
    model.geocount = geocount

    null_geo = vdf_classes.GEOData()
    null_geo.name = "NULL"
    null_geo.matrix = [0] * 12
    null_geo.parent = "NULL"
    null_geo.geocenter = [0] * 3
    null_geo.sphereradius = 0
    null_geo.boxhalfheight = [0] * 3
    null_geo.type = 0
    null_geo.geoflags = 0

    model.records, all_points = fill_records(
        layout, folder, warnings, vdf_classes.GEOData, vdf_classes.serialize_section,
        vdf_classes.serialize_section(null_geo), LOD_BANDS, BAND_COUNT, empty_record=bytes(100),
        write_geos=write_geos, face_plane_mode=face_plane_mode,
    )
    _write_damage_bands(model.records, layout, geocount, preserved, semantics)

    block = anim_io.build_anim(
        layout.entries, layout.matrices, meta, vdf_classes, os.path.splitext(os.path.basename(path))[0]
    ) if export_animations else None
    plan = list(model.plan)
    if block is not None:
        (model.anim_header, model.anim_elements, model.anim_orientations, model.anim_rotations,
         model.anim_translations2, model.anim_positions) = block
        model.anim_present = True
        if not any(kind == "anim" for kind, _ in plan):  # fresh plans: after VGEO with its EXIT, the stock order
            at = next((i + 1 for i, (kind, _) in enumerate(plan) if kind == "vgeo"), len(plan))
            plan[at:at] = [("anim", None), ("exit", None)]
    else:
        plan = _clean_anim_from_plan(plan)
    model.plan = plan

    if any(kind == "colp" for kind, _ in model.plan):
        model.colp_data = _collision_floats(layout, all_points, meta)
    if any(kind == "scps" for kind, _ in model.plan):
        model.scps_tag = "SPCS"
        model.scps_data = (
            [int(v) for v in meta.get("scps_data", [0, 0, 0])] if meta.get("use_scps") else [0, 0, 0]
        )

    with open(path, "wb") as handle:
        handle.write(vdf_file.serialize_vdf(model))
    return warnings


def _write_damage_bands(records, layout: Layout, geocount: int, preserved, semantics) -> None:
    for slot, (_key, lod1_name) in enumerate(layout.placed):
        names = (layout.entries[lod1_name].props or {}).get("damage") or []
        for state in semantics.AUTHORED_DAMAGE_STATES:
            variant = str(names[state - 1] if len(names) >= state else "").strip().lower()
            if variant and variant != "null":
                records[semantics.band_index(0, state) * geocount + slot] = semantics.synthesize_variant_record(
                    records[slot], variant[:8]
                )
    for entry in preserved.get("bands", []):
        slot = layout.slot_of(entry.get("part") or "")
        if slot is None:
            slot = int(entry.get("slot", -1))
        band = int(entry.get("band", -1))
        if not 0 <= slot < geocount or not 0 <= band < BAND_COUNT:
            continue
        index = band * geocount + slot
        if records[index][:4] in (b"\0\0\0\0", b"NULL"):  # an authored swap wins over a preserved one
            records[index] = base64.b64decode(entry["data"])[:100]


def _collision_floats(layout: Layout, lod1_points_bz, meta) -> List[float]:
    """COLP from the inner_col / outer_col helper meshes, else the parts' bounding box."""
    extents = {}
    for kind, node in layout.helpers.items():
        if node.mesh is None or not node.mesh.positions:
            continue
        world = layout.helper_worlds[kind]
        extents[kind] = _extent([
            axes.maya_point_to_bz(axes.transform_point(world, p)) for p in node.mesh.positions
        ])
    if extents:
        inner = extents.get("inner") or extents["outer"]
        outer = extents.get("outer") or extents["inner"]
        return colp_from_extents(inner, outer)
    colp = meta.get("colp")
    if colp and len(colp) == 12:
        return [float(v) for v in colp]
    if not lod1_points_bz:
        return [0.0] * 12
    box = _extent(lod1_points_bz)
    return colp_from_extents(box, box)


def _lod_dists(meta) -> List[float]:
    lod_dists = list(meta.get("lod_dists") or [])
    return [float(v) for v in (lod_dists + [0.0] * 5)[:5]]
