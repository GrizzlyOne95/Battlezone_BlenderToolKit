"""Axis, handedness, UV and matrix conventions (pure functions, no Maya).

Mapping chosen: the neutral model, and therefore Maya, uses the same space as
Ogre / Battlezone 98 Redux meshes: +Y up, right handed, counter-clockwise
front faces, outward normals, V pointing up (origin bottom-left).

Derived from the toolkit, not invented:

* Redux .mesh -> Maya: identity for positions, normals and winding. The toolkit
  maps Ogre to Blender with (x, y, z)_ogre -> (x, -z, y)_blender
  (bz98tools/ogrefast/pure/import_compat.py `_ogre_to_blender_xyz`, and the
  inverse `_blender_to_ogre_xyz` in kenshi_compat.py), which is exactly the
  standard Blender Z-up -> Y-up change, i.e. Maya's convention too. UV V is
  flipped on read/write (import_compat.py `get_texcoords`: v -> 1 - v;
  kenshi_compat.py `set_vertex`: v -> 1 - v).
* Legacy GEO/VDF -> Maya: the toolkit maps BZ (x, y, z) to Blender
  (x, z, y) (bz98tools/import_geo.py `geoload`: bm.verts.new((x, z, y))
  and export_geo.py `_geo_coord`). Composing that with the Blender -> Maya
  step above gives BZ (x, y, z) -> Maya (x, y, -z): Z is mirrored. BZ's nose
  (+Z) therefore points to -Z in Maya, matching what the toolkit shows in
  Blender (+Y nose).
  - Winding: face vertex order is kept unchanged (import_geo.py builds the
    Blender face from the file order). Checked on stock GEOs: the right-handed
    cross product of the file winding points along the stored vertex normal,
    and the stored normals point inward (export_geo.py writes
    -(swapped Blender normal)). Under the Z mirror that turns into CCW /
    outward, so no reversal is needed.
  - Normals: BZ file normal n -> Maya (-nx, -ny, nz) (mirror Z, negate).
  - UV: V -> 1 - V (import_geo.py `uv = (u, 1 - v)`).
  - Matrices: the 12 floats of a VDF record are three basis rows (images of
    the local x, y, z axes in the parent) followed by the translation
    (verified against export_vdf.py, which writes `Euler(rx, rz, ry; YZX)
    .to_matrix()` rows, equal to the transpose of the axis-swapped Blender
    rotation). Column-vector matrix Q = rows^T, translation t. In Maya:
    Q' = C Q C and t' = C t with C = diag(1, 1, -1).

1 BZ unit becomes 1 Maya internal unit (centimetres); no scaling is applied.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

Vec3 = Tuple[float, float, float]
Matrix4 = List[List[float]]

_C = np.diag([1.0, 1.0, -1.0])


# --- legacy BZ (GEO / VDF) -------------------------------------------------

def bz_point_to_maya(p: Sequence[float]) -> Vec3:
    return (float(p[0]), float(p[1]), -float(p[2]))


def maya_point_to_bz(p: Sequence[float]) -> Vec3:
    return (float(p[0]), float(p[1]), -float(p[2]))


def bz_normal_to_maya(n: Sequence[float]) -> Vec3:
    return (-float(n[0]), -float(n[1]), float(n[2]))


def maya_normal_to_bz(n: Sequence[float]) -> Vec3:
    return (-float(n[0]), -float(n[1]), float(n[2]))


def bz_uv_to_maya(uv: Sequence[float]) -> Tuple[float, float]:
    return (float(uv[0]), 1.0 - float(uv[1]))


def maya_uv_to_bz(uv: Sequence[float]) -> Tuple[float, float]:
    return (float(uv[0]), 1.0 - float(uv[1]))


def bz_matrix12_to_maya(m12: Sequence[float]) -> Matrix4:
    """VDF record matrix (3 basis rows + translation) -> Maya 4x4, column-vector."""
    rows = np.array(m12[0:9], dtype=float).reshape(3, 3)
    q = _C @ rows.T @ _C
    t = _C @ np.array(m12[9:12], dtype=float)
    out = np.identity(4)
    out[:3, :3] = q
    out[:3, 3] = t
    return out.tolist()


def maya_matrix_to_bz12(matrix: Sequence[Sequence[float]]) -> List[float]:
    a = np.array(matrix, dtype=float)
    q = _C @ a[:3, :3] @ _C
    t = _C @ a[:3, 3]
    return [float(v) for v in q.T.reshape(9)] + [float(v) for v in t]


# --- Redux Ogre .mesh ------------------------------------------------------

def ogre_point_to_maya(p: Sequence[float]) -> Vec3:
    return (float(p[0]), float(p[1]), float(p[2]))


def maya_point_to_ogre(p: Sequence[float]) -> Vec3:
    return (float(p[0]), float(p[1]), float(p[2]))


def ogre_uv_to_maya(uv: Sequence[float]) -> Tuple[float, float]:
    return (float(uv[0]), 1.0 - float(uv[1]))


def maya_uv_to_ogre(uv: Sequence[float]) -> Tuple[float, float]:
    return (float(uv[0]), 1.0 - float(uv[1]))


# --- generic matrix helpers (column-vector 4x4) ----------------------------

def identity4() -> Matrix4:
    return np.identity(4).tolist()


def matmul(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Matrix4:
    return (np.array(a, dtype=float) @ np.array(b, dtype=float)).tolist()


def inverse(a: Sequence[Sequence[float]]) -> Matrix4:
    return np.linalg.inv(np.array(a, dtype=float)).tolist()


def row_vector_flat_to_matrix(flat: Sequence[float]) -> Matrix4:
    """Maya's 16 floats (row-vector convention, translation in row 3) -> column-vector 4x4."""
    return np.array(flat, dtype=float).reshape(4, 4).T.tolist()


