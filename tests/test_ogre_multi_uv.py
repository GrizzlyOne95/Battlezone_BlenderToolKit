import tempfile
import unittest
from pathlib import Path

import numpy as np

from bz98tools.ogrefast.pure.pose_compat import (
    KenshiObjectSerializer as Writer,
    MeshVersion,
    SubMeshData,
)
from bz98tools.ogrefast.pure.rigged_import_compat import (
    KenshiObjectSerializer as Reader,
)


class PureOgreMultiUvTests(unittest.TestCase):
    def test_two_uv_sets_roundtrip_and_participate_in_vertex_splits(self):
        submesh = SubMeshData()
        submesh.index = 0
        submesh.submesh_name = "multi_uv"
        submesh.material = "multi_uv_mat"

        # Two identical triangles share source vertices and UV0.  The first
        # corner differs only in UV1, so optimization must retain a split there.
        source_indices = np.asarray([0, 1, 2, 0, 1, 2], dtype=np.int32)
        loop_indices = np.arange(6, dtype=np.int32)
        positions = np.asarray(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            dtype=np.float32,
        )
        normals = np.asarray([[0.0, 0.0, 1.0]] * 6, dtype=np.float32)
        uv0 = np.asarray(
            [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]] * 2,
            dtype=np.float32,
        )
        uv1 = uv0.copy()
        uv1[3] = [0.5, 0.25]

        mapping = submesh.set_vertex(
            nd_vert_indices=source_indices,
            nd_loop_indices=loop_indices,
            nd_positions=positions,
            nd_normals=normals,
            nd_tangents=np.empty(3, dtype=np.float32),
            nd_bitangent_signs=np.empty(1, dtype=np.float32),
            nd_bitangents=np.empty(3, dtype=np.float32),
            nd_texcoords=np.stack([uv0, uv1], axis=0),
            nd_colors=np.empty(4, dtype=np.float32),
            nd_alphas=np.empty(4, dtype=np.float32),
            tangent_dimensions=3,
            optimize=True,
        )
        submesh.set_bone_assignments([], mapping)

        self.assertEqual(submesh.geometry.vertex_count, 4)
        texcoord_elements = [
            element
            for element in submesh._wire_geometry.declaration
            if element.semantic == "TEXCOORD"
        ]
        self.assertEqual([element.index for element in texcoord_elements], [0, 1])

        temp_dir = Path(tempfile.mkdtemp(prefix="bz98_multi_uv_"))
        path = temp_dir / "multi_uv.mesh"
        writer = Writer()
        mesh = writer.create_mesh(path.name)
        mesh.set_submeshes([submesh])
        writer.save_mesh(mesh, path, MeshVersion.V_1_10)

        reader = Reader()
        reader.add_resource_location(temp_dir)
        loaded = reader.load_mesh(path.name)
        loaded_submesh = loaded.get_submeshes()[0]
        self.assertEqual(loaded_submesh.geometry.texcoords_size, 2)
        uv_sets = loaded_submesh.get_texcoords()
        self.assertEqual(uv_sets.shape, (2, 8))

        # Import flips V back to Blender coordinates.  The split vertex appears
        # as the fourth exported vertex and must retain UV1=(0.5, 0.25).
        np.testing.assert_allclose(uv_sets[1].reshape(-1, 2)[3], [0.5, 0.25])


if __name__ == "__main__":
    unittest.main()
