from typing import Any
import base64
import os

INNER_COLLISION_NAMES = {
    "inner_col",
    "innercol",
    "inner_collision",
    "innercollision",
}

OUTER_COLLISION_NAMES = {
    "outer_col",
    "outercol",
    "outer_collision",
    "outercollision",
}

HARDPOINT_SUFFIX_RULES = {
    70: ("Weapon hardpoint", "g"),
    71: ("Cannon hardpoint", "gc"),
    72: ("Rocket hardpoint", "gr"),
    73: ("Mortar hardpoint", "gm"),
    74: ("Special hardpoint", "gs"),
}

SEVERITY_ORDER = {
    "ERROR": 0,
    "WARNING": 1,
    "INFO": 2,
}

LEGACY_ORIENTATION_MESSAGE = (
    "Legacy VDF/SDF/GEO vehicle export expects the nose/front toward Blender +Y. "
    "Direct Redux .mesh export expects Blender -Y; Legacy + Redux auto-port converts the legacy setup."
)

ANIMATION_VALIDATION_PRESETS = {
    "PERSON": {
        "label": "person",
        "required": set(range(0, 12)),
        "message": "Person-style exports normally need animation slots 0-11 defined.",
    },
    "WALKER": {
        "label": "walker",
        "required": set(range(0, 8)),
        "message": "Walker exports normally need animation slots 0-7 defined.",
    },
    "TURRET": {
        "label": "turret",
        "required": {0, 1},
        "message": "Deployable turret exports normally need deploy/undeploy animation slots 0 and 1 defined.",
    },
}


def fixgeoname(name, lod):
    geofilename = list(name)
    if len(geofilename) > 8:
        geofilename = geofilename[0:8]
    if lod in (1, 2, 3):
        geofilename[3] = str(lod)
    else:
        geofilename[3] = "3"
    geofilename[4] = "1"
    return "".join(geofilename)


def parse_legacy_geo_name(name):
    lower_name = (name or "").strip().lower()
    if len(lower_name) < 5:
        return {"valid": False, "reason": "too_short", "lod": None}
    if len(lower_name) > 8:
        return {"valid": False, "reason": "too_long", "lod": None}

    lod_char = lower_name[3]
    if lod_char not in {"1", "2", "3"}:
        return {"valid": False, "reason": "invalid_lod", "lod": None}

    return {"valid": True, "reason": None, "lod": int(lod_char)}


def is_collision_helper_name(name):
    lower_name = (name or "").strip().lower()
    return lower_name in INNER_COLLISION_NAMES or lower_name in OUTER_COLLISION_NAMES


def normalized_export_name(name, lod):
    return fixgeoname(name, lod).lower()


def legacy_geo_suffix(name):
    return ((name or "").strip().lower())[5:]


def legacy_geo_base_prefix(name):
    lower_name = (name or "").strip().lower()
    if len(lower_name) < 5:
        return lower_name
    return lower_name[:3] + lower_name[4]


def _is_numbered_suffix(suffix, prefix, min_index=1, max_index=5):
    if len(suffix) != 3 or not suffix.startswith(prefix):
        return False
    slot = suffix[2:]
    return slot.isdigit() and min_index <= int(slot) <= max_index


def issue_applies(issue, export_mode):
    export_modes = issue.get("export_modes", {"ALL"})
    return "ALL" in export_modes or export_mode in export_modes


def sort_issues(issues):
    return sorted(
        issues,
        key=lambda issue: (
            SEVERITY_ORDER.get(issue["severity"], 99),
            issue.get("target", "").lower(),
            issue.get("message", "").lower(),
        ),
    )


def collect_legacy_validation_issues(
    context, export_mode="ALL", validation_preset="AUTO"
):
    if export_mode == "GEO":
        issues = _collect_geo_export_issues(context)
        issues.append(_orientation_reference_issue({"GEO"}))
        return sort_issues(issues)
    return sort_issues(
        _collect_scene_export_issues(context, validation_preset=validation_preset)
    )


def _make_issue(
    severity,
    scope,
    target,
    message,
    export_modes=None,
    object_name="",
    action="",
):
    return {
        "severity": severity,
        "scope": scope,
        "target": target or "",
        "message": message,
        "export_modes": set(export_modes or {"ALL"}),
        "object_name": object_name or "",
        "action": action or "",
    }


def _orientation_reference_issue(export_modes):
    modes = set(export_modes)
    modes.add("ALL")
    return _make_issue(
        "INFO",
        "Orientation",
        "vehicle front",
        LEGACY_ORIENTATION_MESSAGE,
        modes,
    )


def _derive_legacy_texture_name(name):
    base_name = os.path.splitext((name or "").strip())[0]
    base_name = base_name.replace(" ", "_").lower()
    return base_name[:8]


def _get_image_derived_texture_name(material):
    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return ""

    image_nodes = [
        node
        for node in getattr(node_tree, "nodes", [])
        if getattr(node, "type", "") == "TEX_IMAGE"
        and getattr(node, "image", None) is not None
    ]
    if not image_nodes:
        return ""

    active_node = next(
        (node for node in image_nodes if getattr(node, "select", False)), None
    )
    if active_node is None:
        active_node = image_nodes[0]

    image = active_node.image
    image_name = getattr(image, "name", "") or getattr(image, "filepath", "")
    return _derive_legacy_texture_name(image_name)


def _collect_geo_export_issues(context):
    issues = []
    view_layer = getattr(context, "view_layer", None)
    active_obj = getattr(getattr(view_layer, "objects", None), "active", None)

    if active_obj is None:
        issues.append(
            _make_issue(
                "ERROR",
                "Scene",
                "",
                "No active object selected. GEO export requires an active mesh object.",
                {"GEO"},
                action="select_object",
            )
        )
        return issues

    if (
        getattr(active_obj, "type", None) != "MESH"
        or getattr(active_obj, "data", None) is None
    ):
        issues.append(
            _make_issue(
                "ERROR",
                "Object",
                active_obj.name,
                "Active object is not a mesh. GEO export only supports mesh objects.",
                {"GEO"},
                object_name=active_obj.name,
                action="select_object",
            )
        )
        return issues

    if len(getattr(active_obj.data, "vertices", [])) == 0:
        issues.append(
            _make_issue(
                "ERROR",
                "Object",
                active_obj.name,
                "Active mesh has no vertices and cannot be exported as GEO.",
                {"GEO"},
                object_name=active_obj.name,
                action="select_object",
            )
        )

    issues.extend(_collect_face_topology_issues(active_obj, {"GEO"}))
    issues.extend(_collect_material_issues(active_obj, {"GEO"}))
    return issues


