"""Neutral scene <-> Maya scene, plus the four import / export entry points.

Needs Maya (``maya.cmds`` and ``maya.api.OpenMaya``, API 2.0). Everything that
can be done without Maya lives in ``bz98maya.core``. The neutral model is
already in Maya's coordinate convention (see ``core/axes.py`` for the full
axis / handedness / UV derivation and its single comment block), so this
module only moves data; it never flips axes.

Transforms and points go through the API (internal units, centimetres) so the
scene's UI linear unit does not change the numbers: 1 BZ unit = 1 internal
unit.

Per-node data Maya has no slot for is kept in string / int attributes on the
transform: ``bzName`` (original part name), ``bzType`` and ``bzFlags`` (GEO
class and object flags), ``bzProps`` (JSON: geocenter, sphereradius,
boxhalfheight, spin). Vehicle header values and the COLP box live in a JSON
``bzMeta`` attribute on the group created by a VDF import. Materials carry
``bzMaterial`` with the original (possibly Maya-illegal) name.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

import maya.api.OpenMaya as om
import maya.cmds as cmds

from .core import axes
from .core.model import (
    KIND_EMPTY,
    KIND_HARDPOINT,
    KIND_LOD,
    KIND_PART,
    Mesh,
    Node,
    Scene,
)
from .core.toolkit import ensure_toolkit

# Keep the native Ogre backend probe (a global side effect) out of Maya.
ensure_toolkit(stub_ogrefast=True)

from .core import ogre_io, sdf_io, vdf_io  # noqa: E402

DEFAULT_CAMERAS = ("persp", "top", "front", "side", "bottom", "back", "left")
TEXTURE_EXTENSIONS = (".png", ".tga", ".dds", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
PLACE2D_ATTRS = (
    "coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV", "stagger",
    "wrapU", "wrapV", "repeatUV", "offset", "rotateUV", "noiseUV",
    "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne",
)


# --- small helpers ---------------------------------------------------------

def _valid_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name or "")
    if not cleaned or cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def _dag_path(node: str) -> "om.MDagPath":
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _depend_node(node: str) -> "om.MObject":
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDependNode(0)


def _uuid(node: str) -> str:
    return cmds.ls(node, uuid=True)[0]


def _long_name(uuid: str) -> str:
    return cmds.ls(uuid, long=True)[0]


def _matrix_to_mmatrix(matrix) -> "om.MMatrix":
    return om.MMatrix(axes.matrix_to_row_vector_flat(matrix))


def _mmatrix_to_matrix(mmatrix: "om.MMatrix"):
    flat = [mmatrix.getElement(row, col) for row in range(4) for col in range(4)]
    return axes.row_vector_flat_to_matrix(flat)


def _set_local_matrix(node: str, matrix) -> None:
    fn = om.MFnTransform(_dag_path(node))
    fn.setTransformation(om.MTransformationMatrix(_matrix_to_mmatrix(matrix)))


def _world_matrix(node: str):
    return _mmatrix_to_matrix(_dag_path(node).inclusiveMatrix())


def _add_attr(node: str, name: str, value) -> None:
    if not cmds.attributeQuery(name, node=node, exists=True):
        if isinstance(value, str):
            cmds.addAttr(node, longName=name, dataType="string")
        else:
            cmds.addAttr(node, longName=name, attributeType="long")
    if isinstance(value, str):
        cmds.setAttr("%s.%s" % (node, name), value, type="string")
    else:
        cmds.setAttr("%s.%s" % (node, name), int(value))


def _get_attr(node: str, name: str, default=None):
    if cmds.attributeQuery(name, node=node, exists=True):
        return cmds.getAttr("%s.%s" % (node, name))
    return default


def _face_components(shape: str, face_ids: List[int]) -> List[str]:
    """Compact 'shape.f[a:b]' strings for a sorted list of face ids."""
    out = []
    start = prev = None
    for fid in face_ids:
        if start is None:
            start = prev = fid
        elif fid == prev + 1:
            prev = fid
        else:
            out.append("%s.f[%d:%d]" % (shape, start, prev))
            start = prev = fid
    if start is not None:
        out.append("%s.f[%d:%d]" % (shape, start, prev))
    return out


# --- materials -------------------------------------------------------------

def _find_texture(material: str, folder: Optional[str]) -> Optional[str]:
    if not folder or not os.path.isdir(folder):
        return None
    stems = {material.lower(), os.path.basename(material).lower()}
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    for entry in entries:
        stem, ext = os.path.splitext(entry)
        if ext.lower() in TEXTURE_EXTENSIONS and stem.lower() in stems:
            return os.path.join(folder, entry)
    return None


def _shading_group(material: str, texture_folder: Optional[str], cache: Dict[str, str]) -> str:
    if material in cache:
        return cache[material]
    safe = _valid_name(material)
    sg_name = safe + "_SG"
    if cmds.objExists(sg_name) and cmds.nodeType(sg_name) == "shadingEngine":
        cache[material] = sg_name
        return sg_name
    shader = cmds.shadingNode("lambert", asShader=True, name=safe + "_mat")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    _add_attr(shader, "bzMaterial", material)
    texture = _find_texture(material, texture_folder)
    if texture:
        file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=safe + "_tex")
        place = cmds.shadingNode("place2dTexture", asUtility=True, name=safe + "_place2d")
        cmds.connectAttr(place + ".outUV", file_node + ".uvCoord", force=True)
        cmds.connectAttr(place + ".outUvFilterSize", file_node + ".uvFilterSize", force=True)
        for attr in PLACE2D_ATTRS:
            cmds.connectAttr("%s.%s" % (place, attr), "%s.%s" % (file_node, attr), force=True)
        cmds.setAttr(file_node + ".fileTextureName", texture, type="string")
        cmds.connectAttr(file_node + ".outColor", shader + ".color", force=True)
    cache[material] = sg
    return sg


def _material_name_of(shading_group: str) -> str:
    """The BZ material / texture name a shading group stands for ('' = none)."""
    if shading_group == "initialShadingGroup":
        return ""
    shaders = cmds.listConnections(shading_group + ".surfaceShader", source=True, destination=False) or []
    shader = shaders[0] if shaders else None
    if shader:
        stored = _get_attr(shader, "bzMaterial")
        if stored:
            return stored
        files = cmds.listConnections(shader + ".color", source=True, destination=False, type="file") or []
        if files:
            path = cmds.getAttr(files[0] + ".fileTextureName") or ""
            if path:
                return os.path.splitext(os.path.basename(path))[0]
    name = shader or shading_group
    for suffix in ("_mat", "_SG"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


# --- neutral -> Maya -------------------------------------------------------

def _create_mesh(transform: str, mesh: Mesh, texture_folder: Optional[str], cache: Dict[str, str]) -> None:
    points = om.MPointArray([om.MPoint(p[0], p[1], p[2]) for p in mesh.positions])
    counts = om.MIntArray([len(face) for face in mesh.faces])
    connects = om.MIntArray([index for face in mesh.faces for index in face])
    fn = om.MFnMesh()
    fn.create(points, counts, connects, parent=_depend_node(transform))
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    if not shapes:
        raise RuntimeError("mesh creation produced no shape under %s" % transform)
    shape = shapes[0]
    shape = cmds.rename(shape, transform.split("|")[-1] + "Shape")
    shape_long = cmds.ls(shape, long=True)[0]
    fn = om.MFnMesh(_dag_path(shape_long))

    cmds.sets(shape_long, edit=True, forceElement="initialShadingGroup")

    for set_index, loops in enumerate(mesh.uv_sets):
        if len(loops) != len(connects):
            continue
        if set_index == 0:
            uv_set = fn.currentUVSetName()
        else:
            uv_set = fn.createUVSet("uvSet%d" % (set_index + 1))
        lookup = {}
        us, vs, ids = om.MFloatArray(), om.MFloatArray(), om.MIntArray()
        for u, v in loops:
            key = (round(u, 6), round(v, 6))
            if key not in lookup:
                lookup[key] = len(us)
                us.append(u)
                vs.append(v)
            ids.append(lookup[key])
        fn.setUVs(us, vs, uv_set)
        fn.assignUVs(counts, ids, uv_set)

    if mesh.normals is not None and len(mesh.normals) == len(connects):
        normals = om.MVectorArray()
        face_ids = om.MIntArray()
        vertex_ids = om.MIntArray()
        loop = 0
        for face_index, face in enumerate(mesh.faces):
            for vertex in face:
                n = mesh.normals[loop]
                normals.append(om.MVector(n[0], n[1], n[2]))
                face_ids.append(face_index)
                vertex_ids.append(vertex)
                loop += 1
        fn.setFaceVertexNormals(normals, face_ids, vertex_ids)

    by_material: Dict[str, List[int]] = {}
    for face_index, material in enumerate(mesh.face_materials):
        if material:
            by_material.setdefault(material, []).append(face_index)
    for material, face_ids in by_material.items():
        group = _shading_group(material, texture_folder, cache)
        cmds.sets(_face_components(shape_long, face_ids), edit=True, forceElement=group)


def _create_node(node: Node, root: str, texture_folder: Optional[str], cache: Dict[str, str]) -> str:
    """Create the transform under ``root``; returns its UUID."""
    name = _valid_name(node.name)
    if node.kind == KIND_HARDPOINT and node.mesh is None:
        transform = cmds.spaceLocator(name=name)[0]
        cmds.setAttr(transform + ".localScale", 0.5, 0.5, 0.5, type="double3")
        transform = cmds.parent(transform, root)[0]
    else:
        transform = cmds.createNode("transform", name=name, parent=root)
    transform = cmds.ls(transform, long=True)[0]
    if node.mesh is not None and node.mesh.faces:
        _create_mesh(transform, node.mesh, texture_folder, cache)
    _add_attr(transform, "bzName", node.name)
    props = dict(node.props or {})
    if "type" in props:
        _add_attr(transform, "bzType", int(props.pop("type")))
    if "flags" in props:
        _add_attr(transform, "bzFlags", int(props.pop("flags")))
    if props:
        _add_attr(transform, "bzProps", json.dumps(props))
    return _uuid(transform)


def build_scene(scene: Scene, group_name: str, texture_folder: Optional[str] = None) -> str:
    """Create the scene under a new group. Returns the group's long name."""
    root = cmds.group(empty=True, name=_valid_name(group_name))
    root = cmds.ls(root, long=True)[0]
    if scene.meta:
        _add_attr(root, "bzMeta", json.dumps(scene.meta))
    cache: Dict[str, str] = {}
    uuids: Dict[str, str] = {}
    for node in scene.nodes:
        uuids[node.name] = _create_node(node, root, texture_folder, cache)
    for node in scene.nodes:
        if node.parent and node.parent in uuids:
            cmds.parent(_long_name(uuids[node.name]), _long_name(uuids[node.parent]), relative=True)
    for node in scene.nodes:
        _set_local_matrix(_long_name(uuids[node.name]), node.matrix)
    return root


