# Headless Blender integration test for the advanced semantic authoring work.
# Run: blender --background --python tests/blender_integration_test.py
# Uses only synthetic data; no game assets.

import os
import struct
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(tempfile.gettempdir(), "bz_integration")
os.makedirs(TMP, exist_ok=True)
sys.path.insert(0, REPO_ROOT)

import bpy  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not condition:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# 1. Register the addon from the repo tree.
#
# All property declarations across the addon use the annotation-only form
# (`Prop: bpy.props.XxxProperty(...)`), which Blender converts natively on
# every supported version. The suite also asserts no deferred descriptors
# survive registration below.
# ---------------------------------------------------------------------------
try:
    import bpy

    import bz98tools

    bz98tools.register()
    check("addon registers cleanly", True)
except Exception as exc:  # pragma: no cover
    import traceback

    traceback.print_exc()
    check("addon registers cleanly", False, str(exc))
    sys.exit(1)

# RNA conversion sanity: no property group may keep a _PropertyDeferred in
# its class dict after register_class (symptom of a dead declaration style).
try:
    _deferred = []
    for _modname in ("bz98tools",):
        _module = sys.modules[_modname]
        for _name in dir(_module):
            _obj = getattr(_module, _name)
            if isinstance(_obj, type) and issubclass(
                _obj, getattr(bpy.types, "PropertyGroup", object)
            ):
                for _pname, _pval in list(vars(_obj).items()):
                    if type(_pval).__name__ == "_PropertyDeferred":
                        _deferred.append(f"{_obj.__name__}.{_pname}")
    check("all properties converted by RNA", not _deferred, ", ".join(_deferred[:4]))
except Exception as exc:
    check("all properties converted by RNA", False, str(exc))

from bz98tools import export_vdf, import_vdf, validation, semantics, vdf_file  # noqa: E402
from bz98tools import vdf_classes  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def add_box(name, size=1.0, location=(0, 0, 0)):
    half = size / 2
    verts = [
        (-half, -half, -half),
        (half, -half, -half),
        (half, half, -half),
        (-half, half, -half),
        (-half, -half, half),
        (half, -half, half),
        (half, half, half),
        (-half, half, half),
    ]
    faces = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_minimal_geo(path, name):
    """A tiny valid legacy .geo: 4 verts / 4 normals / 1 quad."""
    with open(path, "wb") as f:
        f.write(struct.pack("=4si16siii", b"OEG.", 69, name.encode(), 4, 1, 0))
        for vx, vy, vz in ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)):
            f.write(struct.pack("=fff", vx, vy, vz))
        for nx, ny, nz in ((0, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, 1)):
            f.write(struct.pack("=fff", nx, ny, nz))
        f.write(
            struct.pack(
                "=iiBBBffffi3s13sii",
                0,
                4,
                128,
                128,
                128,
                0.0,
                0.0,
                1.0,
                1.0,
                0,
                bytes((4, 1, 0)),
                b"testmap".ljust(13, b"\0"),
                0,
                0,
            )
        )
        for vi in range(4):
            f.write(struct.pack("=iiff", vi, vi, float(vi % 2), 0.0))


# ---------------------------------------------------------------------------
# 2. Build an authoring scene exercising every new feature.
# ---------------------------------------------------------------------------
scene = bpy.context.scene
props = scene.SDFVDFPropertyGroup
props.Name = "integ"
props.VehicleType = 1
props.VehicleSize = 2

root = add_box("sim11")
root.GEOPropertyGroup.GEOType = 60
root.GEOPropertyGroup.BoundsMode = "PRESERVE"
root.GEOPropertyGroup.HasAuthoredBounds = True
root.GEOPropertyGroup.GeoCenterX = 5.0
root.GEOPropertyGroup.SphereRadius = 42.0
root.GEOPropertyGroup.DamageGeo1 = "simd11"
root.GEOPropertyGroup.HasDamageVariants = True
root.GEOPropertyGroup.GEOFlags = 0x00081234
raw_before = int(root.GEOPropertyGroup.GEOFlags)
root.GEOPropertyGroup.FlagKeepBounds = True
check(
    "FlagKeepBounds set preserves other bits",
    (int(root.GEOPropertyGroup.GEOFlags) & ~1) == (raw_before & ~1)
    and int(root.GEOPropertyGroup.GEOFlags) & 1,
    hex(int(root.GEOPropertyGroup.GEOFlags)),
)
root.GEOPropertyGroup.FlagKeepBounds = False
check(
    "FlagKeepBounds clear preserves other bits",
    int(root.GEOPropertyGroup.GEOFlags) == raw_before,
    hex(int(root.GEOPropertyGroup.GEOFlags)),
)

