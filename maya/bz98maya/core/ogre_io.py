"""Redux Ogre binary .mesh <-> neutral scene (static geometry only). Maya-free.

Reading goes through the toolkit's pure importer
(``ogrefast.pure.import_compat.KenshiObjectSerializer.load_mesh``, which wraps
``bzrmodelporter.ogremesh_serializer.MeshSerializer``) and decodes vertex
buffers with its element decoder. Writing builds ``ogrefast.pure.model``
objects and hands them to ``ogrefast.pure.serializer.OgreMeshSerializer``.

Ogre space equals the neutral / Maya space (see ``axes.py``), so positions and
normals are copied as is and only V is flipped.

Not round-tripped: skeleton links, bone assignments, poses, mesh animations,
LOD levels, edge lists, vertex colours, tangents / binormals (a mesh that
carries skeleton data is rejected on read). Hierarchy is flattened: every
node's world transform is baked into its vertices on write. No .material file
is written (the toolkit's material writer needs Blender).
"""

from __future__ import annotations

import os
import struct
from typing import Dict, List, Tuple

import numpy as np

from . import axes
from .model import KIND_PART, Mesh, Node, Scene
from .toolkit import ensure_toolkit


def _modules():
    ensure_toolkit()
    from bz98tools.ogrefast.pure import import_compat, model as wire
    from bz98tools.ogrefast.pure.serializer import OgreMeshSerializer

    return import_compat, wire, OgreMeshSerializer


# --- read ------------------------------------------------------------------

def read_ogre_mesh(path: str) -> Scene:
    import_compat, _, _ = _modules()
    serializer = import_compat.KenshiObjectSerializer()
    folder, filename = os.path.split(os.path.abspath(path))
    serializer.add_resource_location(folder)
    mesh_data = serializer.load_mesh(filename)  # raises UnsupportedPureImport for rigged meshes

    scene = Scene()
    used: Dict[str, int] = {}
    for submesh in mesh_data.get_submeshes():
        geometry = submesh._wire_geometry
        positions = import_compat._decode_semantic(geometry, {"POSITION"})
        if positions is None:
            scene.warnings.append(f"submesh {submesh.submesh_name}: no POSITION data, skipped")
            continue
        normals = import_compat._decode_semantic(geometry, {"NORMAL"})
        uv_elements = sorted(
            import_compat._elements_by_semantic(geometry, {"TEXCOORD", "TEXTURE_COORDINATES"}),
            key=lambda element: element.index,
        )

        indices = [int(i) for i in submesh.faces]
        mesh = Mesh()
        mesh.positions = [axes.ogre_point_to_maya(p) for p in positions[:, :3]]
        mesh.faces = [tuple(indices[i : i + 3]) for i in range(0, len(indices) - 2, 3)]
        mesh.face_materials = [submesh.material] * len(mesh.faces)
        if normals is not None:
            mesh.normals = [axes.ogre_point_to_maya(normals[i][:3]) for i in indices]
        for element in uv_elements:
            values = import_compat._decode_element(geometry, element)
            if values.shape[1] < 2:
                continue
            mesh.uv_sets.append([axes.ogre_uv_to_maya(values[i][:2]) for i in indices])

        base = submesh.submesh_name or f"submesh{submesh.index}"
        count = used.get(base, 0)
        used[base] = count + 1
        name = base if count == 0 else f"{base}_{count}"
        scene.nodes.append(Node(name=name, parent=None, kind=KIND_PART, mesh=mesh))
    return scene


# --- write -----------------------------------------------------------------

def _world_matrices(scene: Scene) -> Dict[str, list]:
    by_name = {node.name: node for node in scene.nodes}
    cache: Dict[str, list] = {}

    def world(node: Node, depth: int = 0):
        if node.name in cache:
            return cache[node.name]
        matrix = node.matrix
        if node.parent in by_name and depth < 256:
            matrix = axes.matmul(world(by_name[node.parent], depth + 1), node.matrix)
        cache[node.name] = matrix
        return matrix

    for node in scene.nodes:
        world(node)
    return cache