# --- Maya -> neutral -------------------------------------------------------

def _is_transform(node: str) -> bool:
    return "transform" in (cmds.nodeType(node, inherited=True) or [])


def _selected_transforms() -> List[str]:
    roots = []
    for item in cmds.ls(selection=True, long=True) or []:
        if _is_transform(item):
            roots.append(item)
        else:
            roots.extend(cmds.listRelatives(item, parent=True, fullPath=True) or [])
    return roots


def _all_roots() -> List[str]:
    return [
        node
        for node in cmds.ls(assemblies=True, long=True) or []
        if node.lstrip("|") not in DEFAULT_CAMERAS
    ]


def _classify(transform: str):
    """(kind, mesh shape or None) for a transform, or (None, None) to skip it."""
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, noIntermediate=True) or []
    mesh_shapes = [s for s in shapes if cmds.nodeType(s) == "mesh"]
    bz_type = _get_attr(transform, "bzType")
    if mesh_shapes:
        if bz_type is not None and int(bz_type) in vdf_io.HARDPOINT_TYPES:
            return KIND_HARDPOINT, mesh_shapes[0]
        return KIND_PART, mesh_shapes[0]
    if any(cmds.nodeType(s) == "locator" for s in shapes):
        return KIND_HARDPOINT, None
    if bz_type is not None:
        return (KIND_HARDPOINT if int(bz_type) in vdf_io.HARDPOINT_TYPES else KIND_EMPTY), None
    return None, None


