# Battlezone Blender Toolkit v1.5.1

## Summary

A usability release for the Maya plug-in and the Blender add-on. No export or import behaviour changed.

## Fixed

- Maya panel: the **Selected Part** header no longer clips the **Tag Selected as BZ Part** and **Generate Collision Settings** buttons under the next section. The header can now be collapsed like the other sections.

## Changed

- Tooltips for part (GEO) and model (VDF/SDF) fields are now written in plain English, in both the Blender add-on and the Maya panel. Each says what the field does in game, or says "Advanced: leave at the imported value" when that is the right advice. Fields whose in-game effect is unknown say so.
- The old technical notes from those tooltips are kept in `docs/ADVANCED_AUTHORING.md`, section 11.
- Section titles: **Engine Object Flags (advanced)**, and **Damage Representation (read by the game, no effect in stock)**. The game loads damage-state mesh names, but stock BZ98 1.5 and Redux never switch to them. They are kept so files round-trip.

## Downloads

- `Battlezone_BlenderToolKit-v1.5.1.zip`: the Blender add-on.
- `Battlezone_MayaToolKit-v1.5.1.zip`: the Maya plug-in module (see `maya/README.md` for install).
