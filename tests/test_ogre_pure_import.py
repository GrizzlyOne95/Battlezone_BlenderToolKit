import tempfile
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

from bz98tools.ogrefast.pure import kenshi_compat as export_compat
from bz98tools.ogrefast.pure.import_compat import (
    KenshiObjectSerializer as ImportSerializer,
    UnsupportedPureImport,
)


class PureOgreImportTests(unittest.TestCase):
    @staticmethod
    def _make_mesh(*, skeleton_name=""):
        serializer = export_compat.KenshiObjectSerializer()
        mesh = serializer.create_mesh("roundtrip.mesh")
        submesh = export_compat.SubMeshData()
        submesh.index = 0
        submesh.submesh_name = "Hull"
        submesh.material = "bz98/hull"

        positions = np.asarray(
            [[1.0, 2.0, 3.0], [-4.0, 5.0, 6.0], [7.0, -8.0, 9.0]],
            dtype=np.float32,
        )
        normals = np.asarray(
            [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]],
            dtype=np.float32,
        )
        uvs = np.asarray(
            [[0.125, 0.25], [0.75, 0.5], [1.0, 0.875]], dtype=np.float32
        )
        indices = np.asarray([0, 1, 2], dtype=np.int32)
        loops = np.asarray([0, 1, 2], dtype=np.int32)

        out_indices = submesh.set_vertex(
            nd_vert_indices=indices,
            nd_loop_indices=loops,
            nd_positions=positions,
            nd_normals=normals,
            nd_tangents=np.empty(3, dtype=np.float32),
            nd_bitangent_signs=np.empty(1, dtype=np.float32),
            nd_bitangents=np.empty(3, dtype=np.float32),
            nd_texcoords=uvs,
            nd_colors=np.empty(4, dtype=np.float32),
            nd_alphas=np.empty(4, dtype=np.float32),
            tangent_dimensions=3,
            optimize=True,
        )
        submesh.set_bone_assignments([], out_indices)
        mesh.set_submeshes([submesh])
        if skeleton_name:
            mesh.set_linked_skeleton_name(skeleton_name)
        return serializer, mesh, positions, normals, uvs

    def test_static_mesh_roundtrips_through_independent_binary_reader(self):
        export_serializer, mesh, positions, normals, uvs = self._make_mesh()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "roundtrip.mesh"
            export_serializer.save_mesh(mesh, path, export_compat.MeshVersion.V_1_10)

            importer = ImportSerializer()
            importer.add_resource_location(temp_dir)
            loaded = importer.load_mesh(path.name)

        self.assertEqual(loaded.get_linked_skeleton_name(), "")
        self.assertEqual(len(loaded.get_submeshes()), 1)
        submesh = loaded.get_submeshes()[0]
        self.assertEqual(submesh.submesh_name, "Hull")
        self.assertEqual(submesh.material, "bz98/hull")
        self.assertEqual(submesh.faces, [0, 1, 2])
        self.assertEqual(submesh.face_count, 1)
        self.assertEqual(submesh.geometry.vertex_count, 3)
        self.assertTrue(submesh.geometry.has_positions)
        self.assertTrue(submesh.geometry.has_normals)
        self.assertTrue(submesh.geometry.has_texture_coord)

        decoded_positions = np.asarray(submesh.get_positions(), dtype=np.float32).reshape(-1, 3)
        decoded_normals = np.asarray(submesh.get_normals(), dtype=np.float32).reshape(-1, 3)
        decoded_uvs = np.asarray(submesh.get_texcoords(), dtype=np.float32).reshape(1, 3, 2)[0]

        np.testing.assert_allclose(decoded_positions, positions, atol=1e-6)
        np.testing.assert_allclose(decoded_normals, normals, atol=1e-6)
        np.testing.assert_allclose(decoded_uvs, uvs, atol=1e-6)

    def test_static_import_rejects_linked_skeleton_without_losing_data(self):
        export_serializer, mesh, *_ = self._make_mesh(skeleton_name="vehicle.skeleton")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rigged.mesh"
            export_serializer.save_mesh(mesh, path, export_compat.MeshVersion.V_1_10)
            importer = ImportSerializer()
            importer.add_resource_location(temp_dir)
            with self.assertRaises(UnsupportedPureImport):
                importer.load_mesh(path.name)


if __name__ == "__main__":
    unittest.main()