def _extract_mesh(shape: str, warnings: List[str], label: str) -> Mesh:
    fn = om.MFnMesh(_dag_path(shape))
    mesh = Mesh()
    mesh.positions = [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kObject)]
    counts, connects = fn.getVertices()
    cursor = 0
    for count in counts:
        mesh.faces.append(tuple(connects[i] for i in range(cursor, cursor + count)))
        cursor += count
    loop_total = len(connects)

    normals = fn.getNormals(om.MSpace.kObject)
    _normal_counts, normal_ids = fn.getNormalIds()
    if len(normal_ids) == loop_total:
        mesh.normals = [(normals[i].x, normals[i].y, normals[i].z) for i in normal_ids]

    set_names = list(fn.getUVSetNames())
    valid_first = None
    for index, uv_set in enumerate(set_names):
        us, vs = fn.getUVs(uv_set)
        _uv_counts, uv_ids = fn.getAssignedUVs(uv_set)
        ok = len(us) > 0 and len(uv_ids) == loop_total
        if index == 0:
            valid_first = ok
        if not ok:
            warnings.append(
                "%s: UV set '%s' skipped (%s)"
                % (label, uv_set, "empty" if len(us) == 0 else "not every face-vertex has a UV")
            )
            continue
        if valid_first is False and not mesh.uv_sets:
            warnings.append(
                "%s: first UV set '%s' is unusable, so '%s' is written as UV0"
                % (label, set_names[0], uv_set)
            )
        mesh.uv_sets.append([(us[i], vs[i]) if i >= 0 else (0.0, 0.0) for i in uv_ids])

    shaders, face_shader = fn.getConnectedShaders(0)
    names = [_material_name_of(om.MFnDependencyNode(obj).name()) for obj in shaders]
    mesh.face_materials = [names[i] if 0 <= i < len(names) else "" for i in face_shader]
    if len(mesh.face_materials) != len(mesh.faces):
        mesh.face_materials = [""] * len(mesh.faces)
    return mesh


