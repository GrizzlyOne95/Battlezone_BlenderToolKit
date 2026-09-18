import importlib
import unittest

import _bootstrap  # noqa: F401

import bz98tools.ogrefast as ogrefast


class PureKenshiFacadeTests(unittest.TestCase):
    def test_historical_import_name_resolves_to_complete_pure_surface(self):
        module = importlib.import_module("kenshi_blender_tool")
        self.assertTrue(getattr(module, "__bz98_pure_backend__", False))
        self.assertIs(module, ogrefast.ensure_kenshi_blender_tool())

        expected = {
            "AnimationData",
            "BlenderAnimationTrack",
            "BoneAssignmentData",
            "BoneData",
            "GeometryData",
            "KenshiObjectSerializer",
            "Matrix3",
            "MeshData",
            "MeshVersion",
            "OgreQuaternion",
            "OperationType",
            "SkeletonData",
            "SkeletonVersion",
            "SubMeshData",
            "Vector3",
            "VertexGroupData",
        }
        self.assertTrue(expected.issubset(set(module.__all__)))
        for name in expected:
            self.assertTrue(hasattr(module, name), name)

        serializer = module.KenshiObjectSerializer()
        self.assertTrue(serializer.is_pure_python)
        mesh = serializer.create_mesh("facade.mesh")
        skeleton = serializer.create_skeleton("facade.skeleton")
        self.assertIsInstance(mesh, module.MeshData)
        self.assertIsInstance(skeleton, module.SkeletonData)

    def test_native_probe_does_not_mistake_installed_facade_for_native(self):
        available, reason = ogrefast.probe_native_backend(force=True)
        self.assertFalse(available)
        self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main()