def _collect_scene_export_issues(context, validation_preset="AUTO"):
    scene = getattr(context, "scene", None)
    if scene is None:
        return [
            _make_issue(
                "ERROR",
                "Scene",
                "",
                "No active scene available for validation.",
                {"VDF", "SDF"},
            )
        ]

    issues = [_orientation_reference_issue({"VDF", "SDF"})]
    named_candidates = []
    normalized_names = {}
    inner_helpers = []
    outer_helpers = []

    issues.extend(_collect_scene_armature_issues(scene.objects))

    for obj in scene.objects:
        lower_name = obj.name.lower()
        if lower_name in INNER_COLLISION_NAMES:
            inner_helpers.append(obj)
        if lower_name in OUTER_COLLISION_NAMES:
            outer_helpers.append(obj)

        name_info = parse_legacy_geo_name(obj.name)
        geo_props = getattr(obj, "GEOPropertyGroup", None)
        is_spinner_helper = bool(getattr(geo_props, "IsSpinnerHelper", False))
        spinner_like = is_spinner_helper or (
            int(getattr(geo_props, "GEOType", 0)) == 15
        )
        pov_like = _is_pov_helper_candidate(obj)

        if not name_info["valid"]:
            if _looks_like_export_candidate(obj, spinner_like):
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Object",
                        obj.name,
                        _invalid_name_message(name_info["reason"]),
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="fix_name",
                    )
                )
            continue

        lod = name_info["lod"]
        normalized_name = normalized_export_name(obj.name, lod)
        named_candidates.append(
            {
                "object": obj,
                "lod": lod,
                "normalized_name": normalized_name,
                "spinner_like": spinner_like,
            }
        )
        normalized_names.setdefault(normalized_name, []).append(obj)

        if not obj.name[0].isalpha():
            issues.append(
                _make_issue(
                    "INFO",
                    "Naming",
                    obj.name,
                    "Legacy guide best practice is for GEO names to start with a letter. Digit-prefixed names may work, but letter prefixes are safer for shared assets.",
                    {"VDF", "SDF"},
                    object_name=obj.name,
                    action="select_object",
                )
            )

        if getattr(obj, "type", None) != "MESH":
            if spinner_like:
                issues.append(
                    _make_issue(
                        "ERROR",
                        "Object",
                        obj.name,
                        "Spinner/helper empties are VDF-only helpers and will break SDF export if left in a structure scene.",
                        {"SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )
            elif pov_like:
                pass  # class-40 empties are legitimate invisible eyepoint records
            else:
                issues.append(
                    _make_issue(
                        "ERROR",
                        "Object",
                        obj.name,
                        "Object has a valid legacy export name but is not a mesh. Legacy VDF/SDF export expects mesh GEO data.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )
            continue

        if len(getattr(obj.data, "vertices", [])) == 0:
            issues.append(
                _make_issue(
                    "ERROR",
                    "Object",
                    obj.name,
                    "Mesh has a valid legacy export name but contains no vertices.",
                    {"VDF", "SDF"},
                    object_name=obj.name,
                    action="select_object",
                )
            )
            continue

        issues.extend(_collect_legacy_animation_limit_issues(obj))
        issues.extend(_collect_transform_issues(obj))
        issues.extend(_collect_face_topology_issues(obj, {"VDF", "SDF"}))
        issues.extend(_collect_geotype_suffix_issues(obj))
        issues.extend(_collect_material_issues(obj, {"VDF", "SDF"}))

    for normalized_name, objects in normalized_names.items():
        if len(objects) < 2:
            continue
        object_names = ", ".join(sorted(obj.name for obj in objects))
        issues.append(
            _make_issue(
                "ERROR",
                "Scene",
                normalized_name,
                f"Multiple objects normalize to the same export name: {object_names}.",
                {"VDF", "SDF"},
            )
        )

    candidate_name_set = set(normalized_names.keys())
    for entry in named_candidates:
        obj = entry["object"]
        parent = getattr(obj, "parent", None)
        if parent is None:
            continue

        parent_info = parse_legacy_geo_name(parent.name)
        if not parent_info["valid"]:
            issues.append(
                _make_issue(
                    "WARNING",
                    "Object",
                    obj.name,
                    f"Parent '{parent.name}' is not legacy-exportable. This object will export with parent WORLD.",
                    {"VDF", "SDF"},
                    object_name=obj.name,
                    action="select_object",
                )
            )
            continue

        parent_name = normalized_export_name(parent.name, entry["lod"])
        if parent_name not in candidate_name_set:
            issues.append(
                _make_issue(
                    "WARNING",
                    "Object",
                    obj.name,
                    f"Parent '{parent.name}' does not resolve to an exportable object in the same LOD set. This object will likely export with parent WORLD.",
                    {"VDF", "SDF"},
                    object_name=obj.name,
                    action="select_object",
                )
            )

    issues.extend(_collect_collision_helper_issues(inner_helpers, "inner"))
    issues.extend(_collect_collision_helper_issues(outer_helpers, "outer"))
    issues.extend(_collect_lod_counterpart_issues(named_candidates))
    issues.extend(_collect_multiple_root_issues(named_candidates))
    issues.extend(
        _collect_vdf_vehicle_required_issues(
            named_candidates, inner_helpers, outer_helpers, validation_preset
        )
    )
    issues.extend(
        _collect_animation_preset_issues(scene, named_candidates, validation_preset)
    )
    issues.extend(_collect_animation_guide_issues(scene, named_candidates))
    issues.extend(_collect_turret_cockpit_issues(named_candidates))
    issues.extend(_collect_advanced_semantics_issues(scene, named_candidates, export_mode))
    return issues


# ---------------------------------------------------------------------------
# Advanced semantic validation (part types / ObjectFlags / bounds / POV /
# decks / emitters / damage reps / VLOC)
# ---------------------------------------------------------------------------


def _candidate_geotype(entry):
    geo_props = getattr(entry["object"], "GEOPropertyGroup", None)
    return int(getattr(geo_props, "GEOType", 0))


def _is_pov_helper_candidate(obj):
    if getattr(obj, "type", None) == "MESH":
        return False
    geo_props = getattr(obj, "GEOPropertyGroup", None)
    return (
        bool(getattr(geo_props, "IsPOVHelper", False))
        or int(getattr(geo_props, "GEOType", 0)) == 40
    )


def _collect_object_flag_issues(entry):
    obj = entry["object"]
    from . import semantics as bz_semantics

    geo_props = getattr(obj, "GEOPropertyGroup", None)
    if geo_props is None:
        return []
    raw_flags = int(getattr(geo_props, "GEOFlags", 0)) & 0xFFFFFFFF
    issues = []

    unknown_bits = bz_semantics.unknown_flag_bits(raw_flags)
    if unknown_bits:
        issues.append(
            _make_issue(
                "WARNING",
                "Object Flags",
                obj.name,
                f"Unknown ObjectFlags bits {bz_semantics.format_hex(unknown_bits)} are present. They are preserved through edits and round trips, but their engine meaning is undocumented.",
                {"VDF", "SDF"},
                object_name=obj.name,
            )
        )

    decoded = bz_semantics.decode_object_flags(raw_flags)
    if decoded["destroyed"]:
        issues.append(
            _make_issue(
                "WARNING",
                "Object Flags",
                obj.name,
                "ObjectFlags bit 0x200 seeds the destroyed state: IsAlive() is false from spawn, targeting skips it and path blocking stops. Intentional for non-blocking decks; otherwise clear it.",
                {"VDF", "SDF"},
                object_name=obj.name,
            )
        )

    if decoded["keep_bounds"]:
        issues.append(
            _make_issue(
                "INFO",
                "Object Flags",
                obj.name,
                "Bit 0x1 keeps the authored bounds authoritative at load (and forces the collision nibble to non-collidable). Stock assets ship with this clear.",
                {"VDF", "SDF"},
                object_name=obj.name,
            )
        )

    collision_class = decoded["collision_class"]
    part_type = _candidate_geotype(entry)
    if part_type == 55 and collision_class not in (0x1000,):
        issues.append(
            _make_issue(
                "INFO",
                "Object Flags",
                obj.name,
                "NONCOLLIDABLE class (55) forces the collision nibble to 0x1000 at load regardless of the stored value.",
                {"VDF", "SDF"},
                object_name=obj.name,
            )
        )
    return issues


def _compute_mesh_bounds(obj):
    minx = miny = minz = maxx = maxy = maxz = None
    maxoverall = 0.0
    for vert in obj.data.vertices:
        x, y, z = vert.co.x, vert.co.y, vert.co.z
        minx = x if minx is None or x < minx else minx
        maxx = x if maxx is None or x > maxx else maxx
        miny = y if miny is None or y < miny else miny
        maxy = y if maxy is None or y > maxy else maxy
        minz = z if minz is None or z < minz else minz
        maxz = z if maxz is None or z > maxz else maxz
        for value in (x, y, z):
            if abs(value) > maxoverall:
                maxoverall = abs(value)
    cx = (minx + maxx) / 2 if minx is not None else 0.0
    cy = (miny + maxy) / 2 if miny is not None else 0.0
    cz = (minz + maxz) / 2 if minz is not None else 0.0
    return (cx, cy, cz, maxoverall)


def _collect_bounds_issues(entry):
    obj = entry["object"]
    from . import semantics as bz_semantics

    geo_props = getattr(obj, "GEOPropertyGroup", None)
    if geo_props is None or obj.type != "MESH" or obj.data is None:
        return []
    issues = []
    center = (
        float(geo_props.GeoCenterX),
        float(geo_props.GeoCenterY),
        float(geo_props.GeoCenterZ),
    )
    radius = float(geo_props.SphereRadius)
    half = (
        float(geo_props.BoxHalfHeightX),
        float(geo_props.BoxHalfHeightY),
        float(geo_props.BoxHalfHeightZ),
    )

    for severity, message in bz_semantics.evaluate_authored_bounds(center, radius, half):
        issues.append(
            _make_issue(
                severity,
                "Bounds",
                obj.name,
                message,
                {"VDF", "SDF"},
                object_name=obj.name,
            )
        )

    flags_raw = int(getattr(geo_props, "GEOFlags", 0)) & 0xFFFFFFFF
    part_type = _candidate_geotype(entry)
    has_authored = bool(getattr(geo_props, "HasAuthoredBounds", False))
    if has_authored and not bz_semantics.bounds_are_authoritative(flags_raw, part_type):
        issues.append(
            _make_issue(
                "INFO",
                "Bounds",
                obj.name,
                "The file carries explicit bounds but ObjectFlags bit 0x1 is clear (class is not 11/81 either): the engine recomputes them from LOD0 vertices, so these values only matter for round-trip fidelity.",
                {"VDF", "SDF"},
                object_name=obj.name,
            )
        )

    derived = _compute_mesh_bounds(obj)
    for severity, message in bz_semantics.compare_bounds_to_geometry(
        center, radius, derived[:3], derived[3]
    ):
        issues.append(
            _make_issue(
                severity,
                "Bounds",
                obj.name,
                message,
                {"VDF", "SDF"},
                object_name=obj.name,
            )
        )
    return issues


def _collect_eyepoint_issues(named_candidates):
    issues = []
    pov_entries = [e for e in named_candidates if _candidate_geotype(e) == 40]
    if len(pov_entries) > 1:
        names = ", ".join(sorted(e["object"].name for e in pov_entries))
        issues.append(
            _make_issue(
                "WARNING",
                "Eyepoint",
                names,
                f"{len(pov_entries)} class-40 eyepoint parts found. Craft code resolves a single POV node; extras are inert records.",
                {"VDF"},
                object_name=pov_entries[0]["object"].name,
            )
        )
    for entry in pov_entries:
        obj = entry["object"]
        scale = tuple(float(v) for v in getattr(obj, "scale", (1.0, 1.0, 1.0)))
        if any(abs(v - 1.0) > 0.001 for v in scale):
            issues.append(
                _make_issue(
                    "WARNING",
                    "Eyepoint",
                    obj.name,
                    f"Eyepoint object scale is ({scale[0]:.2f}, {scale[1]:.2f}, {scale[2]:.2f}). HoverCraft::UpdateEyepoint multiplies the POV basis rows by 25, so baked scale changes first-person framing and head-bob; apply/clear scale deliberately.",
                    {"VDF"},
                    object_name=obj.name,
                )
            )
        if entry["lod"] != 1:
            issues.append(
                _make_issue(
                    "WARNING",
                    "Eyepoint",
                    obj.name,
                    "Eyepoint parts normally live in the LOD1 set; a POV inside a cockpit/LOD3 slot may never be found by craft code.",
                    {"VDF"},
                    object_name=obj.name,
                )
            )
    return issues


def _collect_deck_face_components(obj):
    """Blender-space face normal Z components (engine up axis after remap)."""
    components = []
    mesh = obj.data
    transform = getattr(obj, "matrix_world", None)
    import mathutils

    identity = mathutils.Matrix.Identity(3)
    basis = identity
    if transform is not None:
        rotation = transform.to_3x3()
        basis = rotation
    for poly in mesh.polygons:
        normal = poly.normal @ basis
        components.append(normal.normalized().z)
    return components


def _collect_bridge_floor_issues(named_candidates):
    from . import semantics as bz_semantics

    issues = []
    root_bridges = []
    floor_parts = []
    for entry in named_candidates:
        part_type = _candidate_geotype(entry)
        obj = entry["object"]
        geo_props = getattr(obj, "GEOPropertyGroup", None)
        flags_raw = int(getattr(geo_props, "GEOFlags", 0)) & 0xFFFFFFFF
        if part_type == bz_semantics.BRIDGE_CLASS and obj.parent is None:
            root_bridges.append(entry)
        if part_type == bz_semantics.FLOOR_CLASS:
            floor_parts.append(entry)

        if part_type == bz_semantics.FLOOR_CLASS and obj.type == "MESH":
            total, drivable = bz_semantics.classify_deck_faces(
                _collect_deck_face_components(obj)
            )
            if total == 0:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Deck",
                        obj.name,
                        "FLOOR-classed part has no faces to analyze; the engine requires collision geometry before any deck faces are collected.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                    )
                )
            elif drivable == 0:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Deck",
                        obj.name,
                        "No face on this FLOOR part is within ~66 degrees of horizontal (world-up component > 0.4). The engine discards steep faces from hover decks, so this part will never be drivable even though it renders fine.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                    )
                )

        if flags_raw & bz_semantics.OBJFLAG_KEEP_BOUNDS and part_type in (
            bz_semantics.BRIDGE_CLASS,
            bz_semantics.FLOOR_CLASS,
        ):
            issues.append(
                _make_issue(
                    "WARNING",
                    "Deck",
                    obj.name,
                    "ObjectFlags bit 0x1 excludes this node from blocking accumulation AND strips its collision class to non-collidable, which removes its deck faces from the floor system. Not usable for drivable decks - decoration only. Also: never flag every part, or the empty accumulator yields a map-sized rect.",
                    {"VDF", "SDF"},
                    object_name=obj.name,
                )
            )

    for entry in root_bridges:
        obj = entry["object"]
        issues.append(
            _make_issue(
                "INFO",
                "Deck",
                obj.name,
                "BRIDGE-rooted entity: every part's upward collision polys become hover deck. Pathing never stamps BRIDGE footprints, so AI crossing depends on terrain tiles beneath the span being routable (geometric cliffs still block); an ODF whose class is 2/10 would block the full footprint instead.",
                {"VDF"},
                object_name=obj.name,
            )
        )

    if floor_parts and not root_bridges:
        names = ", ".join(sorted({e["object"].name for e in floor_parts}))
        issues.append(
            _make_issue(
                "INFO",
                "Deck",
                names,
                "FLOOR parts drive hover height only; they have zero pathing role. Visible floor geometry does not make cells traversable to AI route planning.",
                {"VDF", "SDF"},
            )
        )
    return issues


def _collect_emitter_overflow_issues(named_candidates):
    from . import semantics as bz_semantics

    smoke = sum(1 for e in named_candidates if _candidate_geotype(e) == 76)
    weapons = sum(1 for e in named_candidates if _candidate_geotype(e) == 70)
    issues = []
    if bz_semantics.emitter_overflow(smoke):
        issues.append(
            _make_issue(
                "ERROR",
                "Emitters",
                f"{smoke} smoke emitters",
                "Craft::FindSmokeSource appends every class-76 part into a fixed smokeList[8] with an unchecked count. More than 8 writes past the array into adjacent craft state: silent memory corruption or crashes.",
                {"VDF", "SDF"},
            )
        )
    if bz_semantics.emitter_overflow(weapons):
        issues.append(
            _make_issue(
                "WARNING",
                "Hardpoints",
                f"{weapons} weapon hardpoints",
                "Producer::FindSmokeSource collects every class-70 hardpoint into a fixed list of 8. More than 8 on a producing structure overflows it; plain vehicles without producer behavior are unaffected.",
                {"VDF", "SDF"},
            )
        )
    return issues


def _collect_damage_rep_issues(scene, named_candidates, export_mode):
    from . import semantics as bz_semantics

    issues = []
    export_names = {e["normalized_name"] for e in named_candidates}
    base_by_slot_name = {}
    for entry in named_candidates:
        if entry.get("lod") == 1:
            base_by_slot_name[entry["normalized_name"]] = entry["object"]

    any_variant = False
    for entry in named_candidates:
        obj = entry["object"]
        if entry.get("lod") != 1:
            continue
        geo_props = getattr(obj, "GEOPropertyGroup", None)
        if geo_props is None:
            continue
        filled_states = []
        for state in bz_semantics.AUTHORED_DAMAGE_STATES:
            variant = str(getattr(geo_props, f"DamageGeo{state}", "") or "").strip().lower()
            if not variant or variant == "null":
                continue
            any_variant = True
            filled_states.append(state)
            if variant == entry["normalized_name"]:
                issues.append(
                    _make_issue(
                        "INFO",
                        "Damage Reps",
                        obj.name,
                        f"Damage state {state} points back at the same geometry name; the swap would be a visual no-op.",
                        {"VDF"},
                        object_name=obj.name,
                    )
                )
                continue
            if export_mode == "SDF":
                issues.append(
                    _make_issue(
                        "ERROR",
                        "Damage Reps",
                        obj.name,
                        f"Damage variants are vehicles-only: AddStructReps registers each structure part under its d0 name for every band and never reads later blocks' name fields, so SDF exports cannot carry per-state meshes.",
                        {"SDF"},
                        object_name=obj.name,
                    )
                )
                continue
            if variant not in export_names:
                issues.append(
                    _make_issue(
                        "ERROR",
                        "Damage Reps",
                        obj.name,
                        f"Damage state {state} references '{variant}', which does not resolve to any exportable LOD1 part. A missing variant falls back to LOD 0 of the same state and then to invisibility.",
                        {"VDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )
        if filled_states and len(filled_states) < len(bz_semantics.AUTHORED_DAMAGE_STATES):
            missing = [
                s for s in bz_semantics.AUTHORED_DAMAGE_STATES if s not in filled_states
            ]
            issues.append(
                _make_issue(
                    "WARNING",
                    "Damage Reps",
                    obj.name,
                    f"Damage states {missing} have no mesh while {filled_states} do. The runtime fallback retries only LOD 0 of the current state, so an unfilled intermediate state can blank the part when a driver activates damage swapping.",
                    {"VDF"},
                    object_name=obj.name,
                )
            )

    preserved_store = getattr(scene, "bz_damage_band_records", None)
    if preserved_store is not None and len(preserved_store) > 0 and export_mode == "SDF":
        issues.append(
            _make_issue(
                "INFO",
                "Damage Reps",
                "preserved bands",
                "Imported VGEO variant-band content is preserved verbatim on SDF export even though structures cannot use damage states at runtime.",
                {"SDF"},
            )
        )
    if any_variant and export_mode in ("VDF", "ALL"):
        issues.append(
            _make_issue(
                "INFO",
                "Damage Reps",
                "damage states",
                "Authored damage states are inert in stock Battlezone 98 engines: nothing calls ObjTree_SelectRep. They become live the moment an external driver (e.g. BZR-OpenShim health hook) selects states.",
                {"VDF"},
            )
        )
    return issues


def _collect_vloc_issues(scene, export_mode):
    store = getattr(scene, "bz_vloc_chunks", None)
    if store is None or len(store) == 0:
        return []
    from . import semantics as bz_semantics

    issues = []
    if export_mode == "SDF":
        issues.append(
            _make_issue(
                "WARNING",
                "VLOC",
                "vehicle-load chunks",
                "VLOC injection runs in the vehicle loader ('vhclload' context); SDF/structure loads have no VLOC handler, so entries will not apply to structures.",
                {"SDF"},
            )
        )
    for index, entry in enumerate(store):
        kind = str(getattr(entry, "kind", "GENERIC"))
        if kind == "IDSIZES":
            continue
        class_id = int(getattr(entry, "class_id", 0))
        if kind == "GENERIC":
            notes = bz_semantics.vloc_runtime_notes(class_id)
            label = f"entry {index + 1} (class {class_id})"
        else:
            notes = bz_semantics.vloc_runtime_notes(
                bz_semantics.VLOC_POV if kind == "POV" else bz_semantics.VLOC_HEADLIGHT
            )
            label = f"entry {index + 1}"
        if not str(getattr(entry, "target_object", "") or "").strip() and bool(
            getattr(entry, "preserve_raw", True)
        ):
            issues.append(
                _make_issue(
                    "INFO",
                    "VLOC",
                    label,
                    "Payload preserved verbatim from import; bind a Blender object to edit its matrix deliberately.",
                    {"VDF"},
                )
            )
        for note in notes:
            issues.append(
                _make_issue(
                    "INFO",
                    "VLOC",
                    label,
                    note,
                    {"VDF"},
                )
            )
    return issues


def _collect_advanced_semantics_issues(scene, named_candidates, export_mode):
    issues = []
    for entry in named_candidates:
        issues.extend(_collect_object_flag_issues(entry))
        issues.extend(_collect_bounds_issues(entry))
    issues.extend(_collect_eyepoint_issues(named_candidates))
    issues.extend(_collect_bridge_floor_issues(named_candidates))
    issues.extend(_collect_emitter_overflow_issues(named_candidates))
    issues.extend(_collect_damage_rep_issues(scene, named_candidates, export_mode))
    issues.extend(_collect_vloc_issues(scene, export_mode))
    return issues


def _iter_action_fcurves(action):
    fcurves = getattr(action, "fcurves", None)
    if fcurves is not None:
        yield from fcurves
        return

    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for channelbag in getattr(strip, "channelbags", []):
                yield from getattr(channelbag, "fcurves", [])


def _has_action_fcurves(animation_data):
    action = (
        getattr(animation_data, "action", None) if animation_data is not None else None
    )
    if action is None:
        return False
    return any(True for _curve in _iter_action_fcurves(action))


def _has_armature_modifier(obj):
    for modifier in getattr(obj, "modifiers", []):
        if getattr(modifier, "type", None) == "ARMATURE":
            return True
    return False


def _has_weighted_vertex_groups(obj):
    vertex_groups = getattr(obj, "vertex_groups", None)
    if not vertex_groups:
        return False

    mesh = getattr(obj, "data", None)
    for vertex in getattr(mesh, "vertices", []):
        for group in getattr(vertex, "groups", []):
            try:
                if float(group.weight) != 0.0:
                    return True
            except Exception:
                continue
    return False


def _collect_scene_armature_issues(objects):
    issues = []
    for obj in objects:
        if getattr(obj, "type", None) != "ARMATURE":
            continue

        message = (
            "Armature objects are ignored by legacy VDF/SDF animation export. "
            "Only separate GEO object rotation and location keys are written; use Redux mesh/skeleton export for skeletal deformation."
        )
        if _has_action_fcurves(getattr(obj, "animation_data", None)):
            message = (
                "Armature animation will not export to legacy VDF/SDF. "
                "Legacy ANIM only writes separate GEO object rotation and location keys; use Redux mesh/skeleton export for bone animation."
            )

        issues.append(
            _make_issue(
                "WARNING",
                "Armature",
                obj.name,
                message,
                {"VDF", "SDF"},
                object_name=obj.name,
                action="select_object",
            )
        )
    return issues


def _collect_legacy_animation_limit_issues(obj):
    issues = []
    has_armature_modifier = _has_armature_modifier(obj)
    parent = getattr(obj, "parent", None)
    parent_is_armature = getattr(parent, "type", None) == "ARMATURE"
    has_weighted_groups = _has_weighted_vertex_groups(obj)

    if (
        has_armature_modifier
        or parent_is_armature
        or (has_weighted_groups and has_armature_modifier)
    ):
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                obj.name,
                (
                    "Skinned mesh deformation will not export to legacy VDF/SDF. "
                    "Each moving section must be a separate GEO object with its own rotation/location keys; use Redux mesh/skeleton export for weighted bones."
                ),
                {"VDF", "SDF"},
                object_name=obj.name,
                action="select_object",
            )
        )

    mesh = getattr(obj, "data", None)
    shape_keys = getattr(mesh, "shape_keys", None)
    key_blocks = getattr(shape_keys, "key_blocks", None)
    if shape_keys is not None and key_blocks is not None and len(key_blocks) > 1:
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                obj.name,
                (
                    "Shape keys are not exported by legacy VDF/SDF. "
                    "Legacy ANIM only supports object rotation and location keys."
                ),
                {"VDF", "SDF"},
                object_name=obj.name,
                action="select_object",
            )
        )

    if _has_action_fcurves(getattr(shape_keys, "animation_data", None)):
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                obj.name,
                (
                    "Shape key animation will be ignored by legacy VDF/SDF export. "
                    "Use separate rigid GEO pieces or Redux skeletal/mesh animation instead."
                ),
                {"VDF", "SDF"},
                object_name=obj.name,
                action="select_object",
            )
        )

    unsupported_paths = []
    anim = getattr(obj, "animation_data", None)
    action = getattr(anim, "action", None) if anim is not None else None
    supported_paths = {"location", "rotation_euler", "rotation_quaternion", "scale"}
    if action is not None:
        for curve in _iter_action_fcurves(action):
            data_path = getattr(curve, "data_path", "")
            if data_path and data_path not in supported_paths:
                unsupported_paths.append(data_path)

    if unsupported_paths:
        unique_paths = sorted(set(unsupported_paths))
        shown_paths = ", ".join(unique_paths[:4])
        if len(unique_paths) > 4:
            shown_paths += ", ..."
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                obj.name,
                (
                    f"Unsupported animation channels will be ignored by legacy VDF/SDF export: {shown_paths}. "
                    "Only object location, Euler rotation, and quaternion rotation curves are written."
                ),
                {"VDF", "SDF"},
                object_name=obj.name,
                action="select_object",
            )
        )

    constraints = getattr(obj, "constraints", None)
    if constraints is not None and len(constraints) > 0:
        issues.append(
            _make_issue(
                "INFO",
                "Animation",
                obj.name,
                (
                    "Object constraints are not baked automatically for legacy VDF/SDF export. "
                    "Bake the final object rotation/location keys before exporting if constraints drive motion."
                ),
                {"VDF", "SDF"},
                object_name=obj.name,
                action="select_object",
            )
        )

    return issues


