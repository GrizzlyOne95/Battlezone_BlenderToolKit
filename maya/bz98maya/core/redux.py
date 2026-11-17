"""Redux export (Maya-free): the BZR ``.mesh`` writer and the Legacy + Redux auto-port.

``write_bzr_mesh`` is Blender's pure static mesh export
(``ogrefast/pure/blender_exporter.py``) with the Blender object collection replaced by
the neutral scene: the per-loop arrays are built in Blender's axes (Z up) and handed to
the toolkit's own ``pose_compat.SubMeshData.set_vertex`` / ``MeshData`` / serializer, so
vertex layout, split / dedupe rules, multi-UV handling, the ``_blender_to_ogre_xyz`` and
``1 - v`` conversions, bounds, and the BZR-compatible vertex-buffer organisation come
from the toolkit, not from this module. Tangents are the one thing Blender takes from its
own ``calc_tangents`` (MikkTSpace); ``calc_tangents`` here is a per-loop UV-derivative
tangent frame with the same conventions (``bitangent = sign * cross(normal, tangent)``).

Differences from Blender, all deliberate: a Maya mesh with several materials becomes one
submesh per material (Blender writes one submesh per object, first material), hierarchy
transforms are baked from the world matrix (Blender applies only the object's own
transform), and vertex colours are not exported.

``auto_port`` calls the toolkit's ``ogre_autoport.auto_port_bz98_to_ogre`` unchanged.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import axes, export_options, material
from .model import Mesh, Node, Scene
from .toolkit import ensure_toolkit

_T = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])  # Maya -> Blender axes


def map_tangent_format(export_tangents, export_binormals, zero_tangents_binormals, tangent_parity) -> str:
    """ogrefast/backend.py ``_map_tangent_format``."""
    if zero_tangents_binormals:
        return "ZERO"
    if not export_tangents:
        return "TANGENT_0"
    if export_binormals:
        return "ALL" if tangent_parity else "TANGENT_3"
    return "TANGENT_4"


def _modules():
    ensure_toolkit()
    from bz98tools.ogrefast.pure import kenshi_compat, pose_compat

    return kenshi_compat, pose_compat


def triangles(mesh: Mesh) -> List[Tuple[int, int]]:
    """Fan triangulation as (face index, k): corners 0, k, k + 1 of that face."""
    out = []
    for face_index, face in enumerate(mesh.faces):
        for k in range(1, len(face) - 1):
            out.append((face_index, k))
    return out


def _loop_starts(mesh: Mesh) -> List[int]:
    """First loop index of every face."""
    starts, loop = [], 0
    for face in mesh.faces:
        starts.append(loop)
        loop += len(face)
    return starts


def calc_tangents(positions, vertex_ids, normals, uvs):
    """Per-loop (tangents, bitangent_signs) in Blender space for a triangle list.

    ``vertex_ids`` is (L,) vertex index per loop, loops grouped in threes. Loops sharing a
    vertex, UV and normal are smoothed together.
    """
    loops = len(vertex_ids)
    tan = np.zeros((loops, 3))
    bit = np.zeros((loops, 3))
    for t in range(0, loops, 3):
        i0, i1, i2 = t, t + 1, t + 2
        p0, p1, p2 = (positions[vertex_ids[i]] for i in (i0, i1, i2))
        e1, e2 = p1 - p0, p2 - p0
        du1, dv1 = uvs[i1] - uvs[i0]
        du2, dv2 = uvs[i2] - uvs[i0]
        det = du1 * dv2 - du2 * dv1
        if abs(det) < 1e-20:
            continue
        r = 1.0 / det
        tangent = (e1 * dv2 - e2 * dv1) * r
        bitangent = (e2 * du1 - e1 * du2) * r
        for i in (i0, i1, i2):
            tan[i] += tangent
            bit[i] += bitangent

    groups: Dict[tuple, List[int]] = {}
    for i in range(loops):
        key = (int(vertex_ids[i]), round(float(uvs[i][0]), 5), round(float(uvs[i][1]), 5)) + tuple(
            np.round(normals[i], 4)
        )
        groups.setdefault(key, []).append(i)

    tangents = np.zeros((loops, 3), dtype=np.float32)
    signs = np.ones(loops, dtype=np.float32)
    for members in groups.values():
        t = tan[members].sum(axis=0)
        b = bit[members].sum(axis=0)
        for i in members:
            n = normals[i]
            tt = t - n * float(np.dot(n, t))
            length = float(np.linalg.norm(tt))
            if length < 1e-12:  # no UV area: any perpendicular
                axis = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
                tt = np.cross(n, axis)
                length = float(np.linalg.norm(tt)) or 1.0
            tt = tt / length
            tangents[i] = tt
            signs[i] = 1.0 if float(np.dot(np.cross(n, tt), b)) >= 0.0 else -1.0
    return tangents, signs


def _submesh_arrays(positions_b, loops, normals_b, uv_sets, tangent_format):
    """The ``set_vertex`` argument arrays for one triangle list (Blender's _collect_submesh)."""
    loop_count = len(loops)
    nd_vert_indices = np.asarray(loops, dtype=np.int32)
    nd_loop_indices = np.arange(loop_count, dtype=np.int32)
    nd_positions = np.asarray(positions_b, dtype=np.float32).reshape(-1, 3)
    nd_normals = np.asarray(normals_b, dtype=np.float32).reshape(-1, 3)
    if not uv_sets:
        nd_texcoords = np.empty(2, dtype=np.float32)
    elif len(uv_sets) == 1:
        nd_texcoords = np.asarray(uv_sets[0], dtype=np.float32).reshape(-1, 2)
    else:
        nd_texcoords = np.stack([np.asarray(u, dtype=np.float32).reshape(-1, 2) for u in uv_sets], axis=0)

    effective = tangent_format if uv_sets else "TANGENT_0"
    tangent_dimensions = 4 if effective in {"TANGENT_4", "ALL", "FLIPPED"} else 3
    if effective != "TANGENT_0":
        t, signs = calc_tangents(
            nd_positions.astype(float), nd_vert_indices, nd_normals.astype(float),
            np.asarray(uv_sets[0], dtype=float).reshape(-1, 2),
        )
        nd_tangents = t
        nd_signs = signs
        b = np.cross(nd_normals, t) * signs.reshape(-1, 1)  # sign * cross(normal, tangent)
        if effective == "ALL":
            nd_bitangents = b * signs.reshape(-1, 1)
        elif effective == "TANGENT_4":
            nd_bitangents = np.empty(3, dtype=np.float32)
        elif effective == "FLIPPED":
            nd_bitangents = -b * signs.reshape(-1, 1)
            nd_signs = -signs
        elif effective == "ZERO":
            nd_tangents = np.zeros((loop_count, 3), dtype=np.float32)
            nd_bitangents = np.zeros((loop_count, 3), dtype=np.float32)
            nd_signs = np.zeros(loop_count, dtype=np.float32)
        else:
            nd_bitangents = b
    else:
        nd_tangents = np.empty(3, dtype=np.float32)
        nd_signs = np.empty(1, dtype=np.float32)
        nd_bitangents = np.empty(3, dtype=np.float32)
    return dict(
        nd_vert_indices=nd_vert_indices, nd_loop_indices=nd_loop_indices, nd_positions=nd_positions,
        nd_normals=nd_normals, nd_tangents=nd_tangents, nd_bitangent_signs=nd_signs,
        nd_bitangents=nd_bitangents, nd_texcoords=nd_texcoords,
        nd_colors=np.empty(4, dtype=np.float32), nd_alphas=np.empty(4, dtype=np.float32),
        tangent_dimensions=tangent_dimensions,
    )


def _baked(node: Node, world: np.ndarray, apply_transform: bool):
    """(positions, per-loop normals, flip) in Maya space for ``node``'s mesh."""
    mesh = node.mesh
    if apply_transform:
        pos = [axes.transform_point(world, p) for p in mesh.positions]
        flip = axes.determinant3(world) < 0.0
    else:
        pos, flip = [tuple(p) for p in mesh.positions], False
    return pos, flip


MAX_INFLUENCES = 3  # Blender's _collect_vertex_bone_assignments keeps the three heaviest


def bone_assignments(weights, id_map: Dict[int, int], renormalize: bool = True, dropped: Optional[List[int]] = None):
    """Blender's ``_collect_vertex_bone_assignments`` on neutral weights: [(vertex, bone id, weight)].

    Per vertex the influences are sorted by weight, cut to the three heaviest and, with
    ``renormalize``, scaled to sum to one (zero weights dropped); bone ids go through ``id_map``.
    Influences on bones outside ``id_map`` are dropped and counted in ``dropped[0]`` when given.
    """
    out = []
    for vertex, influences in enumerate(weights):
        if dropped is not None:
            dropped[0] += sum(1 for b, w in influences if b not in id_map and w > 0)
        valid = [(id_map[b], float(w)) for b, w in influences if b in id_map]
        valid.sort(key=lambda item: item[1], reverse=True)
        valid = valid[:MAX_INFLUENCES]
        if renormalize and valid:
            total = sum(w for _b, w in valid)
            if total > 0:
                valid = [(b, w / total) for b, w in valid if w != 0]
        for bone, weight in valid:
            out.append((vertex, bone, weight))
    return out


def build_submeshes(scene: Scene, options: Dict[str, Any], id_map: Optional[Dict[int, int]] = None,
                    warnings: Optional[List[str]] = None) -> List[Tuple[Any, str]]:
    """``[(SubMeshData, material name)]`` for every mesh in the scene, as Blender's collector."""
    warnings = warnings if warnings is not None else []
    kenshi, pose_compat = _modules()
    tangent_format = map_tangent_format(
        options.get("export_tangents", True), options.get("export_binormals", True),
        options.get("zero_tangents_binormals", False), options.get("tangent_parity", True),
    )
    apply_transform = bool(options.get("apply_transform", True))
    worlds = scene_world_matrices(scene)
    out = []
    skinned_warned: set = set()
    for node in scene.nodes:
        mesh = node.mesh
        if mesh is None or not mesh.faces:
            continue
        positions, flip = _baked(node, worlds[node.name], apply_transform)
        world = worlds[node.name] if apply_transform else axes.identity4()
        is_identity = bool(np.allclose(np.array(world), np.identity(4)))
        normals = mesh.normals if mesh.normals is not None and len(mesh.normals) == mesh.loop_count() else None
        starts = _loop_starts(mesh)

        groups: Dict[str, List[Tuple[int, int]]] = {}
        for tri in triangles(mesh):
            name = mesh.face_materials[tri[0]] if mesh.face_materials else ""
            groups.setdefault(name, []).append(tri)

        for material_name, tris in groups.items():
            loops, loop_normals, uv_loops = [], [], [[] for _ in mesh.uv_sets]
            for face_index, k in tris:
                face = mesh.faces[face_index]
                corner_ids = [0, k + 1, k] if flip else [0, k, k + 1]
                flat = None
                if normals is None:
                    flat = axes.newell_normal([positions[face[i]] for i in corner_ids])
                for i in corner_ids:
                    loops.append(face[i])
                    if normals is not None:
                        n = normals[starts[face_index] + i]
                        loop_normals.append(n if is_identity else axes.transform_normal(world, n))
                    else:
                        loop_normals.append(flat)
                    for s, uvs in enumerate(mesh.uv_sets):
                        uv_loops[s].append(uvs[starts[face_index] + i])
            positions_b = (np.asarray(positions, dtype=float).reshape(-1, 3)) @ _T.T
            normals_b = np.asarray(loop_normals, dtype=float).reshape(-1, 3) @ _T.T
            arrays = _submesh_arrays(positions_b, loops, normals_b, uv_loops, tangent_format)

            sub = pose_compat.SubMeshData()
            sub.index = len(out)
            sub.submesh_name = node.name if len(groups) == 1 or not material_name else f"{node.name}_{material_name}"
            sub.material = material_name or node.name
            out_indices = sub.set_vertex(optimize=bool(options.get("mesh_optimize", True)), **arrays)
            assignments = []
            if id_map is not None and mesh.weights is not None:
                dropped = [0]
                assignments = [
                    kenshi.BoneAssignmentData(v, b, w)
                    for v, b, w in bone_assignments(mesh.weights, id_map, options.get("renormalize_weights", True),
                                                    dropped)
                ]
                if dropped[0] and node.name not in skinned_warned:
                    skinned_warned.add(node.name)
                    warnings.append("%s: %d skin influence(s) on joints outside the exported skeleton dropped%s" % (
                        node.name, dropped[0], " and the rest renormalised" if options.get("renormalize_weights", True) else ""))
            elif mesh.weights is not None and node.name not in skinned_warned:
                skinned_warned.add(node.name)
                warnings.append("%s: skinned mesh exported without its skin weights (no skeleton is exported)" % node.name)
            sub.set_bone_assignments(assignments, out_indices)
            out.append((sub, sub.material))
    return out


def scene_world_matrices(scene: Scene) -> Dict[str, list]:
    by_name = {n.name: n for n in scene.nodes}
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


def default_material_info(scene: Scene, name: str) -> dict:
    info = dict(scene.materials.get(name) or {})
    info.setdefault("ambient", list(material.DEFAULT_COLOUR))
    info.setdefault("diffuse", list(material.DEFAULT_COLOUR))
    info.setdefault("specular", [0.0, 0.0, 0.0])
    info.setdefault("emissive", [0.0, 0.0, 0.0])
    return info


def write_bzr_mesh(scene: Scene, path: str, options: Optional[Dict[str, Any]] = None) -> List[str]:
    """Write ``path`` (.mesh) and, with ``export_materials``, its ``.material``. Returns warnings."""
    kenshi_compat, _ = _modules()
    options = export_options.merged_options("MESH", options)
    if not path.lower().endswith(".mesh"):
        path += ".mesh"
    warnings = list(scene.warnings)

    if options.get("batch_export"):
        written = 0
        folder = os.path.dirname(path) or "."
        worlds = scene_world_matrices(scene)
        for node in scene.nodes:
            if node.mesh is None or not node.mesh.faces:
                continue
            # Each file is one part at its world transform (as the single-file path bakes it), with the shared skeleton.
            alone = dataclasses.replace(node, parent=None, matrix=worlds[node.name])
            single = Scene(nodes=[alone], materials=scene.materials, skeleton=scene.skeleton)
            sub_options = dict(options, batch_export=False)
            warnings += write_bzr_mesh(single, os.path.join(folder, node.name + ".mesh"), sub_options)
            written += 1
        if not written:
            raise ValueError("nothing to export: no mesh geometry in the selection")
        return warnings

    skeleton = scene.skeleton if options.get("export_skeleton", True) else None
    id_map = None
    if skeleton is not None:
        from . import skeleton_io

        id_map = skeleton_io.renumbered(skeleton)
    submeshes = build_submeshes(scene, options, id_map, warnings)
    if not submeshes:
        raise ValueError("nothing to export: no mesh geometry in the selection")
    serializer = kenshi_compat.KenshiObjectSerializer()
    mesh_data = serializer.create_mesh(os.path.basename(path))
    mesh_data.set_submeshes([sub for sub, _ in submeshes])
    skeleton_file = os.path.splitext(os.path.basename(path))[0] + ".skeleton"
    if skeleton is not None:
        mesh_data.set_linked_skeleton_name(skeleton_file)
    serializer.save_mesh(mesh_data, path, kenshi_compat.MeshVersion.V_1_10)
    if skeleton is not None:
        write_skeleton_file(skeleton, os.path.join(os.path.dirname(path), skeleton_file), id_map,
                            bool(options.get("export_animation", False)))

    if options.get("export_materials", True):
        materials: Dict[str, dict] = {}
        for _sub, name in submeshes:
            materials.setdefault(name, default_material_info(scene, name))
        material.write_material_file(
            path, materials, bool(options.get("overwrite_material")), bool(options.get("copy_textures"))
        )
    return warnings


def write_skeleton_file(skeleton, path: str, id_map: Dict[int, int], with_animation: bool) -> None:
    """The ``.skeleton``: bones renumbered 0..n-1 in id order (as Blender does); clips only on request."""
    import dataclasses

    from . import skeleton_io

    bones = [dataclasses.replace(b, id=id_map[b.id]) for b in skeleton.bones]
    skeleton_io.write_skeleton(
        dataclasses.replace(skeleton, bones=bones, clips=list(skeleton.clips) if with_animation else []), path
    )


# --- Legacy + Redux ------------------------------------------------------------------

def auto_port(legacy_path: str, options: Dict[str, Any]) -> Dict[str, Any]:
    """Generate the Redux files for an exported .vdf / .sdf / .geo (Blender's auto-port, unchanged)."""
    ensure_toolkit()
    from bz98tools import ogre_autoport

    dest = str(options.get("ogre_dest_dir") or "").strip()
    if dest and not options.get("ogre_nowrite"):
        os.makedirs(dest, exist_ok=True)  # the porter needs the folder to exist
    result = ogre_autoport.auto_port_bz98_to_ogre(legacy_path, export_options.autoport_options(options))
    return result
