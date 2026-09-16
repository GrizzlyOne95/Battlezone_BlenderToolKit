import struct
import unittest

import numpy as np

from bz98tools.ogrefast.pure.import_compat import ImportedSubMeshData
from bz98tools.ogrefast.pure.model import GeometryData, VertexBuffer, VertexElement


class OgreImportCornerExpansionTests(unittest.TestCase):
    def _submesh(self):
        rows = [
            ((1.0, 0.0, 0.0), (0.0, 0.0), (255, 0, 0, 255)),
            ((0.0, 1.0, 0.0), (1.0, 0.0), (0, 255, 0, 255)),
            ((0.0, 0.0, 1.0), (0.0, 1.0), (0, 0, 255, 255)),
        ]
        payload = bytearray()
        for normal, uv, rgba in rows:
            payload.extend(struct.pack("<3f2f4B", *normal, *uv, *rgba))

        geometry = GeometryData(
            vertex_count=3,
            declaration=[
                VertexElement("NORMAL", 0, 0, "FLOAT3", 0),
                VertexElement("TEXCOORD", 0, 12, "FLOAT2", 0),
                VertexElement("DIFFUSE", 0, 20, "COLOUR_ABGR", 0),
            ],
            buffers=[VertexBuffer(0, 24, bytes(payload))],
        )
        submesh = ImportedSubMeshData()
        submesh._wire_geometry = geometry
        submesh.faces = [0, 1, 2, 0, 2, 1]
        submesh.face_count = 2
        return submesh

    def test_uvs_expand_from_vertex_domain_to_loop_domain(self):
        submesh = self._submesh()
        uv = submesh.get_texcoords()
        self.assertEqual(uv.shape, (1, 12))
        np.testing.assert_allclose(
            uv.reshape(1, 6, 2)[0],
            [
                [0.0, 1.0],
                [1.0, 1.0],
                [0.0, 0.0],
                [0.0, 1.0],
                [0.0, 0.0],
                [1.0, 1.0],
            ],
            atol=1e-6,
        )

    def test_normals_and_colours_expand_to_loop_domain(self):
        submesh = self._submesh()
        normals = np.asarray(submesh.get_normals(), dtype=np.float32)
        self.assertEqual(normals.shape, (6, 3))
        # Ogre (x,y,z) -> Blender (x,-z,y)
        np.testing.assert_allclose(normals[2], [0.0, -1.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(normals[4], normals[2], atol=1e-6)

        colors, alpha = submesh.get_colors()
        self.assertEqual(colors.shape, (6, 4))
        self.assertEqual(alpha.shape, (6, 4))
        np.testing.assert_allclose(colors[0], [1.0, 0.0, 0.0, 1.0], atol=1e-6)
        np.testing.assert_allclose(colors[3], colors[0], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
