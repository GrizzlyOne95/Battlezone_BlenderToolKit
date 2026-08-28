# Redux Pilot Animation Reference

The toolkit exposes the verified Redux/legacy `Person` animation-index contract in `bz98tools.pilot_animation_profiles` and in the Blender **Battlezone > Pilot Animation Reference** panel.

## Verified Person animation indices

These indices are semantic `Person` animation slots used by the existing BZR model porter. They are not Ogre skeleton array positions.

| Person index | Ogre animation name | Notes |
| ---: | --- | --- |
| 0 | `stand2Kneel` | legacy Person |
| 1 | `kneel2stand` | legacy Person |
| 2 | `idle` | legacy Person |
| 3 | `fireRecoilSniper` | legacy Person |
| 4 | `runForward` | legacy Person |
| 5 | `runBackward` | legacy Person |
| 6 | `runLeft` | legacy Person strafe-left slot |
| 7 | `runRight` | legacy Person strafe-right slot |
| 8 | `death1` | legacy fall/snipe-death slot |
| 9 | `idleParachute` | Redux-specific |
| 10 | `landParachute` | Redux-specific |
| 11 | `jump` | Redux-specific |

The source porter constants are `PERSON_ANIM_CROUCH` through `PERSON_ANIM_JUMP`, and the porter maps those slots onto the named Ogre animations when constructing pilot skeleton animation data.

## Additional stock Ogre animation names

The stock Redux pilot skeletons also contain named animation clips for which no `Person` animation index has been proven:

- `Take_001`
- `death2`
- `idleEject` / `idleElect` (observed spelling variant)
- `walkBackward`
- `walkForward`
- `walkLeft`
- `walkRight`

These remain name-addressed Ogre clips. The toolkit deliberately reports their Person index as **none** rather than inferring an index from their order in a `.skeleton` file.

## API

```python
from bz98tools import pilot_animation_profiles as profiles

profiles.PERSON_ANIMATION_INDEX_TO_NAME[4]
# "runForward"

profiles.person_animation_index("idleParachute")
# 9

profiles.person_animation_index("walkForward")
# None

profiles.KNOWN_PILOT_CLIPS
# union of verified indexed names plus observed named-only stock clips
```

Individual stock `.skeleton` files remain authoritative for which named clips they actually contain. In particular, `idleEject` and `idleElect` are treated as spelling variants rather than as two guaranteed clips in every stock pilot skeleton.