def _collect_transform_issues(obj):
    issues = []
    if getattr(obj, "type", None) != "MESH":
        return issues

    scale = getattr(obj, "scale", None)
    if scale is not None and any(abs(float(value) - 1.0) > 0.0001 for value in scale):
        issues.append(
            _make_issue(
                "WARNING",
                "Transform",
                obj.name,
                "Object has non-applied scale. Apply scale before final legacy export if this mesh should define raw GEO shape, collision, or Ogre normals.",
                {"VDF", "SDF", "GEO"},
                object_name=obj.name,
                action="select_object",
            )
        )

    rotation_mode = getattr(obj, "rotation_mode", "XYZ")
    has_rotation = False
    if rotation_mode == "QUATERNION":
        quat = getattr(obj, "rotation_quaternion", None)
        if quat is not None:
            has_rotation = (
                abs(float(quat.w) - 1.0) > 0.0001
                or abs(float(quat.x)) > 0.0001
                or abs(float(quat.y)) > 0.0001
                or abs(float(quat.z)) > 0.0001
            )
    else:
        euler = getattr(obj, "rotation_euler", None)
        if euler is not None:
            has_rotation = any(abs(float(value)) > 0.0001 for value in euler)

    if has_rotation:
        issues.append(
            _make_issue(
                "INFO",
                "Transform",
                obj.name,
                "Object has non-applied rotation. This can be intentional for animated or parented GEOs; apply rotation if the mesh itself should be exported in that orientation.",
                {"VDF", "SDF", "GEO"},
                object_name=obj.name,
                action="select_object",
            )
        )

    return issues