def _extract_triangulated(transform: str, shape: str, warnings: List[str], label: str) -> Mesh:
    """Read the mesh from a temporary triangulated duplicate (concave n-gons)."""
    duplicate = cmds.duplicate(transform, returnRootsOnly=True)[0]
    try:
        for child in cmds.listRelatives(duplicate, children=True, type="transform", fullPath=True) or []:
            cmds.delete(child)
        dup_shapes = cmds.listRelatives(duplicate, shapes=True, fullPath=True, noIntermediate=True) or []
        mesh_shapes = [s for s in dup_shapes if cmds.nodeType(s) == "mesh"]
        if not mesh_shapes:
            raise RuntimeError("could not duplicate %s for triangulation" % label)
        cmds.polyTriangulate(mesh_shapes[0], constructionHistory=False)
        return _extract_mesh(mesh_shapes[0], warnings, label)
    finally:
        cmds.delete(duplicate)


def collect_scene(triangulate: bool = False) -> Scene:
    """Selection (with descendants), or the whole scene when nothing is selected.

    ``triangulate`` reads every mesh from a temporary triangulated duplicate (the
    Ogre exporter needs triangles; a fan over a concave n-gon would be wrong).
    """
    roots = _selected_transforms() or _all_roots()
    if not roots:
        raise RuntimeError("nothing to export: the scene is empty")
    transforms = cmds.ls(roots, dag=True, long=True, type="transform") or []

    scene = Scene()
    exported: Dict[str, Node] = {}
    shapes: Dict[str, str] = {}
    for transform in transforms:
        meta = _get_attr(transform, "bzMeta")
        if meta and not scene.meta:
            try:
                scene.meta = json.loads(meta)
            except ValueError:
                scene.warnings.append("ignored unreadable bzMeta on %s" % transform)
        kind, shape = _classify(transform)
        if kind is None:
            continue
        name = _get_attr(transform, "bzName") or transform.split("|")[-1].split(":")[-1]
        props = {}
        extra = _get_attr(transform, "bzProps")
        if extra:
            try:
                props.update(json.loads(extra))
            except ValueError:
                scene.warnings.append("ignored unreadable bzProps on %s" % name)
        bz_type = _get_attr(transform, "bzType")
        if bz_type is not None:
            props["type"] = int(bz_type)
        bz_flags = _get_attr(transform, "bzFlags")
        if bz_flags is not None:
            props["flags"] = int(bz_flags)
        exported[transform] = Node(name=name, kind=kind, props=props)
        if shape:
            shapes[transform] = shape

    # Parent = nearest exported ancestor; the local matrix is relative to it, so
    # plain organising groups between parts do not change the result.
    for transform, node in exported.items():
        world = _world_matrix(transform)
        ancestor = None
        path = transform
        while "|" in path.lstrip("|"):
            path = path.rsplit("|", 1)[0]
            if path in exported:
                ancestor = path
                break
        if ancestor is None:
            node.matrix = world
        else:
            node.parent = exported[ancestor].name
            node.matrix = axes.matmul(axes.inverse(_world_matrix(ancestor)), world)
        if transform in shapes:
            if triangulate:
                node.mesh = _extract_triangulated(transform, shapes[transform], scene.warnings, node.name)
            else:
                node.mesh = _extract_mesh(shapes[transform], scene.warnings, node.name)
        scene.nodes.append(node)
    if not scene.nodes:
        raise RuntimeError("nothing to export: no meshes, locators or tagged transforms found")
    return scene