deck = add_box("flr11", location=(0, 0, -2))
deck.parent = root
deck.GEOPropertyGroup.GEOType = 9

rotor = add_box("rot11", location=(2, 0, 0))
rotor.parent = root
rotor.GEOPropertyGroup.GEOType = 66

nac = add_box("nac11", location=(-2, 0, 0))
nac.parent = root
nac.GEOPropertyGroup.GEOType = 67

pov = bpy.data.objects.new("pov11", None)
pov.empty_display_type = "SINGLE_ARROW"
pov.location = (0, 0.85, 0.45)
bpy.context.scene.collection.objects.link(pov)
pov.GEOPropertyGroup.GEOType = 40
pov.GEOPropertyGroup.IsPOVHelper = True

spinner = bpy.data.objects.new("spn11", None)
spinner.empty_display_type = "ARROWS"
bpy.context.scene.collection.objects.link(spinner)
spinner.parent = root
sp = spinner.GEOPropertyGroup
sp.GEOType = 15
sp.IsSpinnerHelper = True
sp.SpinnerTarget = "sim11"
sp.SpinnerAxis = (0.0, 1.0, 0.0)
sp.SpinnerSpeed = 0.5

vlocs = scene.bz_vloc_chunks
entry = vlocs.add()
entry.name = "VLOC 1"
entry.kind = "POV"
entry.class_id = 40
entry.matrix = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0.0, 0.7, 0.4)
entry.preserve_raw = False
entry.payload_b64 = ""
entry.target_object = pov.name

out_vdf = os.path.join(TMP, "integ.vdf")
result = export_vdf.export(
    bpy.context,
    filepath=out_vdf,
    ExportAnimations=False,
    ExportVDFOnly=True,
)
check("export completes", result == {"FINISHED"})
check("vdf written", os.path.exists(out_vdf))

# ---------------------------------------------------------------------------
# 3. Pure-layer verification of exported bytes.
# ---------------------------------------------------------------------------
data = open(out_vdf, "rb").read()
parsed = vdf_file.parse_vdf(data)
check("exported file parses", parsed.geocount >= 5, f"geocount={parsed.geocount}")

names = {}
for slot, raw in enumerate(parsed.band_records(0)):
    g = vdf_classes.GEOData()
    g.Read(raw, 0)
    n = g.name[:4].lower()
    if n != "null":
        names[n] = g
check("base parts exported", set(names) >= {"sim1", "flr1", "rot1", "nac1", "pov1"}, sorted(names))
check("spinner helper slot follows target", any(g.type == 15 for g in names.values()))
check(
    "authored bounds preserved through PRESERVE mode",
    abs(names["sim1"].sphereradius - 42.0) < 1e-5,
    names["sim1"].sphereradius,
)
flags_out = int(names["sim1"].geoflags) & 0xFFFFFFFF
check("object flags round-trip", flags_out == 0x00081234, hex(flags_out))

damage_band = vdf_classes.GEOData()
damage_band.Read(parsed.band_records(1)[0], 0)
check(
    "damage variant band synthesized",
    damage_band.name[:4].lower() == "simd",
    damage_band.name,
)
check(
    "damage variant copies base fields",
    abs(damage_band.sphereradius - 42.0) < 1e-5,
)

check("VLOC chunk serialized", len(parsed.vlocs) == 1 and parsed.vlocs[0].class_id == 40)

