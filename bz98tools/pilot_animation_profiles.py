# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Pure helpers for identifying Redux pilot rigs and animation contracts.

This module intentionally has no Blender dependency so profile detection, clip
matching, and the Redux Person animation-index contract can be covered by the
normal bpy-free test suite and reused by asset builders.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

SERIALIZER_V1_80 = "[Serializer_v1.80]"
SERIALIZER_V1_10 = "[Serializer_v1.10]"

# Authoritative Person animation indices used by the existing BZR model porter.
# Indices 0-8 are the legacy Person contract; 9-11 are Redux additions.
#
# Keep these semantic indices separate from the Ogre .skeleton animation map:
# Ogre clips are name-addressed, and several additional stock skeleton clips do
# not have a proven Person/legacy animation index.
PERSON_ANIMATION_INDEX_TO_NAME = {
    0: "stand2Kneel",
    1: "kneel2stand",
    2: "idle",
    3: "fireRecoilSniper",
    4: "runForward",
    5: "runBackward",
    6: "runLeft",
    7: "runRight",
    8: "death1",
    9: "idleParachute",
    10: "landParachute",
    11: "jump",
}

PERSON_ANIMATION_NAME_TO_INDEX = {
    name: index for index, name in PERSON_ANIMATION_INDEX_TO_NAME.items()
}

# Additional names observed in stock Redux pilot .skeleton files. These are
# valid named Ogre clips, but no Person animation index is currently proven for
# them. idleEject/idleElect is a stock spelling variant; different skeletons
# may contain one spelling rather than both.
REDUX_NAMED_ONLY_PILOT_CLIPS = (
    "Take_001",
    "death2",
    "idleEject",
    "idleElect",
    "walkBackward",
    "walkForward",
    "walkLeft",
    "walkRight",
)

# Union of every currently observed/known stock pilot clip name. Individual
# stock files remain authoritative for which spelling/clip subset they contain.
KNOWN_PILOT_CLIPS = tuple(
    dict.fromkeys(
        tuple(PERSON_ANIMATION_INDEX_TO_NAME.values())
        + REDUX_NAMED_ONLY_PILOT_CLIPS
    )
)

_CLIP_ALIASES = {
    "idleeject": ("idleElect",),
    "idleelect": ("idleEject",),
}

_BLENDER_NUMERIC_SUFFIX = re.compile(r"\.\d{3}$")


def person_animation_index(clip_name: str):
    """Return the verified Person animation index for ``clip_name`` or ``None``.

    Matching is case-insensitive. Named-only Ogre clips intentionally return
    ``None`` rather than receiving an invented numeric index.
    """

    normalized = str(clip_name or "").lower()
    for name, index in PERSON_ANIMATION_NAME_TO_INDEX.items():
        if name.lower() == normalized:
            return index
    return None


def pilot_animation_reference_rows():
    """Return stable rows for UI/docs: ``(index-or-None, name, indexed)``."""

    rows = [
        (index, name, True)
        for index, name in sorted(PERSON_ANIMATION_INDEX_TO_NAME.items())
    ]
    rows.extend((None, name, False) for name in REDUX_NAMED_ONLY_PILOT_CLIPS)
    return tuple(rows)


def _bone_name(value):
    return str(getattr(value, "name", value))


def _normalized_names(bone_map: Mapping[int, object] | Iterable[object]):
    values = bone_map.values() if hasattr(bone_map, "values") else bone_map
    return [_bone_name(value) for value in values]


def detect_pilot_profile(
    bone_map: Mapping[int, object] | Iterable[object],
    serializer_version: str | None = None,
):
    """Return a best-effort stock pilot profile from its bone signature.

    The known stock families are identified primarily from nation-prefixed helper
    bones and the observed 3P/FP bone counts. POV helper presence is a stronger
    first-person signal than count alone. Unknown/custom rigs are reported as
    such rather than force-fit to a stock profile.
    """

    names = _normalized_names(bone_map)
    lowered = [name.lower() for name in names]
    count = len(names)

    family = "Unknown"
    family_code = "unknown"
    if any(name.startswith("ssp") for name in lowered):
        family = "Soviet"
        family_code = "ssp"
    elif any(name.startswith("asp") for name in lowered):
        family = "American"
        family_code = "asp"
    elif any(name.startswith("bsp") for name in lowered):
        family = "Black Dog"
        family_code = "bsp"

    has_pov_helper = any(
        name.endswith("pov") or "pov" in name.rsplit("_", 1)[-1]
        for name in lowered
    )
    has_gc_helper = any(name.endswith("gc1") for name in lowered)

    view = "Unknown"
    if has_pov_helper:
        view = "First Person"
    elif count == 73:
        view = "First Person"
    elif count == 71:
        view = "Third Person"
    elif family_code == "ssp" and count == 32:
        # Soviet 3P and FP both have 32 bones. The FP skeleton carries the
        # GC1/POV helpers while the 3P skeleton does not.
        view = "First Person" if has_gc_helper else "Third Person"

    expected_count = None
    if family_code == "ssp" and view in {"First Person", "Third Person"}:
        expected_count = 32
    elif family_code in {"asp", "bsp"} and view == "Third Person":
        expected_count = 71
    elif family_code in {"asp", "bsp"} and view == "First Person":
        expected_count = 73

    expected_serializer = None
    if view == "First Person":
        expected_serializer = SERIALIZER_V1_10
    elif view == "Third Person":
        expected_serializer = SERIALIZER_V1_80

    known = family_code != "unknown" and view != "Unknown"
    confidence = (
        "HIGH"
        if known and expected_count == count
        else ("MEDIUM" if known else "LOW")
    )

    warnings = []
    if expected_count is not None and count != expected_count:
        warnings.append(
            f"Expected {expected_count} bones for {family} {view}, found {count}."
        )
    if (
        serializer_version
        and expected_serializer
        and serializer_version != expected_serializer
    ):
        warnings.append(
            f"Expected {expected_serializer} for {view}, found {serializer_version}."
        )

    label = f"{family} {view}" if known else "Unknown / Custom Pilot Rig"
    key = (
        f"{family_code}_"
        f"{'fp' if view == 'First Person' else 'tp' if view == 'Third Person' else 'unknown'}"
    )

    return {
        "key": key,
        "label": label,
        "family": family,
        "family_code": family_code,
        "view": view,
        "bone_count": count,
        "expected_bone_count": expected_count,
        "serializer_version": serializer_version or "",
        "expected_serializer": expected_serializer or "",
        "known": known,
        "confidence": confidence,
        "warnings": tuple(warnings),
    }


def strip_blender_numeric_suffix(name: str) -> str:
    """Turn ``runForward.001`` back into ``runForward`` for auto-matching."""

    return _BLENDER_NUMERIC_SUFFIX.sub("", str(name or ""))


def clip_action_match_candidates(clip_name: str):
    """Return ordered source Action names worth trying for a stock clip."""

    clip_name = str(clip_name or "")
    candidates = [clip_name]
    candidates.extend(_CLIP_ALIASES.get(clip_name.lower(), ()))
    return tuple(dict.fromkeys(candidates))


def action_name_matches_clip(clip_name: str, action_name: str) -> bool:
    """Case-insensitive exact/alias match, tolerating Blender's ``.001`` suffix."""

    action_base = strip_blender_numeric_suffix(action_name).lower()
    return any(
        action_base == candidate.lower()
        for candidate in clip_action_match_candidates(clip_name)
    )