def _collect_geotype_suffix_issues(obj):
    issues = []
    geo_props = getattr(obj, "GEOPropertyGroup", None)
    if geo_props is None:
        return issues

    try:
        geo_type = int(getattr(geo_props, "GEOType", 0))
    except Exception:
        return issues

    suffix = legacy_geo_suffix(obj.name)
    if geo_type == 65:
        pitch_ok = _is_numbered_suffix(suffix, "tx", max_index=9)
        yaw_ok = _is_numbered_suffix(suffix, "ty", max_index=9)
        if not pitch_ok and not yaw_ok:
            issues.append(
                _make_issue(
                    "WARNING",
                    "GEO Type",
                    obj.name,
                    (
                        "Type 65 turret rotators should use a tx# pitch suffix or ty# yaw suffix "
                        "(for example tx1 or ty1). Battlezone may not apply turret rotation to this GEO."
                    ),
                    {"VDF"},
                    object_name=obj.name,
                    action="select_object",
                )
            )
        return issues

    hardpoint_rule = HARDPOINT_SUFFIX_RULES.get(geo_type)
    if hardpoint_rule is None:
        return issues

    role, prefix = hardpoint_rule
    if not _is_numbered_suffix(suffix, prefix, max_index=5):
        issues.append(
            _make_issue(
                "WARNING",
                "GEO Type",
                obj.name,
                (
                    f"Type {geo_type} {role.lower()} GEOs should use suffix {prefix}1-{prefix}5. "
                    "Battlezone uses that suffix to bind the hardpoint slot."
                ),
                {"VDF"},
                object_name=obj.name,
                action="select_object",
            )
        )

    return issues


