"""Redux skeletons in Maya: joints, skin weights, animation clips (needs Maya).

Mapping (Ogre space is Maya space, so nothing is flipped):

* a bone becomes a joint under the import group; ``translate`` = bone position, ``jointOrient`` =
  bone orientation, ``rotate`` = identity, ``scale`` = bone scale. That is the bind pose, also kept
  in the ``bzBind`` attribute (JSON) next to ``bzBoneName`` and ``bzBoneId`` (Ogre's id);
* Ogre keyframes are offsets from the bind pose (``position = bind + t``, ``orientation = bind * q``),
  which is exactly Maya's joint model, so a key is ``translate = bind + t``, ``rotate = euler(q)``,
  ``scale = bind * s`` with no conversion;
* **clips**: Maya has no actions, so every clip is a time range on the one timeline. The skeleton's
  first root joint carries ``bzClips`` (JSON: name, length, start in seconds); keys sit at
  ``start + key time`` seconds. Names, lengths and key times round-trip exactly, and clips that
  have no tracks survive because they are rows of the table. Rotation keys are stored as Euler
  angles with linear tangents, unrolled so consecutive keys stay on the same branch;
* skin weights go to a ``skinCluster`` per mesh (bind pose = current pose, so skin before keying).

Export reads the same things back: bind pose from ``bzBind`` (a joint without it uses its current
pose), clips from ``bzClips`` (or one ``Take_001`` over the keyed range when there is no table).
"""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from .core.model import Bone, Clip, Skeleton, Track

BONE_ID_ATTR = "bzBoneId"
BONE_NAME_ATTR = "bzBoneName"
BIND_ATTR = "bzBind"
CLIPS_ATTR = "bzClips"
CLIP_GAP = 1.0  # seconds between clips on the timeline
CLIP_MARGIN = 0.25  # keys within this of a clip range belong to it (Ogre key times can overshoot the length)


def _plug(node: str, attr: str) -> "om.MPlug":
    selection = om.MSelectionList()
    selection.add(node)
    return om.MFnDependencyNode(selection.getDependNode(0)).findPlug(attr, False)


def _dag(node: str) -> "om.MDagPath":
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _string_attr(node: str, name: str, value: str) -> None:
    if not cmds.attributeQuery(name, node=node, exists=True):
        cmds.addAttr(node, longName=name, dataType="string")
    cmds.setAttr("%s.%s" % (node, name), value, type="string")


def _valid(name: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name or "")
    return cleaned if cleaned and not cleaned[0].isdigit() else "_" + cleaned


# --- build ---------------------------------------------------------------------

def build_skeleton(skeleton: Skeleton, parent: str) -> Dict[str, str]:
    """Create the joints under ``parent``; returns {bone name: joint long name}."""
    joints: Dict[str, str] = {}
    order = sorted(skeleton.bones, key=lambda b: (b.parent is not None, b.id))
    pending = list(order)
    while pending:  # parents first
        progress = False
        for bone in list(pending):
            if bone.parent is None or bone.parent in joints:
                holder = joints[bone.parent] if bone.parent else parent
                joint = cmds.createNode("joint", name=_valid(bone.name), parent=holder)
                joint = cmds.ls(joint, long=True)[0]
                joints[bone.name] = joint
                pending.remove(bone)
                progress = True
        if not progress:
            raise ValueError("bone hierarchy has a cycle or a missing parent")
    for bone in skeleton.bones:
        joint = joints[bone.name]
        fn = oma.MFnIkJoint(_dag(joint))
        fn.setTranslation(om.MVector(*bone.position), om.MSpace.kTransform)
        w, x, y, z = bone.orientation
        fn.setOrientation(om.MQuaternion(x, y, z, w))
        fn.setScale(list(bone.scale))
        _string_attr(joint, BONE_NAME_ATTR, bone.name)
        if not cmds.attributeQuery(BONE_ID_ATTR, node=joint, exists=True):
            cmds.addAttr(joint, longName=BONE_ID_ATTR, attributeType="long")
        cmds.setAttr("%s.%s" % (joint, BONE_ID_ATTR), int(bone.id))
        _string_attr(joint, BIND_ATTR, json.dumps(
            {"position": list(bone.position), "orientation": list(bone.orientation), "scale": list(bone.scale)}))
    return joints


