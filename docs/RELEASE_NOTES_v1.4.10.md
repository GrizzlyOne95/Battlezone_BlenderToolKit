# BZ98R Blender Toolkit v1.4.10

## Summary

This patch release brings the published toolkit up to the current `main` branch after v1.4.9. It adds the runtime semantic-verification evidence kit and tightens the experimental controls used to validate advanced Battlezone 98 Redux VDF/GEO behaviors against the game runtime.

## Added

- Added `scripts/make_runtime_evidence_kit.py`, a bpy-free generator for synthetic Phase A-F runtime-validation assets.
- Added deterministic evidence manifests and runtime-session record templates that link authored state, exported bytes, and observed Redux behavior.
- Added automated tests for the runtime evidence-kit generator.
- Added strict enforcement of the Battlezone runtime's 8-character directly referenced asset-name limit in generated evidence assets.

## Changed

- Expanded `docs/RUNTIME_SEMANTIC_VERIFICATION.md` with normative session-identity, asset-integrity, transform, damage-threshold, and scope-of-conclusion controls.
- Runtime verification now explicitly requires executable/configuration hashes, renderer/backend identity, deployed-asset hashes, cache-safe filename discipline, non-orthogonal transform cases, bidirectional threshold bracketing, and separation of AI-pathing evidence from direct traversal evidence.
- Bumped the Blender addon package version to 1.4.10 so installed builds can be distinguished from v1.4.9.

## Validation

- The release workflow regenerates synthetic fixtures and runs the bpy-free semantic test suite before packaging.
- The release workflow verifies `bl_info["version"] == (1, 4, 10)` before creating the release.
- The runtime evidence-kit test coverage is included in the repository at this tag.

## Installation

1. Download `bz98tools.zip` from this release.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install...** and select `bz98tools.zip`.
4. Enable the Battlezone toolkit addon if Blender does not enable it automatically.

**Full changes:** compare `v1.4.9...v1.4.10` after the release tag is created.