def _collect_face_topology_issues(obj, export_modes):
    issues = []
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return issues

    large_faces = [
        poly.index
        for poly in getattr(mesh, "polygons", [])
        if len(getattr(poly, "vertices", [])) > 10
    ]
    if large_faces:
        sample = ", ".join(str(index) for index in large_faces[:5])
        if len(large_faces) > 5:
            sample += ", ..."
        issues.append(
            _make_issue(
                "WARNING",
                "Geometry",
                obj.name,
                (
                    f"{len(large_faces)} face(s) have more than 10 vertices ({sample}). "
                    "Legacy GEO tools reserve 10 vertex slots per face; split large n-gons before export."
                ),
                export_modes,
                object_name=obj.name,
                action="select_object",
            )
        )

    return issues


def _geo_type(obj):
    geo_props = getattr(obj, "GEOPropertyGroup", None)
    if geo_props is None:
        return 0
    try:
        return int(getattr(geo_props, "GEOType", 0))
    except Exception:
        return 0


def _lod_counterpart_key(obj):
    return legacy_geo_base_prefix(obj.name) + legacy_geo_suffix(obj.name)


def _object_vertex_count(obj):
    mesh = getattr(obj, "data", None)
    return len(getattr(mesh, "vertices", []) or [])


def _object_has_assigned_material(obj):
    mesh = getattr(obj, "data", None)
    materials = getattr(mesh, "materials", None)
    if not materials:
        return False
    return any(material is not None for material in materials)