# --- entry points used by the plug-in menu ---------------------------------

def _report(warnings: List[str]) -> None:
    for message in warnings:
        om.MGlobal.displayWarning("Battlezone: " + message)


def _import(scene: Scene, path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    root = build_scene(scene, stem, texture_folder=os.path.dirname(os.path.abspath(path)))
    # The API 2.0 calls above are not undoable, so drop the partial undo queue.
    cmds.flushUndo()
    om.MGlobal.displayInfo("Battlezone: this import is not undoable (undo queue cleared).")
    _report(scene.warnings)
    cmds.select(root, replace=True)
    om.MGlobal.displayInfo("Battlezone: imported %d node(s) from %s" % (len(scene.nodes), path))
    return root


def import_vdf(path: str) -> str:
    return _import(vdf_io.read_vdf(path), path)


def import_sdf(path: str) -> str:
    return _import(sdf_io.read_sdf(path), path)


def import_ogre_mesh(path: str) -> str:
    return _import(ogre_io.read_ogre_mesh(path), path)


def export_vdf(path: str) -> None:
    warnings = vdf_io.write_vdf(collect_scene(), path)
    _report(warnings)
    om.MGlobal.displayInfo("Battlezone: wrote %s (+ .geo files beside it)" % path)


def export_sdf(path: str) -> None:
    warnings = sdf_io.write_sdf(collect_scene(), path)
    _report(warnings)
    om.MGlobal.displayInfo("Battlezone: wrote %s (+ .geo files beside it)" % path)


def export_ogre_mesh(path: str) -> None:
    warnings = ogre_io.write_ogre_mesh(collect_scene(triangulate=True), path)
    _report(warnings)
    om.MGlobal.displayInfo("Battlezone: wrote %s" % path)
