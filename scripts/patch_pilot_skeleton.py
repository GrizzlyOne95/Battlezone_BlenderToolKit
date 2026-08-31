#!/usr/bin/env python3
# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Patch named pilot animations into an original Redux OGRE skeleton."""

from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Avoid executing the Blender-dependent bz98tools package initializer when this
# command is run from normal CPython.  This is the same pattern used by tests.
if "bz98tools" not in sys.modules:
    import types

    package = types.ModuleType("bz98tools")
    package.__path__ = [os.path.join(_REPO_ROOT, "bz98tools")]
    sys.modules["bz98tools"] = package

from bz98tools.pilot_animation_patch import (  # noqa: E402
    PilotAnimationPatchError,
    load_skeleton,
    patch_skeleton_files,
)


def _parser():
    parser = argparse.ArgumentParser(
        description=(
            "Copy selected named animations from a Blender-exported/replacement "
            "pilot skeleton into an original stock Redux skeleton while preserving "
            "the stock bone contract and serializer version."
        )
    )
    parser.add_argument("stock", help="Original stock .skeleton used as the authority")
    parser.add_argument(
        "replacement", help="Replacement/exported .skeleton containing custom animation"
    )
    parser.add_argument("output", help="New patched .skeleton path")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--animation",
        "-a",
        action="append",
        dest="animations",
        metavar="NAME",
        help="Animation name to patch; repeat for multiple clips",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Patch every animation present in the replacement skeleton",
    )
    parser.add_argument(
        "--bind-tolerance",
        type=float,
        default=1.0e-4,
        help="Absolute/relative tolerance for imported/exported bind transforms",
    )
    parser.add_argument(
        "--skip-bind-validation",
        action="store_true",
        help="Only validate animation track handles/names; not recommended",
    )
    parser.add_argument(
        "--validate-chunks",
        action="store_true",
        help="Enable strict OGRE chunk-size validation while reading/writing",
    )
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.all:
            replacement = load_skeleton(
                args.replacement, validate_chunk_sizes=args.validate_chunks
            )
            animation_names = list(replacement.animation_map)
        else:
            animation_names = args.animations

        patched = patch_skeleton_files(
            args.stock,
            args.replacement,
            args.output,
            animation_names,
            validate_bind_pose=not args.skip_bind_validation,
            bind_tolerance=args.bind_tolerance,
            validate_chunk_sizes=args.validate_chunks,
        )
        output = load_skeleton(args.output, validate_chunk_sizes=args.validate_chunks)
        print(
            f"Patched {len(patched)} animation(s) into {args.output} "
            f"using {output.serializer_version}: {', '.join(patched)}"
        )
        return 0
    except (OSError, PilotAnimationPatchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