def _world_location_distance(a, b):
    try:
        return (
            a.matrix_world.to_translation() - b.matrix_world.to_translation()
        ).length
    except Exception:
        return 0.0


def _world_rotation_delta(a, b):
    try:
        return (
            a.matrix_world.to_quaternion()
            .rotation_difference(b.matrix_world.to_quaternion())
            .angle
        )
    except Exception:
        return 0.0


def _collect_lod_counterpart_issues(named_candidates):
    issues = []
    lod_by_key = {}
    for entry in named_candidates:
        obj = entry["object"]
        lod_by_key.setdefault(_lod_counterpart_key(obj), {})[entry["lod"]] = obj

    for entry in named_candidates:
        obj = entry["object"]
        lod = entry["lod"]
        counterpart = lod_by_key.get(_lod_counterpart_key(obj), {}).get(1)

        if lod == 2:
            if counterpart is None:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "LOD",
                        obj.name,
                        "LOD2 cockpit GEO has no matching LOD1 counterpart. Cockpit GEOs should mirror the LOD1 part name, parent relationship, and pivot/origin.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )
                continue

            if getattr(obj, "parent", None) is not counterpart:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "LOD",
                        obj.name,
                        f"LOD2 cockpit GEO should be parented directly to matching LOD1 GEO '{counterpart.name}' so first-person parts follow the same hierarchy.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )

            if _world_location_distance(obj, counterpart) > 0.001:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Pivot",
                        obj.name,
                        f"LOD2 cockpit GEO origin does not match matching LOD1 GEO '{counterpart.name}'. The guide expects identical pivots for matching cockpit parts.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )

            if _world_rotation_delta(obj, counterpart) > 0.01:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Pivot",
                        obj.name,
                        f"LOD2 cockpit GEO orientation does not match matching LOD1 GEO '{counterpart.name}'. Matching local axes are important for rotators, POV, and emitters.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )

        elif lod == 3:
            if counterpart is None:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "LOD",
                        obj.name,
                        "LOD3 GEO has no matching LOD1 counterpart. Redux does not need LOD3, but legacy fallback LODs should match a primary part.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )
            else:
                lod3_vertices = _object_vertex_count(obj)
                lod1_vertices = _object_vertex_count(counterpart)
                if lod1_vertices > 0 and lod3_vertices >= lod1_vertices:
                    issues.append(
                        _make_issue(
                            "WARNING",
                            "LOD",
                            obj.name,
                            f"LOD3 GEO has {lod3_vertices} vertices versus {lod1_vertices} on LOD1 '{counterpart.name}'. LOD3 should be a lower-poly legacy fallback and is unneeded for Redux.",
                            {"VDF", "SDF"},
                            object_name=obj.name,
                            action="select_object",
                        )
                    )

            if _object_has_assigned_material(obj):
                issues.append(
                    _make_issue(
                        "WARNING",
                        "LOD",
                        obj.name,
                        "LOD3 GEO has assigned materials/textures. Legacy LOD3 parts are normally low-detail/untextured; Redux does not require them.",
                        {"VDF", "SDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )

    return issues


def _is_top_level_model_root(entry):
    obj = entry["object"]
    if entry["lod"] != 1 or entry.get("spinner_like"):
        return False
    if getattr(obj, "type", None) != "MESH":
        return False
    geo_type = _geo_type(obj)
    if geo_type == 40 or geo_type in HARDPOINT_SUFFIX_RULES:
        return False
    parent = getattr(obj, "parent", None)
    if parent is None:
        return True
    return not parse_legacy_geo_name(getattr(parent, "name", "")).get("valid")


def _collect_multiple_root_issues(named_candidates):
    roots = [
        entry["object"] for entry in named_candidates if _is_top_level_model_root(entry)
    ]
    if len(roots) < 2:
        return []

    shown = ", ".join(sorted(obj.name for obj in roots[:6]))
    if len(roots) > 6:
        shown += ", ..."
    return [
        _make_issue(
            "WARNING",
            "Scene",
            "model roots",
            (
                f"Multiple top-level LOD1 model roots are exportable ({shown}). "
                "The legacy guide warns that unrelated linked/exported models in one scene can corrupt output; keep one model hierarchy per export scene."
            ),
            {"VDF", "SDF"},
        )
    ]


def _vdf_collision_helpers_required(named_candidates, validation_preset):
    preset = (validation_preset or "AUTO").upper()
    if preset == "VEHICLE":
        return True

    candidates = [entry["object"] for entry in named_candidates]
    geo_types = {_geo_type(obj) for obj in candidates}
    if 61 in geo_types:
        return False
    return 40 in geo_types or any(
        geo_type in HARDPOINT_SUFFIX_RULES for geo_type in geo_types
    )


def _collect_vdf_vehicle_required_issues(
    named_candidates, inner_helpers, outer_helpers, validation_preset
):
    issues = []
    candidates = [entry["object"] for entry in named_candidates]
    has_exportable_geo = any(getattr(obj, "type", None) == "MESH" for obj in candidates)
    if not has_exportable_geo:
        return issues

    pov_objects = [obj for obj in candidates if _geo_type(obj) == 40]
    if not pov_objects:
        issues.append(
            _make_issue(
                "ERROR",
                "Scene",
                "POV",
                "VDF vehicle export has no exportable Type 40 POV GEO. Battlezone can crash when a vehicle has no POV/eyepoint.",
                {"VDF"},
            )
        )
    elif len(pov_objects) > 1:
        pov_names = ", ".join(obj.name for obj in pov_objects)
        issues.append(
            _make_issue(
                "ERROR",
                "Scene",
                "POV",
                f"Multiple exportable Type 40 POV GEOs detected: {pov_names}. Only one POV can be active; remove or rename duplicates.",
                {"VDF"},
            )
        )

    if not _vdf_collision_helpers_required(named_candidates, validation_preset):
        return issues

    if not inner_helpers:
        issues.append(
            _make_issue(
                "WARNING",
                "Scene",
                "inner_col",
                "VDF vehicle export has no inner_col collision mesh. Generate COL boxes before final vehicle export.",
                {"VDF"},
            )
        )

    if not outer_helpers:
        issues.append(
            _make_issue(
                "WARNING",
                "Scene",
                "outer_col",
                "VDF vehicle export has no outer_col collision mesh. Generate COL boxes before final vehicle export.",
                {"VDF"},
            )
        )

    return issues


def _scene_animation_indices(scene):
    indices = set()
    for item in getattr(scene, "AnimationCollection", []) or []:
        try:
            indices.add(int(getattr(item, "Index", -1)))
        except Exception:
            continue
    return indices


def _has_object_animation(obj):
    if _has_action_fcurves(getattr(obj, "animation_data", None)):
        return True

    mesh = getattr(obj, "data", None)
    shape_keys = getattr(mesh, "shape_keys", None)
    if _has_action_fcurves(getattr(shape_keys, "animation_data", None)):
        return True

    return False


def _iter_object_transform_fcurves(obj):
    animation_data = getattr(obj, "animation_data", None)
    action = (
        getattr(animation_data, "action", None) if animation_data is not None else None
    )
    if action is None:
        return

    transform_paths = {
        "location",
        "rotation_euler",
        "rotation_quaternion",
        "rotation_axis_angle",
        "scale",
    }
    for fcurve in _iter_action_fcurves(action):
        if getattr(fcurve, "data_path", "") in transform_paths:
            yield fcurve


def _fcurve_has_key_in_range(fcurve, start_frame, end_frame):
    low = min(start_frame, end_frame)
    high = max(start_frame, end_frame)
    for keyframe in getattr(fcurve, "keyframe_points", []) or []:
        try:
            frame = float(keyframe.co[0])
        except Exception:
            continue
        if low <= frame <= high:
            return True
    return False


def _object_loop_transform_mismatch(obj, start_frame, end_frame, tolerance=0.0001):
    for fcurve in _iter_object_transform_fcurves(obj):
        if not _fcurve_has_key_in_range(fcurve, start_frame, end_frame):
            continue
        try:
            start_value = float(fcurve.evaluate(start_frame))
            end_value = float(fcurve.evaluate(end_frame))
        except Exception:
            continue
        if abs(start_value - end_value) > tolerance:
            return True
    return False


def _animation_item_int(item, attr, default=0):
    try:
        return int(getattr(item, attr, default))
    except Exception:
        return default


def _collect_animation_guide_issues(scene, named_candidates):
    issues = []
    animated_objects = [
        entry["object"]
        for entry in named_candidates
        if _has_object_animation(entry["object"])
    ]
    if len(animated_objects) > 25:
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                "animated GEO count",
                (
                    f"{len(animated_objects)} exportable GEOs have animation. The legacy guide warns that roughly more than 25 animated GEOs can glitch; "
                    "larger Redux-era setups may work, but test in the target game."
                ),
                {"VDF", "SDF"},
            )
        )

    for item in getattr(scene, "AnimationCollection", []) or []:
        index = _animation_item_int(item, "Index", 0)
        start = _animation_item_int(item, "Start", 0)
        length = _animation_item_int(item, "Length", 0)
        loop_count = _animation_item_int(item, "Loop", 0)
        if length == 0:
            continue

        abs_length = abs(length)
        if abs_length < 10 or abs_length > 20:
            issues.append(
                _make_issue(
                    "INFO",
                    "Animation",
                    f"slot {index}",
                    (
                        f"ANIM slot {index} is {abs_length} frame(s). The legacy guide prefers 10-20 frame clips; "
                        "longer clips can work, but verify in legacy BZ if supporting it."
                    ),
                    {"VDF", "SDF"},
                )
            )

        if loop_count <= 0:
            continue

        end = start + length
        mismatched = [
            obj.name
            for obj in animated_objects
            if _object_loop_transform_mismatch(obj, start, end)
        ]
        if not mismatched:
            continue

        shown = ", ".join(sorted(mismatched[:4]))
        if len(mismatched) > 4:
            shown += ", ..."
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                f"slot {index}",
                (
                    f"Looping ANIM slot {index} starts and ends on different keyed transforms for {shown}. "
                    "Legacy looping animations should return to the same pose at the first and last frame."
                ),
                {"VDF", "SDF"},
            )
        )

    return issues


