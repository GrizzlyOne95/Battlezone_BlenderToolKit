import os
import unittest

from bz98tools.ogrefast.batch import export_selected_individually


class _Object:
    def __init__(self, name, object_type="MESH", selected=False):
        self.name = name
        self.type = object_type
        self._selected = bool(selected)

    def select_get(self):
        return self._selected

    def select_set(self, value):
        self._selected = bool(value)


class _Objects(list):
    active = None


class _ViewLayer:
    def __init__(self, objects):
        self.objects = objects


class _Context:
    def __init__(self, objects):
        self.view_layer = _ViewLayer(objects)


class OgreBatchTests(unittest.TestCase):
    def test_exports_selected_meshes_and_restores_selection(self):
        first = _Object("alpha", selected=True)
        armature = _Object("rig", object_type="ARMATURE", selected=True)
        second = _Object("beta", selected=True)
        ignored = _Object("gamma", selected=False)
        objects = _Objects([first, armature, second, ignored])
        objects.active = armature
        context = _Context(objects)

        calls = []

        def export_one(path, obj):
            calls.append(
                (
                    path,
                    obj.name,
                    [item.name for item in objects if item.select_get()],
                    objects.active.name,
                )
            )
            return {"FINISHED"}

        results = export_selected_individually(
            context, os.path.join("out", "chosen.mesh"), export_one
        )

        self.assertEqual(
            calls,
            [
                (os.path.join("out", "alpha.mesh"), "alpha", ["alpha"], "alpha"),
                (os.path.join("out", "beta.mesh"), "beta", ["beta"], "beta"),
            ],
        )
        self.assertEqual([result[2] for result in results], [{"FINISHED"}, {"FINISHED"}])
        self.assertTrue(first.select_get())
        self.assertTrue(armature.select_get())
        self.assertTrue(second.select_get())
        self.assertFalse(ignored.select_get())
        self.assertIs(objects.active, armature)

    def test_restores_state_when_export_raises(self):
        first = _Object("alpha", selected=True)
        second = _Object("beta", selected=True)
        objects = _Objects([first, second])
        objects.active = second
        context = _Context(objects)

        def explode(path, obj):
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            export_selected_individually(context, "chosen.mesh", explode)

        self.assertTrue(first.select_get())
        self.assertTrue(second.select_get())
        self.assertIs(objects.active, second)

    def test_no_selected_meshes_returns_empty(self):
        armature = _Object("rig", object_type="ARMATURE", selected=True)
        objects = _Objects([armature])
        context = _Context(objects)
        self.assertEqual(
            export_selected_individually(
                context, "chosen.mesh", lambda path, obj: {"FINISHED"}
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
