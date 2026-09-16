import unittest

import numpy as np

from bz98tools.ogrefast.pure.kenshi_compat import SubMeshData


class PureKenshiCompatTests(unittest.TestCase):
    def _base_arrays(self):
        return dict(
            nd_vert_indices=np.asarray([0, 1, 2, 0, 2, 3], dtype=np.int32),
            nd_loop_indices=np.arange(6, dtype=np.int32),
            nd_positions=np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [1.0, 1.0, 0.0],
                    [0.0, 1.0, 0.0],
                ],
                dtype=np.float32,
            ),
            nd_normals=np.tile(np.asarray([[0.0, 0.0, 1.0]], dtype=np.float32), (6, 1)),
            nd_tangents=np.empty(3, dtype=np.float32),
            nd_bitangent_signs=np.empty(1, dtype=np.float32),
            nd_bitangents=np.empty(3, dtype=np.float32),
            nd_texcoords=np.asarray(
                [
                    [0.0, 0.0],
                    [1.0, 0.0],
                    [1.0, 1.0],
                    [0.0, 0.0],
                    [1.0, 1.0],
                    [0.0, 1.0],
                ],
                dtype=np.float32,
            ),
            nd_colors=np.empty(4, dtype=np.float32),
            nd_alphas=np.empty(4, dtype=np.float32),
        )

    def test_optimize_merges_identical_loops_for_same_source_vertex(self):
        submesh = SubMeshData()
        mapping = submesh.set_vertex(**self._base_arrays(), optimize=True)

        self.assertEqual(submesh.geometry.vertex_count, 4)
        self.assertEqual(mapping.tolist(), [0, 1, 2, 3])
        self.assertEqual(submesh.faces, [0, 1, 2, 0, 2, 3])
        self.assertEqual(submesh.face_count, 2)
        self.assertFalse(submesh.use_32bit_indexes)
        self.assertEqual(
            [element.semantic for element in submesh._wire_geometry.declaration],
            ["POSITION", "NORMAL", "TEXCOORD"],
        )
        self.assertEqual(submesh._wire_geometry.buffers[0].vertex_size, 32)
        self.assertEqual(len(submesh._wire_geometry.buffers[0].data), 4 * 32)

    def test_uv_seam_splits_same_source_vertex(self):
        arrays = self._base_arrays()
        arrays["nd_texcoords"] = arrays["nd_texcoords"].copy()
        arrays["nd_texcoords"][3] = [0.5, 0.5]

        submesh = SubMeshData()
        mapping = submesh.set_vertex(**arrays, optimize=True)

        self.assertEqual(submesh.geometry.vertex_count, 5)
        self.assertEqual(mapping.tolist(), [0, 1, 2, 0, 3])
        self.assertNotEqual(submesh.faces[0], submesh.faces[3])

    def test_no_optimization_keeps_one_exported_vertex_per_loop(self):
        submesh = SubMeshData()
        mapping = submesh.set_vertex(**self._base_arrays(), optimize=False)

        self.assertEqual(submesh.geometry.vertex_count, 6)
        self.assertEqual(mapping.tolist(), [0, 1, 2, 0, 2, 3])
        self.assertEqual(submesh.faces, list(range(6)))

    def test_triangle_list_rejects_non_triangle_loop_count(self):
        arrays = self._base_arrays()
        arrays["nd_vert_indices"] = arrays["nd_vert_indices"][:-1]
        arrays["nd_loop_indices"] = arrays["nd_loop_indices"][:-1]
        arrays["nd_normals"] = arrays["nd_normals"][:-1]
        arrays["nd_texcoords"] = arrays["nd_texcoords"][:-1]

        with self.assertRaisesRegex(ValueError, "divisible by three"):
            SubMeshData().set_vertex(**arrays, optimize=True)


if __name__ == "__main__":
    unittest.main()