def skin(mesh_transform: str, weights: List[List[Tuple[int, float]]], joints_by_id: Dict[int, str]) -> Optional[str]:
    """Bind ``mesh_transform`` to the joints and load the weights. Returns the skinCluster."""
    ids = sorted(joints_by_id)
    names = [joints_by_id[i] for i in ids]
    cluster = cmds.skinCluster(names, mesh_transform, toSelectedBones=True, bindMethod=0, skinMethod=0,
                               normalizeWeights=0, obeyMaxInfluences=False, maximumInfluences=3,
                               name=_valid(mesh_transform.split("|")[-1]) + "_skin")[0]
    shape = cmds.listRelatives(mesh_transform, shapes=True, fullPath=True, noIntermediate=True)[0]
    selection = om.MSelectionList()
    selection.add(cluster)
    fn = oma.MFnSkinCluster(selection.getDependNode(0))
    count = len(weights)
    column = {bone_id: i for i, bone_id in enumerate(ids)}
    values = om.MDoubleArray(count * len(ids), 0.0)
    for vertex, influences in enumerate(weights):
        for bone_id, weight in influences:
            if bone_id in column:
                values[vertex * len(ids) + column[bone_id]] += weight
    components = om.MFnSingleIndexedComponent()
    component = components.create(om.MFn.kMeshVertComponent)
    components.addElements(list(range(count)))
    influence_indices = om.MIntArray([fn.indexForInfluenceObject(_dag(j)) for j in names])
    fn.setWeights(_dag(shape), component, influence_indices, values, False)
    cmds.setAttr(cluster + ".normalizeWeights", 1)  # interactive normalisation for editing afterwards
    return cluster


_MIDDLE = {0: 1, 1: 2, 2: 0, 3: 2, 4: 0, 5: 1}  # rotate order -> index (x, y, z) of the middle axis


def unrolled_eulers(quaternions, order: int):
    """Euler angles (x, y, z radians, rotate order ``order``) for ``om.MQuaternion`` s, unrolled.

    Each result is the same rotation as its quaternion, picked among the equivalent angle
    triples (full turns, and the flip ``(a + pi, pi - b, c + pi)`` of the middle axis) as the
    one nearest the previous key. (``MEulerRotation.closestSolution`` is not reliable close to
    gimbal lock.)
    """
    tau = 2.0 * math.pi
    middle = _MIDDLE.get(order, 1)
    out: List[Tuple[float, float, float]] = []
    previous = None
    for quaternion in quaternions:
        target = quaternion.normal()
        euler = target.asEulerRotation()
        euler.reorderIt(order)
        base = (euler.x, euler.y, euler.z)
        if previous is None:
            out.append(base)
            previous = base
            continue
        flipped = tuple((math.pi - v) if i == middle else v + math.pi for i, v in enumerate(base))
        best, best_distance = base, None
        for candidate in (base, flipped):
            shifted = tuple(v + tau * round((p - v) / tau) for v, p in zip(candidate, previous))
            rebuilt = om.MEulerRotation(shifted[0], shifted[1], shifted[2], order).asQuaternion()
            dot = rebuilt.x * target.x + rebuilt.y * target.y + rebuilt.z * target.z + rebuilt.w * target.w
            if abs(dot) < 1.0 - 1e-6:
                continue
            distance = sum((v - p) ** 2 for v, p in zip(shifted, previous))
            if best_distance is None or distance < best_distance:
                best, best_distance = shifted, distance
        out.append(best)
        previous = best
    return out


def _euler_keys(rotations, order: int):
    """Unrolled Euler (x, y, z radians) for a sequence of Ogre (w, x, y, z) quaternions."""
    return unrolled_eulers([om.MQuaternion(x, y, z, w) for w, x, y, z in rotations], order)


