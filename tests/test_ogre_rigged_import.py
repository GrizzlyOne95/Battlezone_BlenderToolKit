import tempfile
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401

from bz98tools.ogrefast.pure.kenshi_compat import (
    BoneAssignmentData,
    BoneData,
    MeshVersion,
    OgreQuaternion,
    SkeletonData,
    SkeletonVersion,
    SubMeshData,
    Vector3,
)
from bz98tools.ogrefast.pure.rigged_import_compat import (
    KenshiObjectSerializer as RiggedImportSerializer,
)
from bz98tools.ogrefast.pure.skeleton_compat import (
    KenshiObjectSerializer as RiggedExportSerializer,
)


class PureOgreRiggedImportTests(unittest.TestCase):
    def test_mesh_weights_and_linked_skeleton_roundtrip(self):
        writer = RiggedExportSerializer()
        mesh = writer.create_mesh("vehicle.mesh")
        mesh.set_linked_skeleton_name("vehicle.skeleton")

        submesh = SubMeshData()
        submesh.index = 0
        submesh.submesh_name = "Hull"
        submesh.material = "bz98/hull"

        positions = np.asarray(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            dtype=np.float32,
        )
        normals = np.asarray([[0.0, 0.0, 1.0]] * 3, dtype=np.float32)
        indices = np.asarray([0, 1, 2], dtype=np.int32)
        out_indices = submesh.set_vertex(
            nd_vert_indices=indices,
            nd_loop_indices=indices,
            nd_positions=positions,
            nd_normals=normals,
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
        submesh.set_bone_assignments(
            [
                BoneAssignmentData(0, 0, 1.0),
                BoneAssignmentData(1, 0, 0.25),
                BoneAssignmentData(1, 1, 0.75),
                BoneAssignmentData(2, 1, 1.0),
            ],
            out_indices,
        )
        mesh.set_submeshes([submesh])

        skeleton = SkeletonData("vehicle.skeleton", "General")
        skeleton.set_bones(
            [
                BoneData(
                    0,
                    "root",
                    Vector3(),
                    OgreQuaternion(),
                    Vector3(1.0, 1.0, 1.0),
                    "",
                    ["turret"],
                ),
                BoneData(
                    1,
                    "turret",
                    Vector3(0.0, 1.0, 0.0),
                    OgreQuaternion(),
                    Vector3(1.0, 1.0, 1.0),
                    "root",
                    [],
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            mesh_path = Path(temp_dir) / "vehicle.mesh"
            skeleton_path = Path(temp_dir) / "vehicle.skeleton"
            writer.save_mesh(mesh, mesh_path, MeshVersion.V_1_10)
            writer.save_skeleton(skeleton, skeleton_path, SkeletonVersion.V_1_8)

            reader = RiggedImportSerializer()
            reader.add_resource_location(temp_dir)
            loaded = reader.load_mesh(mesh_path.name)

        self.assertEqual(loaded.get_linked_skeleton_name(), "vehicle.skeleton")
        loaded_skeleton = loaded.get_linked_skeleton()
        self.assertIsNotNone(loaded_skeleton)
        self.assertEqual(
            [(bone.id, bone.name, bone.parent_name) for bone in loaded_skeleton.get_bones()],
            [(0, "root", ""), (1, "turret", "root")],
        )

        loaded_submesh = loaded.get_submeshes()[0]
        loaded_submesh.set_bone_mapping({0: "root", 1: "turret"})
        groups = {group.name: group.group for group in loaded_submesh.get_vertex_groups()}
        self.assertEqual(set(groups), {"root", "turret"})
        self.assertEqual(groups["root"], [([0], 1.0), ([1], 0.25)])
        self.assertEqual(groups["turret"], [([1], 0.75), ([2], 1.0)])

        decoded_positions = np.asarray(
            loaded_submesh.get_positions(), dtype=np.float32
        ).reshape(-1, 3)
        np.testing.assert_allclose(decoded_positions, positions, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
