import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import _bootstrap  # noqa: F401

from bz98tools.ogrefast import backend


class _Operator:
    def __init__(self):
        self.reports = []

    def report(self, levels, message):
        self.reports.append((set(levels), message))


class OgreMeshUpgraderTests(unittest.TestCase):
    def test_resolve_prefers_upgrader_beside_configured_converter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            converter = os.path.join(temp_dir, "OgreXMLConverter.exe")
            upgrader = os.path.join(temp_dir, "OgreMeshUpgrader.exe")
            open(converter, "wb").close()
            open(upgrader, "wb").close()

            self.assertEqual(
                os.path.abspath(upgrader),
                os.path.abspath(backend._resolve_mesh_upgrader(converter)),
            )

    def test_upgrade_runs_in_place_against_mesh_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mesh = os.path.join(temp_dir, "pilot.mesh")
            converter = os.path.join(temp_dir, "OgreXMLConverter")
            upgrader = os.path.join(temp_dir, "OgreMeshUpgrader")
            open(mesh, "wb").close()
            open(converter, "wb").close()
            open(upgrader, "wb").close()

            operator = _Operator()
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with mock.patch.object(backend.subprocess, "run", return_value=completed) as run:
                self.assertTrue(
                    backend._upgrade_mesh_for_bzr(
                        operator,
                        mesh,
                        xml_converter=converter,
                    )
                )

            run.assert_called_once_with(
                [upgrader, os.path.abspath(mesh)],
                cwd=os.path.abspath(temp_dir),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertFalse(operator.reports)

    def test_upgrade_failure_is_not_reported_as_successful_export(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mesh = os.path.join(temp_dir, "pilot.mesh")
            converter = os.path.join(temp_dir, "OgreXMLConverter")
            upgrader = os.path.join(temp_dir, "OgreMeshUpgrader")
            open(mesh, "wb").close()
            open(converter, "wb").close()
            open(upgrader, "wb").close()

            operator = _Operator()
            completed = SimpleNamespace(
                returncode=3,
                stdout="",
                stderr="upgrade failed\n",
            )
            with mock.patch.object(backend.subprocess, "run", return_value=completed):
                self.assertFalse(
                    backend._upgrade_mesh_for_bzr(
                        operator,
                        mesh,
                        xml_converter=converter,
                    )
                )

            self.assertTrue(operator.reports)
            self.assertIn("exit code 3", operator.reports[-1][1])


if __name__ == "__main__":
    unittest.main()
