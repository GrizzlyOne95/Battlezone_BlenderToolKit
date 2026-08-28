import ast
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "bz98tools" / "jak_animation_builder.py"
WRAPPER = ROOT / "scripts" / "build_jak_pilot_asset.py"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_jak_builder_sources_parse_without_blender():
    ast.parse(_read(BACKEND), filename=str(BACKEND))
    ast.parse(_read(WRAPPER), filename=str(WRAPPER))


def test_jak_builder_declares_expected_source_files_and_aliases():
    source = _read(BACKEND)
    for filename in (
        "jak_walk.fbx",
        "jak_skel.fbx",
        "jak_attack01.fbx",
        "jak_attack02.fbx",
        "jak_attack03.fbx",
        "jak_curious.fbx",
        "jak_death01.fbx",
        "jak_eat01.fbx",
    ):
        assert filename in source

    for alias in (
        '"run": "walk"',
        '"jump": "walk"',
        '"attack4": "attack3"',
        '"eat2": "eat1"',
        '"stand2Kneel": "idle"',
        '"idleParachute": "idle"',
        '"landParachute": "idle"',
    ):
        assert alias in source


def test_cli_exposes_integration_ready_inputs():
    source = _read(WRAPPER)
    for option in (
        "--source-dir",
        "--output-blend",
        "--manifest",
        "--alias",
        "--rest-tolerance",
    ):
        assert option in source
