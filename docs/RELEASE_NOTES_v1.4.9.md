# BZ98R Blender Toolkit v1.4.9

## Summary

This release rolls the current Blender Toolkit work since v1.4.8 into a new packaged addon. It includes the advanced VDF/GEO semantic authoring work, new pilot-animation tooling, model-porting improvements, UI cleanup, stronger validation, and a much broader automated test suite.

## Added

- Advanced GEO/VDF semantic authoring and preservation support, including engine part-class metadata, ObjectFlags handling, authored bounds, damage-band records, VLOC injection data, deck semantics, and unknown-chunk preservation.
- Dedicated Redux pilot-animation patch/reference tooling and UI.
- Additional model-porting support including 32-bit index buffers, Ogre edge-list handling, improved normal-porting behavior, and better duplicate-model tracking.
- Advanced authoring documentation, runtime-test tooling, synthetic fixtures, and expanded validation guidance.
- Automated bpy-free tests plus Blender-level integration coverage for import/export and semantic round trips.

## Changed

- Refactored the addon bootstrap/core layout to reduce the historical monolithic entry point while retaining compatibility.
- Improved Animation and Quick Tools panel layout and general UI scannability.
- Improved texture conversion performance through NumPy-backed vectorized conversion paths.
- Expanded import/export handling for preserved section order, unknown data, advanced flags, bounds, damage records, and VLOC data.

## Fixed

- Fixed addon registration issues involving missing normal-mode enum data.
- Fixed Blender runtime issues found by headless integration testing, including collection-property iteration and preserved section-plan handling.
- Fixed model-porting and texture-conversion regressions found during the code-health and TODO cleanup work.

## Validation

- Existing CI runs the semantic test suite under Python 3.11 and 3.12.
- Synthetic fixtures exercise the advanced binary/semantic features.
- Blender integration testing covers addon registration, property handling, export/parse/import round trips, and validation behavior.

## Installation

1. Download `bz98tools.zip` from this release.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install...** and select `bz98tools.zip`.
4. Enable the Battlezone toolkit addon if Blender does not enable it automatically.

**Full changes:** compare `v1.4.8...v1.4.9` after the release tag is created.