# ---------------------------------------------------------------------------
# 4. Import round trip into a clean scene with real .geo files present.
# ---------------------------------------------------------------------------
for geo_name in ("sim11", "simd11"):
    make_minimal_geo(os.path.join(TMP, geo_name + ".geo"), geo_name)

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

scene.bz_preserved_chunks.clear()
scene.bz_damage_band_records.clear()
scene.bz_vloc_chunks.clear()

import_vdf.load(bpy.context, out_vdf, ImportMapTextures=False)

objs = bpy.data.objects
check("import recreates root mesh", objs.get("SIM11") is not None or objs.get("sim11") is not None)
re_root = next(
    (o for o in objs if o.name.lower().startswith("sim1")), None
)
check("root found after import", re_root is not None)
if re_root is not None:
    gp = re_root.GEOPropertyGroup
    check("imported type preserved", int(gp.GEOType) == 60, gp.GEOType)
    check("bounds mode defaults to PRESERVE", gp.BoundsMode == "PRESERVE")
    check(
        "imported bounds values restored",
        abs(float(gp.SphereRadius) - 42.0) < 1e-4 and abs(float(gp.GeoCenterX) - 5.0) < 1e-4,
        f"{gp.SphereRadius}/{gp.GeoCenterX}",
    )
    check("damage variant name attached", gp.DamageGeo1.lower() == "simd11")
    check("HasDamageVariants set", bool(gp.HasDamageVariants))
    check(
        "unknown flag bits survive import",
        int(gp.GEOFlags) & 0xFFFFFFFF == 0x00081234,
        hex(int(gp.GEOFlags)),
    )

re_pov = next((o for o in objs if o.name.lower().startswith("pov1")), None)
check("eyepoint imported as object", re_pov is not None)
if re_pov is not None:
    check(
        "eyepoint class preserved",
        int(re_pov.GEOPropertyGroup.GEOType) == 40,
    )

re_spinner = next((o for o in objs if o.name.lower().startswith("spn1")), None)
check("spinner helper recreated", re_spinner is not None)
if re_spinner is not None:
    check(
        "spinner helper flagged",
        bool(re_spinner.GEOPropertyGroup.IsSpinnerHelper),
    )

check("VLOC entries stored on scene", len(scene.bz_vloc_chunks) == 1)

# ---------------------------------------------------------------------------
# 5. Validation executes end to end.
# ---------------------------------------------------------------------------
issues = validation.collect_legacy_validation_issues(bpy.context, "ALL", "VEHICLE")
check("validation runs", isinstance(issues, list))
severities = {}
for issue in issues:
    severities[issue["severity"]] = severities.get(issue["severity"], 0) + 1
print(f"[INFO] validation issue counts: {severities}")
scopes = {issue["scope"] for issue in issues}
print(f"[INFO] validation scopes seen: {sorted(scopes)}")

# Emitter overflow check: add nine validly-named class-76 parts.
for i in range(9):
    e = add_box(f"s{i}k11")
    e.GEOPropertyGroup.GEOType = 76
issues2 = validation.collect_legacy_validation_issues(bpy.context, "ALL", "VEHICLE")
overflow = [
    i
    for i in issues2
    if i["scope"] == "Emitters" and i["severity"] == "ERROR"
]
check("smoke overflow flagged ERROR", len(overflow) >= 1)

# Deck steepness check: FLOOR part whose faces are all vertical is impossible
# with a cube (has up faces); instead validate POV scale warning triggers.
pov_obj = next((o for o in bpy.data.objects if o.name.lower().startswith("pov1")), None)
if pov_obj is not None:
    pov_obj.scale = (2.0, 2.0, 2.0)
    issues3 = validation.collect_legacy_validation_issues(bpy.context, "ALL", "VEHICLE")
    pov_warn = [i for i in issues3 if i["scope"] == "Eyepoint" and "scale" in i["message"]]
    check("eyepoint scale warning fires", len(pov_warn) >= 1)

# ---------------------------------------------------------------------------
print()
if FAILURES:
    print(f"INTEGRATION FAILURES: {FAILURES}")
    sys.exit(1)
print("ALL INTEGRATION CHECKS PASSED")
