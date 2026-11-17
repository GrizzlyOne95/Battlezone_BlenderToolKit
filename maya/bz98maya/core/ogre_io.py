"""Redux Ogre binary .mesh <-> neutral scene (static geometry only). Maya-free.

Reading goes through the toolkit's pure importer
(``ogrefast.pure.import_compat.KenshiObjectSerializer.load_mesh``, which wraps
``bzrmodelporter.ogremesh_serializer.MeshSerializer``) and decodes vertex
buffers with its element decoder; the sibling ``.material`` supplies diffuse colour
and texture files. Writing is Blender's export pipeline, see ``redux.py``.

Ogre space equals the neutral / Maya space (see ``axes.py``), so positions and
normals are copied as is and only V is flipped.

Not round-tripped on read: skeleton links, bone assignments, poses, mesh animations,
LOD levels, edge lists, vertex colours, tangents / binormals (a mesh that
carries skeleton data is rejected).
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

def read_ogre_mesh(path: str, import_materials: bool = True, import_skeleton: bool = True) -> Scene:
    """Static or skinned mesh. A linked ``.skeleton`` beside the mesh becomes ``scene.skeleton``."""
    import_compat, _, _ = _modules()
    from bz98tools.ogrefast.pure import rigged_import_compat
    from bz98tools.ogrefast.pure.pose_import_compat import PoseDetectingMeshSerializer

    folder, filename = os.path.split(os.path.abspath(path))
    with open(os.path.join(folder, filename), "rb") as stream:
        source = PoseDetectingMeshSerializer(stream).read()
    # Raises UnsupportedPureImport for mesh animations and pose normal offsets (kept out, as in Blender).
    mesh_data = rigged_import_compat._convert_mesh(source, filename)

    scene = Scene()
    if getattr(source, "_pure_import_poses", None):
        scene.warnings.append("pose / shape-key data in the mesh is not imported")
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
        if submesh.boneassignments:
            mesh.weights = [[] for _ in mesh.positions]
            for item in submesh.boneassignments:
                if 0 <= item.vertex_index < len(mesh.weights):
                    mesh.weights[item.vertex_index].append((int(item.bone_index), float(item.weight)))
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
    skeleton_name = mesh_data.get_linked_skeleton_name()
    if skeleton_name and import_skeleton:
        from . import skeleton_io

        skeleton_path = os.path.join(folder, skeleton_name)
        if not os.path.isfile(skeleton_path):  # case-insensitive, as the game's resource lookup
            skeleton_path = next(
                (os.path.join(folder, f) for f in os.listdir(folder) if f.lower() == skeleton_name.lower()), ""
            )
        if skeleton_path and os.path.isfile(skeleton_path):
            scene.skeleton = skeleton_io.read_skeleton(skeleton_path)
        else:
            scene.warnings.append(f"linked skeleton {skeleton_name} was not found beside the mesh")
    if import_materials:
        _attach_materials(scene, os.path.join(folder, os.path.splitext(filename)[0] + ".material"), folder)
    return scene


def _attach_materials(scene: Scene, material_path: str, folder: str) -> None:
    """The sibling .material: diffuse colour and the diffuse texture file found beside the mesh."""
    from . import material

    parsed = material.parse_material_file(material_path) if os.path.isfile(material_path) else {}
    for node in scene.nodes:
        for name in {m for m in node.mesh.face_materials if m} if node.mesh else ():
            info = parsed.get(name)
            if info is None:
                continue
            entry = scene.materials.setdefault(name, {})
            if info.get("diffuse"):
                entry["diffuse"] = list(info["diffuse"])
            texture = info["textures"].get("DiffuseMap")
            if texture:
                entry["texture"] = texture
                found = material.find_texture_file(texture, folder)
                if found:
                    entry["texture_path"] = found


# --- write ---------------------------------------------------------------

def write_ogre_mesh(scene: Scene, path: str, options=None) -> List[str]:
    """Write the BZR .mesh (and .material) with Blender's export pipeline; see ``redux.py``."""
    from . import redux

    return redux.write_bzr_mesh(scene, path, options)