def build_clips(skeleton: Skeleton, joints: Dict[str, str]) -> None:
    """Key every clip onto the joints and write the clip table on the first root joint."""
    table = []
    start = 0.0
    channels: Dict[Tuple[str, str], List[Tuple[float, float]]] = {}
    bones = {b.name: b for b in skeleton.bones}
    for clip in skeleton.clips:
        table.append({"name": clip.name, "length": clip.length, "start": start})
        for track in clip.tracks:
            if track.bone not in joints or not track.times:
                continue
            bone = bones[track.bone]
            joint = joints[track.bone]
            order = cmds.getAttr(joint + ".rotateOrder")
            eulers = _euler_keys(track.rotations, order)
            for i, t in enumerate(track.times):
                at = start + t
                tx, ty, tz = track.translations[i]
                sx, sy, sz = track.scales[i] if i < len(track.scales) else (1.0, 1.0, 1.0)
                values = {
                    "translateX": bone.position[0] + tx, "translateY": bone.position[1] + ty,
                    "translateZ": bone.position[2] + tz,
                    "rotateX": eulers[i][0], "rotateY": eulers[i][1], "rotateZ": eulers[i][2],
                    "scaleX": bone.scale[0] * sx, "scaleY": bone.scale[1] * sy, "scaleZ": bone.scale[2] * sz,
                }
                for attr, value in values.items():
                    channels.setdefault((joint, attr), []).append((at, value))
        start += clip.length + CLIP_GAP
    for (joint, attr), keys in channels.items():
        plug = _plug(joint, attr)
        times = om.MTimeArray([om.MTime(t, om.MTime.kSeconds) for t, _v in keys])
        values = om.MDoubleArray([v for _t, v in keys])
        curve = oma.MFnAnimCurve()
        curve.create(plug)
        curve.addKeys(times, values, oma.MFnAnimCurve.kTangentLinear, oma.MFnAnimCurve.kTangentLinear)
    roots = [joints[b.name] for b in skeleton.bones if b.parent is None]
    if roots and table:
        _string_attr(roots[0], CLIPS_ATTR, json.dumps(table))
    if table:
        end = table[-1]["start"] + table[-1]["length"]
        cmds.playbackOptions(minTime=0, maxTime=max(1, round(end * _fps())))


def _fps() -> float:
    return om.MTime(1.0, om.MTime.kSeconds).asUnits(om.MTime.uiUnit())


# --- collect ---------------------------------------------------------------------

def is_bone(node: str) -> bool:
    """Every joint is a bone; those from an import carry their Ogre id, others get one on export."""
    return cmds.nodeType(node) == "joint"


def joint_ids() -> Dict[str, int]:
    """Bone id of every joint (long name): the imported ``bzBoneId``, else the next free number."""
    joints = cmds.ls(type="joint", long=True) or []
    ids: Dict[str, int] = {}
    for joint in joints:
        if cmds.attributeQuery(BONE_ID_ATTR, node=joint, exists=True):
            ids[joint] = int(cmds.getAttr(joint + "." + BONE_ID_ATTR))
    taken = set(ids.values())
    free = iter(i for i in range(len(joints) + len(taken) + 1) if i not in taken)
    for joint in sorted(joints):
        if joint not in ids:
            ids[joint] = next(free)
    return ids


def _curve(joint: str, attr: str):
    plug = _plug(joint, attr)
    found = oma.MAnimUtil.findAnimation(plug)
    return oma.MFnAnimCurve(found[0]) if len(found) else None


def _static(joint: str, attr: str) -> float:
    plug = _plug(joint, attr)
    if attr.startswith("rotate"):
        return plug.asMAngle().asRadians()
    if attr.startswith("translate"):
        return plug.asMDistance().asCentimeters()
    return plug.asDouble()


def collect_skeleton(joints: List[str]) -> Optional[Skeleton]:
    """Neutral skeleton (bones + clips) from the tagged joints in ``joints`` (long names)."""
    bones_joints = [j for j in joints if is_bone(j)]
    if not bones_joints:
        return None
    by_path = {j: j for j in bones_joints}
    all_ids = joint_ids()
    skeleton = Skeleton(name="")
    bind: Dict[str, dict] = {}
    for joint in bones_joints:
        name = (cmds.getAttr(joint + "." + BONE_NAME_ATTR) if cmds.attributeQuery(BONE_NAME_ATTR, node=joint, exists=True)
                else "") or joint.split("|")[-1].split(":")[-1]
        data = None
        if cmds.attributeQuery(BIND_ATTR, node=joint, exists=True):
            try:
                data = json.loads(cmds.getAttr(joint + "." + BIND_ATTR))
            except ValueError:
                data = None
        if data is None:  # untagged bind: the current pose
            local = om.MTransformationMatrix(om.MMatrix(cmds.xform(joint, query=True, objectSpace=True, matrix=True)))
            t = local.translation(om.MSpace.kTransform)
            q = local.rotation(asQuaternion=True)
            data = {"position": [t.x, t.y, t.z], "orientation": [q.w, q.x, q.y, q.z],
                    "scale": list(local.scale(om.MSpace.kTransform))}
        parent = None
        path = joint
        while "|" in path.lstrip("|"):
            path = path.rsplit("|", 1)[0]
            if path in by_path:
                parent = path
                break
        bind[joint] = data
        skeleton.bones.append(Bone(
            name=name, id=all_ids[joint], parent=parent,
            position=tuple(data["position"]), orientation=tuple(data["orientation"]), scale=tuple(data["scale"]),
        ))
    names = {j: b.name for j, b in zip(bones_joints, skeleton.bones)}
    for bone, joint in zip(skeleton.bones, bones_joints):
        bone.parent = names[bone.parent] if bone.parent else None
    skeleton.clips = _collect_clips(bones_joints, skeleton, names, bind)
    return skeleton


