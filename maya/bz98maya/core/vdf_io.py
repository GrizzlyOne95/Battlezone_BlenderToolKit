"""Legacy .vdf (+ .geo) <-> neutral scene. Maya-free.

Binary work is done by the toolkit: ``vdf_file.parse_vdf`` / ``serialize_vdf``
for the vehicle container and ``bzrmodelporter.bzgeo_serializer.GeoSerializer``
for the parts. This module only converts between those objects and the neutral
model (see ``axes.py`` for the coordinate conventions).

Not round-tripped (dropped on read, rewritten with defaults on write):
ANIM block, damage-state variant bands, SCPS, VLOC chunks, unknown chunks,
trailing bytes, the VDFC trailing int, per-face shade/texture/translucency
bytes, face colour (written 204,204,204), face parent / tree branch, the
GEO header unknown/flags words, the "unknown" float after each face plane,
and the authored plane / plane distance (recomputed from the winding).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import numpy as np

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
LOD_BANDS = (0, 4, 8)  # semantics.band_index(lod_slot, damage_state 0) for LOD1/2/3
BAND_COUNT = 28  # semantics.VGEO_BAND_COUNT
DEFAULT_FACE_COLOR = (204, 204, 204)
GEO_HEADER_UNKNOWN = 69  # value the toolkit exporter writes (GEOHeaderUnknown default)
MAX_TEXTURE_CHARS = 8  # export_geo._derive_legacy_texture_name


def _modules():
    ensure_toolkit()
    from bz98tools import vdf_classes, vdf_file
    from bz98tools.bzrmodelporter import bzgeo, spacial
    from bz98tools.bzrmodelporter.bzgeo_serializer import GeoSerializer

    return vdf_classes, vdf_file, bzgeo, spacial, GeoSerializer


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


def mesh_to_geo(name: str, mesh: Mesh):
    """Build a bzgeo.Geo. Vertices are welded on (position, normal)."""
    _, _, bzgeo, spacial, _ = _modules()
    normals = with_normals(mesh)
    uvs = mesh.uv_sets[0] if mesh.uv_sets else None

    vertex_ids: Dict[tuple, int] = {}
    out_positions: List[tuple] = []
    out_normals: List[tuple] = []

    geo = bzgeo.Geo()
    geo.name = name[:15]
    geo._checksum = GEO_HEADER_UNKNOWN
    geo.flags = 0

    loop = 0
    for face_index, face in enumerate(mesh.faces):
        bz_points = [axes.maya_point_to_bz(mesh.positions[i]) for i in face]
        bz_face_normal = np.array(axes.newell_normal(bz_points))
        # Stock GEOs store plane normal = normalize(cross(b - a, c - a)) and d = -n . a
        # (checked on shipped files; export_geo.py's RECOMPUTE mode uses +n . a).
        plane_d = -float(np.dot(bz_face_normal, np.array(bz_points[0])))

        gface = geo.create_face(face_index)
        gface.color = spacial.Color3(*DEFAULT_FACE_COLOR)
        gface.plane = bzgeo.SurfacePlane(
            spacial.Vector3(*(float(v) for v in bz_face_normal)), plane_d
        )
        gface.poly_area = 0.0
        gface.shade_type = 4
        gface.texture_type = 0
        gface.xluscent_type = 0
        gface.texture_name = texture_name(mesh.face_materials[face_index])
        gface.parent_face_index = 0
        gface.tree_branch = 0
        for vi in face:
            normal = axes.maya_normal_to_bz(normals[loop])
            key = (vi, tuple(round(c, 6) for c in normal))
            if key not in vertex_ids:
                vertex_ids[key] = len(out_positions)
                out_positions.append(axes.maya_point_to_bz(mesh.positions[vi]))
                out_normals.append(normal)
            index = vertex_ids[key]
            u, v = axes.maya_uv_to_bz(uvs[loop]) if uvs else (0.0, 0.0)
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


# --- read ------------------------------------------------------------------

def read_vdf(path: str) -> Scene:
    vdf_classes, vdf_file, _, _, GeoSerializer = _modules()
    with open(path, "rb") as handle:
        parsed = vdf_file.parse_vdf(handle.read())

    folder = os.path.dirname(os.path.abspath(path))
    scene = Scene()
    scene.meta = {
        "name": parsed.vdfc_name,
        "vehicletype": int(parsed.vdfc_vehicletype),
        "vehiclesize": int(parsed.vdfc_vehiclesize),
        "lod_dists": [float(v) for v in parsed.vdfc_lod_dists],
        "mass": float(parsed.vdfc_mass),
        "multiplyer": float(parsed.vdfc_multiplyer),
        "drag": float(parsed.vdfc_drag),
        "colp": [float(v) for v in parsed.colp_data] if parsed.colp_data is not None else None,
    }

    records = []  # (lod, GEOData)
    for lod, band in enumerate(LOD_BANDS, start=1):
        for slot in range(parsed.geocount):
            index = band * parsed.geocount + slot
            if index >= len(parsed.records):
                break
            geo_data = vdf_classes.GEOData()
            geo_data.Read(parsed.records[index], 0)
            if geo_data.name[0:4].lower() == "null" or not geo_data.name:
                continue
            records.append((lod, geo_data))

    nodes: Dict[str, Node] = {}
    parents: Dict[str, str] = {}
    for lod, geo_data in records:
        key = geo_data.name.lower()
        if key in nodes:
            scene.warnings.append(f"duplicate part name {geo_data.name}; later record ignored")
            continue
        mesh = None
        geo_path = _find_case_insensitive(folder, geo_data.name + ".geo")
        if geo_path is not None:
            try:
                with open(geo_path, "rb") as handle:
                    mesh = geo_to_mesh(GeoSerializer(handle).read())
            except Exception as exc:  # corrupt GEO: keep the part as an empty
                scene.warnings.append(f"could not read {os.path.basename(geo_path)}: {exc}")
        part_type = int(geo_data.type)
        props = {
            "type": part_type,
            "flags": int(geo_data.geoflags),
            "geocenter": [float(v) for v in geo_data.geocenter],
            "sphereradius": float(geo_data.sphereradius),
            "boxhalfheight": [float(v) for v in geo_data.boxhalfheight],
        }
        matrix12 = [float(v) for v in geo_data.matrix]
        if part_type == SPINNER_TYPE:
            # Spinner records store axis * speed in the first three floats, not a rotation.
            props["spin"] = matrix12[0:3]
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
    return scene


# --- write -----------------------------------------------------------------

def _lod_of(name: str) -> Optional[int]:
    if len(name) >= 5 and name[3] in "123":
        return int(name[3])
    return None


def _slot_key(name: str) -> str:
    return name[:3] + name[5:]  # export_transforms.lod_slot


def write_vdf(scene: Scene, path: str) -> List[str]:
    """Write ``path`` and a ``<name>.geo`` beside it for every part with a mesh.

    Returns warnings (skipped parts etc.).
    """
    vdf_classes, vdf_file, _, _, GeoSerializer = _modules()
    folder = os.path.dirname(os.path.abspath(path))
    warnings: List[str] = list(scene.warnings)

    entries: Dict[str, Node] = {}
    for node in scene.nodes:
        name = node.name.lower()
        if len(name) > 8:
            warnings.append(f"{node.name}: name longer than 8 characters, truncated")
            name = name[:8]
        if _lod_of(name) is None:
            warnings.append(
                f"{node.name}: skipped, VDF part names need a LOD digit (1-3) as the 4th character"
            )
            continue
        if name in entries:
            warnings.append(f"{node.name}: duplicate part name after truncation, skipped")
            continue
        entries[name] = node

    by_lod: Dict[int, Dict[str, str]] = {1: {}, 2: {}, 3: {}}  # slot key -> name
    for name in entries:
        by_lod[_lod_of(name)][_slot_key(name)] = name
    for lod in (2, 3):
        for key, name in list(by_lod[lod].items()):
            if key not in by_lod[1]:
                warnings.append(f"{name}: skipped, no LOD1 part with the same name")
                del by_lod[lod][key]

    # Only parts that survived the LOD filtering are written. A skipped node (an
    # organising group, an orphaned LOD part) is removed from the hierarchy and its
    # matrix is composed into its children, so their world transforms stay put.
    entries = {n: entries[n] for lod_names in by_lod.values() for n in lod_names.values()}
    entry_of = {id(node): name for name, node in entries.items()}
    by_node_name = {node.name: node for node in scene.nodes}
    resolved: Dict[str, Tuple[Optional[str], list]] = {}
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
        resolved[name] = (parent_name, matrix)

    def parent_record_name(name: str) -> str:
        parent_name = resolved[name][0]
        if parent_name is None:
            return "WORLD"
        lod = _lod_of(name)
        if _lod_of(parent_name) != lod:
            candidate = parent_name[:3] + str(lod) + parent_name[4:]
            if candidate in entries:
                return candidate
        return parent_name

    # Parents must precede children in the LOD1 slot order.
    pending = list(by_lod[1].items())
    placed: List[Tuple[str, str]] = []
    placed_names = set()
    lod1_names = set(by_lod[1].values())
    while pending:
        progress = False
        for item in list(pending):
            key, name = item
            parent = parent_record_name(name)
            if parent == "WORLD" or parent not in lod1_names or parent in placed_names:
                placed.append(item)
                placed_names.add(name)
                pending.remove(item)
                progress = True
        if not progress:  # cycle: keep file order rather than loop forever
            warnings.append("parent cycle among LOD1 parts; slot order may be wrong")
            placed.extend(pending)
            break

    geocount = len(placed)
    model = vdf_file.ParsedVDF()
    model.plan = list(vdf_file.new_empty_plan())
    meta = scene.meta or {}
    model.vdfc_name = str(meta.get("name") or os.path.splitext(os.path.basename(path))[0])[:15]
    model.vdfc_vehicletype = int(meta.get("vehicletype", 0))
    model.vdfc_vehiclesize = int(meta.get("vehiclesize", 0))
    lod_dists = list(meta.get("lod_dists") or [])
    lod_dists = (lod_dists + [0.0] * 5)[:5]
    model.vdfc_lod_dists = [float(v) for v in lod_dists]
    model.vdfc_mass = float(meta.get("mass", 1.0))
    model.vdfc_multiplyer = float(meta.get("multiplyer", 1.0))
    model.vdfc_drag = float(meta.get("drag", 0.0))
    model.vdfc_null = 0
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
    null_record = vdf_classes.serialize_section(null_geo)

    records = [null_record] * (BAND_COUNT * geocount)
    all_points = []
    for slot, (key, lod1_name) in enumerate(placed):
        for lod, band in zip((1, 2, 3), LOD_BANDS):
            name = by_lod[lod].get(key)
            if name is None:
                continue
            node = entries[name]
            record = vdf_classes.GEOData()
            record.name = name
            record.parent = parent_record_name(name)
            props = node.props or {}
            part_type = props.get("type")
            if part_type is None:
                part_type = 70 if node.kind == KIND_HARDPOINT else 0
            record.type = int(part_type)
            record.geoflags = int(props.get("flags", 0))
            matrix12 = axes.maya_matrix_to_bz12(resolved[name][1])
            if record.type == SPINNER_TYPE and props.get("spin") is not None:
                matrix12[0:3] = [float(v) for v in props["spin"]]
            record.matrix = matrix12
            center, radius, half = _bounds_from_mesh(node.mesh)
            has_mesh = node.mesh is not None and bool(node.mesh.faces)
            if has_mesh:  # bounds always follow the geometry that is being written
                record.geocenter, record.sphereradius, record.boxhalfheight = center, radius, half
            else:
                record.geocenter = list(props.get("geocenter") or center)
                record.sphereradius = float(props.get("sphereradius", radius))
                record.boxhalfheight = list(props.get("boxhalfheight") or half)
            records[band * geocount + slot] = vdf_classes.serialize_section(record)

            if node.mesh is not None and node.mesh.faces:
                bad = 0
                for face in node.mesh.faces:
                    non_planar, concave = axes.polygon_issues(
                        [node.mesh.positions[i] for i in face]
                    )
                    bad += int(non_planar or concave)
                if bad:
                    warnings.append(
                        f"{name}: {bad} non-planar or concave face(s); GEO needs convex planar polygons"
                    )
                geo = mesh_to_geo(name, node.mesh)
                with open(os.path.join(folder, name + ".geo"), "wb") as handle:
                    GeoSerializer(handle).write(geo)
                if lod == 1:
                    all_points.extend(
                        axes.maya_point_to_bz(
                            axes.transform_point(_world(resolved, entries, name), p)
                        )
                        for p in node.mesh.positions
                    )
    model.records = records

    colp = meta.get("colp")
    if not colp or len(colp) != 12:
        colp = _default_colp(all_points)
    model.colp_data = [float(v) for v in colp]

    with open(path, "wb") as handle:
        handle.write(vdf_file.serialize_vdf(model))
    return warnings


def _world(resolved, entries: Dict[str, Node], name: str):
    matrix = axes.identity4()
    seen = set()
    current: Optional[str] = name
    while current is not None and current not in seen:
        seen.add(current)
        parent, local = resolved[current]
        matrix = axes.matmul(local, matrix)
        current = parent
    return matrix


def _default_colp(points_bz) -> List[float]:
    """COLP order: Y max/min (out,in,in,out), X ..., Z ...; inner = outer."""
    if not points_bz:
        return [0.0] * 12
    pts = np.array(points_bz, dtype=float)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return [
        hi[1], hi[1], lo[1], lo[1],
        hi[0], hi[0], lo[0], lo[0],
        hi[2], hi[2], lo[2], lo[2],
    ]
