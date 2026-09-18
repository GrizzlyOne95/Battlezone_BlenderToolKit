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


def test_cli_adds_complete_known_redux_pilot_surface():
    backend = _read(BACKEND)
    source = _read(WRAPPER)
    # The CLI must reuse the explicit Jak profile as the single source of
    # truth rather than maintaining a second independent index map.
    assert "JAK_PERSON_COMPAT_ALIASES" in backend
    assert "JAK_PERSON_COMPAT_ALIASES" in source
    assert "KNOWN_PILOT_CLIPS" in source
    assert "_missing_compat" in source
    # Native sniper/crouch FSM drives the three-stage melee attack.
    for alias in (
        '"stand2Kneel": "attack1"',
        '"fireRecoilSniper": "attack2"',
        '"kneel2stand": "attack3"',
    ):
        assert alias in backend, alias
    for alias in (
        '"runForward": "walk"',
        '"runBackward": "walk"',
        '"runLeft": "walk"',
        '"runRight": "walk"',
        '"death1": "death"',
        '"idleParachute": "idle"',
        '"landParachute": "idle"',
        '"Take_001": "idle"',
        '"death2": "death"',
        '"idleEject": "idle"',
        '"idleElect": "idle"',
        '"walkBackward": "walk"',
        '"walkForward": "walk"',
        '"walkLeft": "walk"',
        '"walkRight": "walk"',
    ):
        assert alias in backend, alias


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


def test_stand_up_fix_never_rewrites_pose_curves():
    # Regression test: rotating baked root-bone keys a second time
    # double-rotates the whole animal (handstand in game). The posture fix
    # may only move rest pose and mesh data; sampled curves stay untouched.
    backend = _read(BACKEND)
    start = backend.find("def _apply_stand_up_fix")
    assert start != -1
    end = backend.find("\ndef ", start + 1)
    body = backend[start:end if end != -1 else len(backend)]
    assert "keyframe_points" not in body
    assert "rotation_quaternion" not in body
    assert "edit_bone.transform" in body or "edit_bones" in body