def _clip_table(roots: List[str]) -> List[dict]:
    for root in roots:
        if cmds.attributeQuery(CLIPS_ATTR, node=root, exists=True):
            try:
                return json.loads(cmds.getAttr(root + "." + CLIPS_ATTR))
            except ValueError:
                return []
    return []


def _collect_clips(joints: List[str], skeleton: Skeleton, names: Dict[str, str], bind: Dict[str, dict]) -> List[Clip]:
    table = _clip_table(joints)
    curves = {j: {a: _curve(j, a) for a in _ATTRS} for j in joints}
    keyed = [t for j in joints for a in _ATTRS if curves[j][a] is not None
             for t in _key_times(curves[j][a])]
    if not table:
        if not keyed:
            return []
        table = [{"name": "Take_001", "length": max(keyed) - min(keyed), "start": min(keyed)}]
    clips = []
    for entry in table:
        start, length = float(entry["start"]), float(entry["length"])
        end = start + length
        clip = Clip(name=entry["name"], length=length)
        for joint in joints:
            channel = curves[joint]
            times = sorted({round(t, 9) for a in _ATTRS if channel[a] is not None
                            for t in _key_times(channel[a]) if start - 1e-6 <= t <= end + CLIP_MARGIN})
            if not times:
                continue
            order = cmds.getAttr(joint + ".rotateOrder")
            data = bind[joint]
            track = Track(bone=names[joint])
            for t in times:
                values = {}
                for attr in _ATTRS:
                    curve = channel[attr]
                    values[attr] = curve.evaluate(om.MTime(t, om.MTime.kSeconds)) if curve else _static(joint, attr)
                q = om.MEulerRotation(values["rotateX"], values["rotateY"], values["rotateZ"], order).asQuaternion()
                track.times.append(max(0.0, t - start))
                track.translations.append((
                    values["translateX"] - data["position"][0],
                    values["translateY"] - data["position"][1],
                    values["translateZ"] - data["position"][2],
                ))
                track.rotations.append((q.w, q.x, q.y, q.z))
                track.scales.append((
                    values["scaleX"] / data["scale"][0] if data["scale"][0] else 1.0,
                    values["scaleY"] / data["scale"][1] if data["scale"][1] else 1.0,
                    values["scaleZ"] / data["scale"][2] if data["scale"][2] else 1.0,
                ))
            clip.tracks.append(track)
        clips.append(clip)
    return clips


_ATTRS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ")


def _key_times(curve) -> List[float]:
    return [curve.input(i).asUnits(om.MTime.kSeconds) for i in range(curve.numKeys)]


def find_skin_cluster(shape: str) -> Optional[str]:
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    clusters = cmds.ls(history, type="skinCluster")
    return clusters[0] if clusters else None


def original_shape(cluster: str) -> Optional[str]:
    """The bind-pose (intermediate) shape feeding the skinCluster."""
    connections = cmds.listConnections(cluster + ".originalGeometry[0]", source=True, destination=False, shapes=True)
    return cmds.ls(connections[0], long=True)[0] if connections else None


def collect_weights(cluster: str, shape: str) -> List[List[Tuple[int, float]]]:
    """Per-vertex [(bone id, weight)] from the skinCluster (nonzero entries only)."""
    selection = om.MSelectionList()
    selection.add(cluster)
    fn = oma.MFnSkinCluster(selection.getDependNode(0))
    influences = fn.influenceObjects()
    all_ids = joint_ids()
    ids = [all_ids.get(path.fullPathName(), -1) for path in influences]
    dag = _dag(shape)
    count = om.MFnMesh(dag).numVertices
    components = om.MFnSingleIndexedComponent()
    component = components.create(om.MFn.kMeshVertComponent)
    components.addElements(list(range(count)))
    values, width = fn.getWeights(dag, component)
    out: List[List[Tuple[int, float]]] = []
    for vertex in range(count):
        row = []
        for column in range(width):
            weight = values[vertex * width + column]
            if weight > 0.0 and ids[column] >= 0:
                row.append((ids[column], float(weight)))
        out.append(row)
    return out
