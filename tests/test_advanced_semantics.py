# Battlezone 98R Blender ToolKit
# Copyright (C) 2024–2025 "GrizzlyOne95" and contributors
#
# This file is part of BZ98R Blender ToolKit, which is distributed
# under the terms of the GNU General Public License v3.0.
# See the LICENSE file or <https://www.gnu.org/licenses/>.

"""Unit tests for the pure semantic layer (no Blender required).

Run:
    python tests/run_tests.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)

import _bootstrap

_bootstrap.ensure_package()

from bz98tools import semantics, vdf_classes, vdf_file  # noqa: E402

FIXTURE_DIR = os.path.join(_HERE, "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "rb") as handle:
        return handle.read()


def parse_record(raw):
    geo = vdf_classes.GEOData()
    geo.Read(raw, 0)
    return geo


class PartTypeTests(unittest.TestCase):
    def test_known_types_have_metadata(self):
        for value in (0, 8, 9, 15, 40, 66, 67, 68, 75, 76, 77, 81):
            self.assertTrue(semantics.is_known_part_type(value), value)

    def test_unknown_type_label_preserves_number(self):
        label = semantics.part_type_label(90)
        self.assertIn("90", label)
        self.assertIn("Unknown", label)
        self.assertIn("0x005a", label.lower())

    def test_phantom_types_flagged(self):
        self.assertIn("NOT AN ENGINE CLASS", semantics.part_type_label(33))
        self.assertIn("NOT AN ENGINE CLASS", semantics.part_type_label(34))

    def test_confidence_labels(self):
        self.assertEqual(semantics.part_type_confidence(66), semantics.CONFIRMED)
        self.assertEqual(semantics.part_type_confidence(64), semantics.INFERRED)


class ObjectFlagsCodecTests(unittest.TestCase):
    def test_task_example_unknown_bits_survive_known_edit(self):
        raw = 0x00008124  # unknown 0x8100 plus known bit 0x20-ish pattern
        raw |= semantics.OBJFLAG_KEEP_BOUNDS | (0x3000 << 12)
        modified = semantics.apply_flag_bit(
            raw, semantics.OBJFLAG_KEEP_BOUNDS, False
        )
        self.assertEqual(modified & 0x8100, 0x8100)
        self.assertEqual(modified & semantics.OBJFLAG_KEEP_BOUNDS, 0)

    def test_decode_round_trip(self):
        raw = semantics.encode_object_flags(
            keep_bounds=True,
            destroyed=True,
            collision_class=0x2000,
            team=7,
            unknown=0x7E000000,
        )
        decoded = semantics.decode_object_flags(raw)
        self.assertTrue(decoded["keep_bounds"])
        self.assertTrue(decoded["destroyed"])
        self.assertEqual(decoded["collision_class"], 0x2000)
        self.assertEqual(decoded["team"], 7)
        self.assertEqual(decoded["unknown"], 0x7E000000)

    def test_encode_ignores_unknown_overlap(self):
        raw = semantics.encode_object_flags(unknown=0xFFFFFFFF)
        self.assertEqual(
            raw, semantics.unknown_flag_bits(0xFFFFFFFF) & 0xFFFFFFFF
        )
        self.assertEqual(
            raw & semantics.KNOWN_OBJFLAG_MASK, 0
        )


class BoundsSemanticsTests(unittest.TestCase):
    def test_authoritative_requires_flag_or_special_class(self):
        self.assertTrue(semantics.bounds_are_authoritative(0x1, 60))
        self.assertTrue(semantics.bounds_are_authoritative(0, 11))
        self.assertTrue(semantics.bounds_are_authoritative(0, 81))
        self.assertFalse(semantics.bounds_are_authoritative(0, 60))

    def test_negative_half_extent_is_error(self):
        issues = semantics.evaluate_authored_bounds((0, 0, 0), 1.0, (2.0, -1.0, 2.0))
        self.assertTrue(any(s == "ERROR" for s, _ in issues))

    def test_zero_radius_warns(self):
        issues = semantics.evaluate_authored_bounds((0, 0, 0), 0.0, (1, 1, 1))
        self.assertTrue(any(s == "WARNING" and "zero" in m for s, m in issues))

    def test_compare_flags_oversized_radius(self):
        results = semantics.compare_bounds_to_geometry(
            (0, 0, 0), 50.0, (0, 0, 0), 2.0
        )
        self.assertTrue(any(s == "WARNING" and "broadphase" in m for s, m in results))


class VLOCTests(unittest.TestCase):
    def test_headlight_round_trip(self):
        payload = (
            __import__("struct").pack("<I", 38)
            + __import__("struct").pack("<12f", *([1.0] * 12))
        )
        chunk = semantics.parse_vloc_payload(payload)
        self.assertEqual(chunk.kind_key, "HEADLIGHT")
        rebuilt = semantics.build_vloc_payload(chunk)
        self.assertEqual(rebuilt, payload)

    def test_generic_class_from_first_dword(self):
        import struct

        payload = struct.pack("<I", 76) + struct.pack("<12f", *([0.5] * 12))
        chunk = semantics.parse_vloc_payload(payload)
        self.assertEqual(chunk.kind_key, "GENERIC")
        self.assertEqual(chunk.class_id, 76)

    def test_idsizes_kept_opaque(self):
        import struct

        payload = struct.pack("<12I", 42, 0, 0, 0, 0, 0, 7, 256, 9, 512, 0, 0)
        chunk = semantics.parse_vloc_payload(payload)
        self.assertEqual(chunk.kind_key, "IDSIZES")
        self.assertEqual(chunk.opaque_payload, payload)

    def test_runtime_notes_for_injected_spinner(self):
        notes = semantics.vloc_runtime_notes(15)
        self.assertTrue(any("rate stays zero" in n for n in notes))

    def test_runtime_notes_for_injected_hardpoint(self):
        notes = semantics.vloc_runtime_notes(70)
        self.assertTrue(any("empty names" in n for n in notes))


class DamageModelTests(unittest.TestCase):
    def make_table(self):
        base = vdf_classes.serialize_section(
            _record_bytes := None
        ) if False else None
        table = semantics.DamageVariantTable()
        return table

    def test_band_layout_matches_stock(self):
        # LOD slots at stride 4; damage states contiguous within a slot.
        self.assertEqual(semantics.band_index(0, 0), 0)
        self.assertEqual(semantics.band_index(1, 0), 4)
        self.assertEqual(semantics.band_index(2, 0), 8)
        self.assertEqual(semantics.band_index(0, 1), 1)
        self.assertEqual(semantics.band_index(0, 3), 3)

    def test_capture_and_rebuild_verbatim(self):
        records = [b"\x00" * 100] * (28 * 2)
        base_record = make_named_record("fxv11", radius=2.0)
        variant = make_named_record("fxvd1", radius=2.0)
        records[0] = base_record
        records[1 * 2 + 0] = variant  # band 1 slot 0
        table = semantics.DamageVariantTable()
        table.capture_band_records(records, 2)
        self.assertTrue(table.has_damage_content())
        self.assertEqual(
            table.build_band_record(0, 1), records[1 * 2 + 0]
        )

    def test_authored_name_synthesizes_from_base(self):
        base_record = make_named_record("fxv11", radius=3.0)
        table = semantics.DamageVariantTable()
        table.base_records[0] = base_record
        table.set_variant_name(0, 2, "fxv22")
        built = table.build_band_record(0, semantics.band_index(0, 2))
        parsed = parse_record(built)
        self.assertEqual(parsed.name.lower(), "fxv22")
        self.assertAlmostEqual(parsed.sphereradius, 3.0)
        self.assertEqual(built[8:], base_record[8:])

    def test_filler_preserved_over_synthesis(self):
        base_record = bytearray(make_named_record("fxv11"))
        variant = bytearray(make_named_record("fxt11"))
        variant[42] = 0x7F
        records = [bytes(base_record)] * (28 * 2)
        records[2] = bytes(variant)
        table = semantics.DamageVariantTable()
        table.capture_band_records(records, 2)
        self.assertEqual(table.build_band_record(0, 1), bytes(variant))


def make_named_record(name, radius=1.0):
    """Standalone record builder to avoid importing the fixture builder."""
    import struct as _struct

    data = bytearray(100)
    name_bytes = name.encode("ascii")[:8]
    data[0 : len(name_bytes)] = name_bytes
    _struct.pack_into("<f", data, 76, radius)  # SphereRadius @ 0x4C
    return bytes(data)


class VDFRoundTripTests(unittest.TestCase):
    FIXTURES = [
        "ordinary.vdf",
        "special_types.vdf",
        "known_flags.vdf",
        "unknown_flags.vdf",
        "unknown_type.vdf",
        "custom_bounds.vdf",
        "eyepoint.vdf",
        "bridge_floor.vdf",
        "damage_rep.vdf",
        "combination.vdf",
    ]

    def test_all_fixtures_parse(self):
        for name in self.FIXTURES:
            parsed = vdf_file.parse_vdf(load_fixture(name))
            self.assertGreater(parsed.geocount, 0, name)

    def test_byte_exact_round_trip(self):
        for name in self.FIXTURES:
            original = load_fixture(name)
            parsed = vdf_file.parse_vdf(original)
            rebuilt = vdf_file.serialize_vdf(parsed)
            self.assertEqual(rebuilt, original, f"non-exact round trip: {name}")

    def test_double_round_trip_stability(self):
        for name in self.FIXTURES:
            original = load_fixture(name)
            once = vdf_file.serialize_vdf(vdf_file.parse_vdf(original))
            twice = vdf_file.serialize_vdf(vdf_file.parse_vdf(once))
            self.assertEqual(once, twice, name)

    def test_semantic_equality_after_round_trip(self):
        for name in self.FIXTURES:
            original = load_fixture(name)
            first_pass = vdf_file.parse_vdf(original)
            reparsed = vdf_file.parse_vdf(vdf_file.serialize_vdf(first_pass))
            self.assertEqual(
                [bytes(r) for r in first_pass.records],
                [bytes(r) for r in reparsed.records],
                name,
            )
            self.assertEqual(first_pass.geocount, reparsed.geocount, name)
            self.assertEqual(len(first_pass.vlocs), len(reparsed.vlocs), name)

    def test_unknown_part_type_value_preserved(self):
        parsed = vdf_file.parse_vdf(load_fixture("unknown_type.vdf"))
        geo = parse_record(parsed.records[0])
        self.assertEqual(geo.type, 0x2A)
        self.assertEqual(int(geo.geoflags) & 0xFFFF, 0x8100)

    def test_unknown_chunk_preserved_verbatim(self):
        parsed = vdf_file.parse_vdf(load_fixture("combination.vdf"))
        raw_tags = [chunk.tag for chunk in parsed.raw_chunks]
        self.assertIn(b"SCHK", raw_tags)
        raw = next(c for c in parsed.raw_chunks if c.tag == b"SCHK")
        self.assertIn(b"\xDE\xAD\xBE\xEF", raw.full_bytes)

    def test_vloc_entries_parsed_with_kinds(self):
        parsed = vdf_file.parse_vdf(load_fixture("combination.vdf"))
        kinds = [chunk.kind_key for chunk in parsed.vlocs]
        self.assertEqual(kinds, ["HEADLIGHT", "POV", "GENERIC", "IDSIZES"])
        generic = parsed.vlocs[2]
        self.assertEqual(generic.class_id, 77)
        self.assertAlmostEqual(generic.matrix[9], 0.5)

    def test_damage_variant_names_surface(self):
        parsed = vdf_file.parse_vdf(load_fixture("damage_rep.vdf"))
        table = semantics.DamageVariantTable()
        table.capture_band_records(parsed.records, parsed.geocount)
        state1_slot0 = parse_record(table.build_band_record(0, 1))
        self.assertEqual(state1_slot0.name.lower(), "fxvd1")
        state1_slot1 = parse_record(table.build_band_record(1, 1))
        self.assertEqual(state1_slot1.name.lower(), "fxt11")
        # undocumented filler byte survives
        self.assertEqual(table.variant_records[(1, 1)][42], 0x7F)

    def test_custom_bounds_preserved(self):
        parsed = vdf_file.parse_vdf(load_fixture("custom_bounds.vdf"))
        geo = parse_record(parsed.records[0])
        self.assertAlmostEqual(geo.sphereradius, 33.0)
        self.assertAlmostEqual(geo.geocenter[0], 12.5)
        self.assertAlmostEqual(geo.boxhalfheight[1], 4.0)
        flags = int(geo.geoflags) & 0xFFFFFFFF
        self.assertTrue(flags & semantics.OBJFLAG_KEEP_BOUNDS)

    def test_eyepoint_transform_preserved(self):
        parsed = vdf_file.parse_vdf(load_fixture("eyepoint.vdf"))
        pov = None
        for slot, raw in enumerate(parsed.band_records(0)):
            geo = parse_record(raw)
            if int(geo.type) == 40:
                pov = geo
        self.assertIsNotNone(pov)
        self.assertEqual(pov.name.lower(), "fxp11")
        self.assertAlmostEqual(pov.matrix[10], 0.85)
        self.assertAlmostEqual(pov.matrix[11], 0.45)

    def test_anim_tail_dwords_survive(self):
        parsed = vdf_file.parse_vdf(load_fixture("combination.vdf"))
        self.assertTrue(parsed.anim_present)
        element = parsed.anim_elements[0]
        expected = [(i * 3) % 97 for i in range(32)]
        self.assertEqual(list(element.unknowngeoflag), expected)
        orientation = parsed.anim_orientations[0]
        self.assertEqual(int(orientation.unknown), 5)

    def test_bridge_floor_configuration(self):
        parsed = vdf_file.parse_vdf(load_fixture("bridge_floor.vdf"))
        root = parse_record(parsed.band_records(0)[0])
        deck = parse_record(parsed.band_records(0)[1])
        wall = parse_record(parsed.band_records(0)[2])
        self.assertEqual(root.type, semantics.BRIDGE_CLASS)
        self.assertEqual(deck.type, semantics.FLOOR_CLASS)
        self.assertEqual(wall.type, 60)
        self.assertEqual(deck.parent.lower(), "fxr11")


class DeckClassificationTests(unittest.TestCase):
    def test_steep_faces_not_drivable(self):
        total, drivable = semantics.classify_deck_faces([1.0, 0.9, -1.0, 0.2, 0.39])
        self.assertEqual(total, 5)
        self.assertEqual(drivable, 2)


class FreshExportPlanTests(unittest.TestCase):
    """Simulates the exporter's path for newly authored scenes: canonical plan
    plus a user-added VLOC entry must serialize and reparse cleanly."""

    def test_fresh_plan_with_user_vloc_round_trips(self):
        import struct

        model = vdf_file.ParsedVDF()
        model.plan = list(vdf_file.new_empty_plan())
        model.vdfc_name = "sim"
        model.vdfc_vehicletype = 1
        model.vdfc_lod_dists = [10, 20, 30, 40, 50]
        model.geocount = 1

        geo = vdf_classes.GEOData()
        geo.name = "sim11"
        geo.parent = "WORLD"
        geo.matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
        geo.geocenter = [0, 0, 0]
        geo.sphereradius = 2.0
        geo.boxhalfheight = [1, 1, 1]
        geo.type = 60
        geo.geoflags = 0
        model.records = [vdf_classes.serialize_section(geo)] + [b"\x00" * 100] * 27

        chunk = semantics.VLOCChunk()
        chunk.kind_value = 77
        chunk.matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.25, 0.5, 0.75]
        chunk.opaque_payload = b""
        chunk.preserve_raw = False
        model.vlocs.append(chunk)

        # Mirror of the exporter's plan reconciliation.
        while sum(1 for kind, _ in model.plan if kind == "vloc") < len(model.vlocs):
            model.plan.append(
                ("vloc", sum(1 for kind, _ in model.plan if kind == "vloc"))
            )

        data = vdf_file.serialize_vdf(model)
        reparsed = vdf_file.parse_vdf(data)
        self.assertEqual(reparsed.geocount, 1)
        self.assertEqual(len(reparsed.vlocs), 1)
        self.assertEqual(reparsed.vlocs[0].class_id, 77)
        self.assertAlmostEqual(reparsed.vlocs[0].matrix[9], 0.25)
        base = parse_record(reparsed.records[0])
        self.assertEqual(base.name.lower(), "sim11")

    def test_new_empty_plan_has_exit_after_vdfc(self):
        plan = vdf_file.new_empty_plan()
        kinds = [kind for kind, _ in plan]
        self.assertGreater(kinds.index("exit"), kinds.index("vdfc"))
        self.assertEqual(kinds[0], "vdfc")
        self.assertEqual(kinds[kinds.index("vdfc") + 1], "exit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
