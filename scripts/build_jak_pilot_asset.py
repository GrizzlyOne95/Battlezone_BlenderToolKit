#!/usr/bin/env python3
# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2026 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Build an export-ready Jak armature from the original BZ2 FBX set.

Run from Blender, for example:

    blender --background --python scripts/build_jak_pilot_asset.py -- \
        --source-dir "C:/assets/jak" \
        --output-blend "C:/assets/jak/jakpilot.blend" \
        --manifest "C:/assets/jak/jakpilot_manifest.json"

The script imports ``jak_walk.fbx`` as the authoritative mesh/bind pose,
consolidates the fixed animation FBXs, retargets ``jak_skel.fbx`` idle onto
that bind pose, creates the original BZ2 logical aliases and the currently
known Redux Person compatibility aliases, then saves an optional .blend.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy

from bz98tools.jak_animation_builder import (
    JakAnimationBuildError,
    build_jak_animation_set,
    parse_alias_specs,
)


def _script_argv():
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        required=True,
        help="Directory containing jak_walk.fbx, jak_skel.fbx and the fixed Jak animation FBXs.",
    )
    parser.add_argument(
        "--output-blend",
        help="Optional .blend path to save after a successful build.",
    )
    parser.add_argument(
        "--manifest",
        help="Optional JSON report path. The same manifest is also stored on the armature.",
    )
    parser.add_argument(
        "--armature-name",
        default="Jak_Armature",
        help="Name assigned to the canonical jak_walk armature.",
    )
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        metavar="DEST=SOURCE",
        help="Add/override an Action alias, e.g. --alias kneel=idle. Repeat as needed.",
    )
    parser.add_argument(
        "--no-redux-compat-aliases",
        action="store_true",
        help="Do not create the known stand2Kneel/idleParachute/landParachute compatibility aliases.",
    )
    parser.add_argument(
        "--rest-tolerance",
        type=float,
        default=1.0e-4,
        help="Maximum matrix component delta accepted for fixed-FBX common-bind validation.",
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(_script_argv() if argv is None else argv)
    try:
        extra_aliases = parse_alias_specs(args.alias)
        armature, report = build_jak_animation_set(
            args.source_dir,
            context=bpy.context,
            armature_name=args.armature_name,
            include_compat_aliases=not args.no_redux_compat_aliases,
            extra_aliases=extra_aliases,
            rest_tolerance=args.rest_tolerance,
        )

        if args.manifest:
            manifest = Path(args.manifest).expanduser().resolve()
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(report.to_json() + "\n", encoding="utf-8")

        if args.output_blend:
            output = Path(args.output_blend).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(output))

        print("Jak animation build completed")
        print(f"  armature: {armature.name}")
        print(f"  bones: {report.bone_count}")
        print(f"  baked clips: {len(report.clips)}")
        print(f"  aliases: {len(report.aliases)}")
        for clip in report.clips:
            mode = "retarget" if clip.retargeted else "direct"
            print(
                f"  {clip.name:10s} {clip.frames:3d} frames "
                f"({clip.duration_seconds:.3f}s) [{mode}] <- {clip.source_file}"
            )
        return 0
    except JakAnimationBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
