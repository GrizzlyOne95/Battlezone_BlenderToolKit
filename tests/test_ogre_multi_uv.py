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

        # The historical/native accessor is Blender-loop-domain, not raw
        # vertex-buffer-domain: 6 indexed triangle corners x 2 floats/set.
        self.assertEqual(uv_sets.shape, (2, 12))
        uv_loops = uv_sets.reshape(2, 6, 2)

        # Import flips V back to Blender coordinates.  The first corner of the
        # second triangle is the UV1 seam and must survive vertex splitting and
        # index-buffer expansion as authored.
        np.testing.assert_allclose(uv_loops[1][3], [0.5, 0.25])
        np.testing.assert_allclose(uv_loops[0][3], [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