def _collect_animation_preset_issues(scene, named_candidates, validation_preset):
    issues = []
    preset = (validation_preset or "AUTO").upper()
    indices = _scene_animation_indices(scene)

    if preset in ANIMATION_VALIDATION_PRESETS:
        rule = ANIMATION_VALIDATION_PRESETS[preset]
        missing = sorted(rule["required"] - indices)
        if missing:
            shown = ", ".join(str(index) for index in missing)
            issues.append(
                _make_issue(
                    "WARNING",
                    "Animation",
                    rule["label"],
                    f"{rule['message']} Missing slot(s): {shown}.",
                    {"VDF", "SDF"},
                )
            )
        return issues

    if preset == "ANIMATED" and not indices:
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                "animated object",
                "Animated-object validation expects at least one Scene AnimationCollection entry.",
                {"VDF", "SDF"},
            )
        )
        return issues

    animated_objects = [
        entry["object"]
        for entry in named_candidates
        if _has_object_animation(entry["object"])
    ]
    if animated_objects and not indices:
        object_names = ", ".join(sorted(obj.name for obj in animated_objects[:4]))
        if len(animated_objects) > 4:
            object_names += ", ..."
        issues.append(
            _make_issue(
                "WARNING",
                "Animation",
                "scene",
                (
                    f"Exportable object animation exists ({object_names}) but no ANIM sequence entries are defined. "
                    "Add animation elements before exporting animated VDF/SDF assets."
                ),
                {"VDF", "SDF"},
            )
        )

    return issues


def _is_turret_yaw_name(name):
    return _is_numbered_suffix(legacy_geo_suffix(name), "ty", max_index=9)


def _is_turret_pitch_name(name):
    return _is_numbered_suffix(legacy_geo_suffix(name), "tx", max_index=9)


def _iter_ancestors(obj):
    seen = set()
    parent = getattr(obj, "parent", None)
    while parent is not None and id(parent) not in seen:
        seen.add(id(parent))
        yield parent
        parent = getattr(parent, "parent", None)


def _has_ancestor_matching(obj, predicate):
    return any(predicate(parent) for parent in _iter_ancestors(obj))