def _build_submesh(wire, name: str, material: str, rows: List[tuple], indices: List[int], uv_count: int):
    declaration = [
        wire.VertexElement("POSITION", 0, 0, "FLOAT3"),
        wire.VertexElement("NORMAL", 0, 12, "FLOAT3"),
    ]
    offset = 24
    for k in range(uv_count):
        declaration.append(wire.VertexElement("TEXCOORD", 0, offset, "FLOAT2", index=k))
        offset += 8
    fmt = "<" + "f" * (6 + 2 * uv_count)
    data = b"".join(struct.pack(fmt, *row) for row in rows)
    geometry = wire.GeometryData(
        vertex_count=len(rows),
        declaration=declaration,
        buffers=[wire.VertexBuffer(0, offset, data)],
    )
    return wire.SubMeshData(
        material_name=material, indices=indices, name=name, geometry=geometry
    )


def write_ogre_mesh(scene: Scene, path: str) -> List[str]:
    """Write ``path`` (.mesh). One submesh per (node, material). Returns warnings."""
    _, wire, OgreMeshSerializer = _modules()
    warnings: List[str] = list(scene.warnings)
    worlds = _world_matrices(scene)

    submeshes = []
    all_points: List[Tuple[float, float, float]] = []
    for node in scene.nodes:
        mesh = node.mesh
        if mesh is None or not mesh.faces:
            continue
        world = worlds[node.name]
        flip = axes.determinant3(world) < 0.0
        uv_count = len(mesh.uv_sets)
        has_normals = mesh.normals is not None and len(mesh.normals) == mesh.loop_count()

        # material -> (vertex rows, vertex lookup, indices)
        groups: Dict[str, Tuple[List[tuple], Dict[tuple, int], List[int]]] = {}
        loop = 0
        for face_index, face in enumerate(mesh.faces):
            material = mesh.face_materials[face_index] if mesh.face_materials else ""
            rows, lookup, indices = groups.setdefault(material, ([], {}, []))
            points = [axes.transform_point(world, mesh.positions[i]) for i in face]
            flat_normal = axes.newell_normal(points)
            if flip:
                flat_normal = (-flat_normal[0], -flat_normal[1], -flat_normal[2])
            corner_ids = []
            for corner, vi in enumerate(face):
                if has_normals:
                    normal = axes.transform_normal(world, mesh.normals[loop + corner])
                else:
                    normal = flat_normal
                uvs = [
                    axes.maya_uv_to_ogre(mesh.uv_sets[k][loop + corner]) for k in range(uv_count)
                ]
                row = tuple(float(v) for v in points[corner]) + tuple(normal)
                for uv in uvs:
                    row += (float(uv[0]), float(uv[1]))
                key = tuple(round(v, 6) for v in row)
                if key not in lookup:
                    lookup[key] = len(rows)
                    rows.append(row)
                    all_points.append(points[corner])
                corner_ids.append(lookup[key])
            loop += len(face)
            if flip:
                corner_ids.reverse()
            for i in range(1, len(corner_ids) - 1):  # fan triangulation
                indices.extend((corner_ids[0], corner_ids[i], corner_ids[i + 1]))

        for material, (rows, _lookup, indices) in groups.items():
            if len(groups) == 1:
                sub_name = node.name
            else:
                sub_name = f"{node.name}_{material}" if material else node.name
            submeshes.append(
                _build_submesh(wire, sub_name, material, rows, indices, uv_count)
            )

    if not submeshes:
        raise ValueError("nothing to export: no mesh geometry in the selection")

    pts = np.array(all_points, dtype=float)
    bounds = wire.MeshBounds(
        tuple(float(v) for v in pts.min(axis=0)),
        tuple(float(v) for v in pts.max(axis=0)),
        float(np.linalg.norm(pts, axis=1).max()),
    )
    mesh_data = wire.MeshData(submeshes=submeshes, bounds=bounds)
    OgreMeshSerializer().dump(mesh_data, path)
    return warnings
