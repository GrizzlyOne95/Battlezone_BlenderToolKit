import tempfile
import unittest
from pathlib import Path

import numpy as np

from bz98tools.ogrefast.pure.pose_compat import (
    KenshiObjectSerializer as PoseWriter,
    MeshVersion,
    SubMeshData,
)
from bz98tools.ogrefast.pure.rigged_import_compat import (
    KenshiObjectSerializer as PoseReader,
)


class PureOgrePoseTests(unittest.TestCase):
    def _submesh(self, index, name, x_offset, delta):
        submesh = SubMeshData()
        submesh.index = index
        submesh.submesh_name = name
        submesh.material = name + "Mat"

        mapping = submesh.set_vertex(
            nd_vert_indices=np.asarray([0, 1, 2], dtype=np.int32),
            nd_loop_indices=np.asarray([0, 1, 2], dtype=np.int32),
            nd_positions=np.asarray(
                [
                    [x_offset + 0.0, 0.0, 0.0],
                    [x_offset + 1.0, 0.0, 0.0],
                    [x_offset + 0.0, 1.0, 0.0],
                ],
                dtype=np.float32,
            ),
            nd_normals=np.asarray([[0.0, 0.0, 1.0]] * 3, dtype=np.float32),
            nd_tangents=np.empty(3, dtype=np.float32),
            nd_bitangent_signs=np.empty(1, dtype=np.float32),
            nd_bitangents=np.empty(3, dtype=np.float32),
            nd_texcoords=np.asarray(
                [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32
            ),
            nd_colors=np.empty(4, dtype=np.float32),
            nd_alphas=np.empty(4, dtype=np.float32),
            tangent_dimensions=3,
            optimize=True,
        )
        submesh.set_bone_assignments([], mapping)
        submesh.append_shapekey(
            "OraclePose", np.asarray(delta, dtype=np.float32), mapping
        )
        return submesh

    def test_two_submesh_shape_keys_roundtrip(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="bz98_pose_test_"))
        path = temp_dir / "pose.mesh"

        writer = PoseWriter()
        mesh = writer.create_mesh(path.name)
        mesh.set_submeshes(
            [
                self._submesh(
                    0,
                    "first",
                    0.0,
                    [[1.0, 2.0, 3.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                ),
                self._submesh(
                    1,
                    "second",
                    10.0,
                    [[0.0, 0.0, 0.0], [4.0, 5.0, 6.0], [0.0, 0.0, 0.0]],
                ),
            ]
        )
        wire = mesh.to_wire()
        self.assertEqual([pose.target for pose in wire.poses], [1, 2])
        self.assertEqual([len(pose.vertices) for pose in wire.poses], [1, 1])
        np.testing.assert_allclose(wire.poses[0].vertices[0].offset, [1.0, 3.0, -2.0])
        np.testing.assert_allclose(wire.poses[1].vertices[0].offset, [4.0, 6.0, -5.0])

        writer.save_mesh(mesh, path, MeshVersion.V_1_10)

        reader = PoseReader()
        reader.add_resource_location(temp_dir)
        loaded = reader.load_mesh(path.name)
        first, second = loaded.get_submeshes()

        first_shapes = first.get_shapekeys()
        second_shapes = second.get_shapekeys()
        self.assertEqual([name for name, _ in first_shapes], ["OraclePose"])
        self.assertEqual([name for name, _ in second_shapes], ["OraclePose"])

        np.testing.assert_allclose(
            first_shapes[0][1],
            [[1.0, 2.0, 3.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            second_shapes[0][1],
            [[10.0, 0.0, 0.0], [15.0, 5.0, 6.0], [10.0, 1.0, 0.0]],
            atol=1e-6,
        )

    def test_zero_delta_pose_is_preserved_as_empty_sparse_pose(self):
        submesh = self._submesh(
            0,
            "zero",
            0.0,
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        )
        writer = PoseWriter()
        mesh = writer.create_mesh("zero.mesh")
        mesh.set_submeshes([submesh])
        wire = mesh.to_wire()
        self.assertEqual(len(wire.poses), 1)
        self.assertEqual(wire.poses[0].name, "OraclePose")
        self.assertEqual(wire.poses[0].vertices, [])


if __name__ == "__main__":
    unittest.main()