def _collect_turret_cockpit_issues(named_candidates):
    issues = []
    candidates = [entry["object"] for entry in named_candidates]
    if not candidates:
        return issues

    has_turret_rotator = any(_geo_type(obj) == 65 for obj in candidates)
    if not has_turret_rotator:
        return issues

    has_cockpit_lod = any(entry.get("lod") == 2 for entry in named_candidates)
    has_pov = any(_geo_type(obj) == 40 for obj in candidates)

    if has_cockpit_lod and has_pov:
        issues.append(
            _make_issue(
                "INFO",
                "Scene",
                "Turret cockpit",
                (
                    "Turret cockpit detected. Redux output should use separate cockpit files so cockpit-only "
                    "geometry can follow duplicate ty#/tx# cockpit bones instead of the camera bone."
                ),
                {"VDF"},
            )
        )

    for obj in candidates:
        geo_type = _geo_type(obj)
        if geo_type == 40:
            pitch_parent = next(
                (
                    parent
                    for parent in _iter_ancestors(obj)
                    if _geo_type(parent) == 65 and _is_turret_pitch_name(parent.name)
                ),
                None,
            )
            if pitch_parent is not None:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Object",
                        obj.name,
                        (
                            f"POV is parented under pitch rotator '{pitch_parent.name}'. Redux turret cockpits "
                            "are more reliable when POV follows yaw only; keep guns/hardpoints under tx#."
                        ),
                        {"VDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )

            yaw_parent = _has_ancestor_matching(
                obj,
                lambda parent: _geo_type(parent) == 65
                and _is_turret_yaw_name(parent.name),
            )
            if not yaw_parent:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Object",
                        obj.name,
                        "Turret POV should usually be parented under a ty# yaw rotator so the cockpit view follows left/right aiming.",
                        {"VDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )

        if geo_type in HARDPOINT_SUFFIX_RULES:
            pitch_parent = _has_ancestor_matching(
                obj,
                lambda parent: _geo_type(parent) == 65
                and _is_turret_pitch_name(parent.name),
            )
            if not pitch_parent:
                issues.append(
                    _make_issue(
                        "WARNING",
                        "Object",
                        obj.name,
                        (
                            "Turret weapon/hardpoint GEOs should normally be parented under a tx# pitch rotator. "
                            "Otherwise guns may not follow up/down aiming in Redux."
                        ),
                        {"VDF"},
                        object_name=obj.name,
                        action="select_object",
                    )
                )

    cockpit_rotators = [
        obj
        for entry in named_candidates
        for obj in [entry["object"]]
        if entry.get("lod") == 2 and _geo_type(obj) == 65
    ]
    primary_rotator_prefixes = {
        legacy_geo_base_prefix(obj.name) + legacy_geo_suffix(obj.name)
        for entry in named_candidates
        for obj in [entry["object"]]
        if entry.get("lod") == 1 and _geo_type(obj) == 65
    }
    for obj in cockpit_rotators:
        counterpart_key = legacy_geo_base_prefix(obj.name) + legacy_geo_suffix(obj.name)
        if counterpart_key not in primary_rotator_prefixes:
            issues.append(
                _make_issue(
                    "WARNING",
                    "Object",
                    obj.name,
                    (
                        "Cockpit turret rotator has no matching primary ty#/tx# rotator. Separate Redux cockpit "
                        "files work best when cockpit LOD rotators mirror the primary turret rotators."
                    ),
                    {"VDF"},
                    object_name=obj.name,
                    action="select_object",
                )
            )

    return issues


def _collect_collision_helper_issues(helpers, label):
    issues = []
    if not helpers:
        return issues

    if len(helpers) > 1:
        helper_names = ", ".join(sorted(obj.name for obj in helpers))
        issues.append(
            _make_issue(
                "WARNING",
                "Scene",
                label,
                f"Multiple {label} collision helpers are present ({helper_names}). VDF export will use whichever object is encountered last.",
                {"VDF"},
            )
        )

    for helper in helpers:
        if (
            getattr(helper, "type", None) != "MESH"
            or getattr(helper, "data", None) is None
        ):
            issues.append(
                _make_issue(
                    "ERROR",
                    "Object",
                    helper.name,
                    "Collision helper is not a mesh object. VDF collision helpers must be meshes.",
                    {"VDF"},
                    object_name=helper.name,
                    action="select_object",
                )
            )
            continue

        if len(getattr(helper.data, "vertices", [])) == 0:
            issues.append(
                _make_issue(
                    "ERROR",
                    "Object",
                    helper.name,
                    "Collision helper mesh contains no vertices.",
                    {"VDF"},
                    object_name=helper.name,
                    action="select_object",
                )
            )

    return issues


def _collect_material_issues(obj, export_modes):
    issues = []
    mesh = getattr(obj, "data", None)
    materials = getattr(mesh, "materials", None)
    if materials is None:
        return issues

    for index, material in enumerate(materials):
        slot_label = f"{obj.name}:slot{index}"
        if material is None:
            issues.append(
                _make_issue(
                    "WARNING",
                    "Material",
                    slot_label,
                    "Material slot is empty. Export will fall back to blank texture naming for faces using this slot.",
                    export_modes,
                )
            )
            continue

        material_props = getattr(material, "MaterialPropertyGroup", None)
        raw_texture = ""
        if material_props is not None:
            raw_texture = (getattr(material_props, "MapTexture", "") or "").strip()
        image_texture = _get_image_derived_texture_name(material)

        if raw_texture and len(raw_texture) > 8:
            issues.append(
                _make_issue(
                    "WARNING",
                    "Material",
                    material.name,
                    f"Legacy texture name '{raw_texture}' exceeds 8 characters and may not round-trip cleanly.",
                    export_modes,
                    object_name=obj.name,
                    action="select_object",
                )
            )
            continue

        if raw_texture:
            continue

        if image_texture:
            issues.append(
                _make_issue(
                    "INFO",
                    "Material",
                    material.name,
                    f"Texture name is blank. Export will derive '{image_texture}' from the linked image texture.",
                    export_modes,
                    object_name=obj.name,
                    action="select_object",
                )
            )
            continue

        derived_name = _derive_legacy_texture_name(material.name)
        if not derived_name:
            issues.append(
                _make_issue(
                    "WARNING",
                    "Material",
                    slot_label,
                    "Material has no Battlezone texture name and no usable Blender material name to derive one from.",
                    export_modes,
                    object_name=obj.name,
                    action="select_object",
                )
            )
        elif len((material.name or "").strip().replace(" ", "_")) > 8:
            issues.append(
                _make_issue(
                    "INFO",
                    "Material",
                    material.name,
                    f"Texture name is blank. Export will auto-derive and truncate this material name to '{derived_name}'.",
                    export_modes,
                    object_name=obj.name,
                    action="select_object",
                )
            )

    return issues


def _looks_like_export_candidate(obj, spinner_like):
    if is_collision_helper_name(obj.name):
        return False
    if getattr(obj, "type", None) in {"MESH", "EMPTY"}:
        return True
    if spinner_like:
        return True
    return getattr(obj, "parent", None) is not None


def _invalid_name_message(reason):
    if reason == "too_short":
        return "Object name is shorter than 5 characters and will be skipped by legacy VDF/SDF export."
    if reason == "too_long":
        return "Object name exceeds 8 characters and will be skipped by legacy VDF/SDF export."
    if reason == "invalid_lod":
        return "Object name does not have a valid LOD marker at character 4 and will be skipped by legacy VDF/SDF export."
    return "Object name is not valid for legacy VDF/SDF export."