def matrix_to_row_vector_flat(matrix: Sequence[Sequence[float]]) -> List[float]:
    return [float(v) for v in np.array(matrix, dtype=float).T.reshape(16)]


def transform_point(matrix: Sequence[Sequence[float]], p: Sequence[float]) -> Vec3:
    a = np.array(matrix, dtype=float)
    v = a[:3, :3] @ np.array(p[:3], dtype=float) + a[:3, 3]
    return (float(v[0]), float(v[1]), float(v[2]))


def transform_normal(matrix: Sequence[Sequence[float]], n: Sequence[float]) -> Vec3:
    """Inverse-transpose of the 3x3 part, renormalised."""
    a = np.array(matrix, dtype=float)[:3, :3]
    try:
        m = np.linalg.inv(a).T
    except np.linalg.LinAlgError:
        m = a
    v = m @ np.array(n[:3], dtype=float)
    length = float(np.linalg.norm(v))
    if length > 0.0:
        v = v / length
    return (float(v[0]), float(v[1]), float(v[2]))


def determinant3(matrix: Sequence[Sequence[float]]) -> float:
    return float(np.linalg.det(np.array(matrix, dtype=float)[:3, :3]))


# --- polygon helpers --------------------------------------------------------

def newell_normal(points: Sequence[Sequence[float]]) -> Vec3:
    """Unit normal of a polygon by Newell's method (right-handed along the winding).

    Robust to collinear leading vertices; (0, 1, 0) for a degenerate polygon.
    """
    pts = np.array(points, dtype=float).reshape(-1, 3)
    nxt = np.roll(pts, -1, axis=0)
    n = np.array([
        np.sum((pts[:, 1] - nxt[:, 1]) * (pts[:, 2] + nxt[:, 2])),
        np.sum((pts[:, 2] - nxt[:, 2]) * (pts[:, 0] + nxt[:, 0])),
        np.sum((pts[:, 0] - nxt[:, 0]) * (pts[:, 1] + nxt[:, 1])),
    ])
    length = float(np.linalg.norm(n))
    if length <= 1e-12:
        return (0.0, 1.0, 0.0)
    n = n / length
    return (float(n[0]), float(n[1]), float(n[2]))


def polygon_issues(points: Sequence[Sequence[float]], tolerance: float = 1e-3):
    """(non_planar, concave) for a polygon with 4 or more vertices."""
    pts = np.array(points, dtype=float).reshape(-1, 3)
    if len(pts) < 4:
        return False, False
    n = np.array(newell_normal(pts))
    extent = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) or 1.0
    non_planar = float(np.max(np.abs((pts - pts[0]) @ n))) > tolerance * extent
    prev_edge = pts - np.roll(pts, 1, axis=0)
    next_edge = np.roll(pts, -1, axis=0) - pts
    turn = np.cross(prev_edge, next_edge) @ n
    concave = bool(np.any(turn < -1e-9 * extent * extent))
    return non_planar, concave
