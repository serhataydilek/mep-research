"""Tests for deterministic Phase 3C B0 inter-system conflict evaluation."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.clash import (
    Segment,
    _evaluate_route_pair,
    SYSTEM_PAIR_ORDER,
    compress_path_cells,
    evaluate_b0_conflicts,
    nominal_half_extents,
    positive_aabb_overlap,
    swept_segment_aabb,
    write_b0_conflicts,
)
from src.demand import load_service_definitions
from src.voxel import build_occupancy_grids
from src.voxel import GridSpec


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"
HVAC = {"envelope_type": "rectangular", "width_m": 0.4, "height_m": 0.25}
PIPE = {"envelope_type": "circular", "diameter_m": 0.1}
DEFINITIONS = {"hvac": HVAC, "water": PIPE, "drainage": PIPE}
SPEC = GridSpec(1.0, 0.0, 0.0, 0.0, 8, 8, 4)


def route(system: str, connection_id: str, cells: list[list[int]]) -> dict:
    return {"system": system, "connection_id": connection_id, "path_cells": cells}


class GeometryTests(unittest.TestCase):
    def test_service_extents_and_overlap_boundaries(self) -> None:
        self.assertEqual(nominal_half_extents(HVAC), (0.2, 0.125))
        self.assertEqual(nominal_half_extents(PIPE), (0.05, 0.05))
        self.assertFalse(positive_aabb_overlap((0, 1, 0, 1, 0, 1), (1, 2, 0, 1, 0, 1)))

    def test_hard_clearance_and_clearance_only_classifications(self) -> None:
        first = route("hvac", "hvac/0", [[0, 0, 0], [1, 0, 0]])
        overlapping = route("water", "water/0", [[0, 0, 0], [1, 0, 0]])
        hard = _evaluate_route_pair("case", first, overlapping, SPEC, DEFINITIONS)
        self.assertTrue(hard["hard_envelope_conflict"])
        self.assertTrue(hard["clearance_violation"])
        self.assertFalse(hard["clearance_only_violation"])

        a = Segment((0, 0, 0), (1, 0, 0))
        below_clearance = Segment((0, 0.14, 0), (1, 0.14, 0))
        exact_clearance = Segment((0, 0.15, 0), (1, 0.15, 0))
        nominal_a = swept_segment_aabb(a, 0.05, 0.05)
        self.assertFalse(positive_aabb_overlap(nominal_a, swept_segment_aabb(below_clearance, 0.05, 0.05)))
        self.assertTrue(positive_aabb_overlap(swept_segment_aabb(a, 0.05, 0.05, 0.025), swept_segment_aabb(below_clearance, 0.05, 0.05, 0.025)))
        self.assertFalse(positive_aabb_overlap(swept_segment_aabb(a, 0.05, 0.05, 0.025), swept_segment_aabb(exact_clearance, 0.05, 0.05, 0.025)))
        fine_spec = GridSpec(0.01, 0.0, 0.0, 0.0, 200, 200, 1)
        clearance_only = _evaluate_route_pair(
            "case", route("water", "water/1", [[0, 0, 0], [100, 0, 0]]),
            route("drainage", "drainage/1", [[0, 14, 0], [100, 14, 0]]), fine_spec, DEFINITIONS
        )
        self.assertFalse(clearance_only["hard_envelope_conflict"])
        self.assertTrue(clearance_only["clearance_violation"])
        self.assertTrue(clearance_only["clearance_only_violation"])

    def test_perpendicular_segments_need_z_overlap(self) -> None:
        horizontal = route("hvac", "hvac/0", [[0, 1, 0], [2, 1, 0]])
        crossing = route("water", "water/0", [[1, 0, 0], [1, 2, 0]])
        separated = route("water", "water/1", [[1, 0, 2], [1, 2, 2]])
        self.assertTrue(_evaluate_route_pair("case", horizontal, crossing, SPEC, DEFINITIONS)["hard_envelope_conflict"])
        self.assertFalse(_evaluate_route_pair("case", horizontal, separated, SPEC, DEFINITIONS)["hard_envelope_conflict"])


class CompressionTests(unittest.TestCase):
    def test_compression_and_world_endpoints(self) -> None:
        straight = compress_path_cells([[0, 0, 0], [1, 0, 0], [2, 0, 0]], SPEC)
        self.assertEqual(len(straight), 1)
        self.assertEqual(straight[0].start, SPEC.cell_center(0, 0, 0))
        self.assertEqual(straight[0].end, SPEC.cell_center(2, 0, 0))
        one_bend = compress_path_cells([[0, 0, 0], [1, 0, 0], [1, 1, 0]], SPEC)
        self.assertEqual(len(one_bend), 2)
        multi_bend = compress_path_cells([[0, 0, 0], [1, 0, 0], [1, 1, 0], [1, 1, 1]], SPEC)
        self.assertEqual(len(multi_bend), 3)
        zero = compress_path_cells([[2, 2, 1]], SPEC)
        self.assertEqual(zero, (Segment(SPEC.cell_center(2, 2, 1), SPEC.cell_center(2, 2, 1)),))


class ConflictIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = evaluate_b0_conflicts(SCENARIOS, DEMANDS, SYSTEMS)

    def test_case_pair_counts_details_and_same_system_diagnostics(self) -> None:
        self.assertEqual(len(self.result["cases"]), 12)
        self.assertEqual([row["system_pair"] for row in self.result["system_pair_metrics"]], list(SYSTEM_PAIR_ORDER))
        self.assertEqual(self.result["b0_search_statistics"]["total_route_instances"], 292)
        self.assertEqual(self.result["b0_search_statistics"]["successful_route_instances"], 292)
        for case in self.result["cases"]:
            routes = case["successful_route_count"]
            total_pairs = routes * (routes - 1) // 2
            self.assertEqual(total_pairs, case["inter_system_route_pair_count"] + case["same_system_route_pair_count"])
            self.assertEqual(case["route_pair_count"], case["inter_system_route_pair_count"])
            self.assertEqual(sum(item["route_pair_count"] for item in case["system_pair_summaries"]), case["route_pair_count"])
            self.assertEqual([item["system_pair"] for item in case["system_pair_summaries"]], list(SYSTEM_PAIR_ORDER))
            self.assertLessEqual(case["hard_conflicting_route_pair_count"], case["clearance_violating_route_pair_count"])
            self.assertEqual(case["clearance_only_route_pair_count"], case["clearance_violating_route_pair_count"] - case["hard_conflicting_route_pair_count"])
            details = case["conflict_details"]
            self.assertEqual(len(details), case["clearance_violating_route_pair_count"])
            self.assertTrue(all(detail["route_a_system"] != detail["route_b_system"] for detail in details))
            for route_record in case["routes"]:
                self.assertIn("inter_system_hard_conflict_partner_count", route_record)
                self.assertIn("inter_system_clearance_violation_partner_count", route_record)

    def test_invariance_determinism_and_output_reproducibility(self) -> None:
        pair_results = {}
        for case in self.result["cases"]:
            for detail in case["conflict_details"]:
                key = (case["scenario_id"], detail["system_pair"], detail["route_a_connection_id"], detail["route_b_connection_id"])
                value = tuple(detail[field] for field in ("hard_envelope_conflict", "clearance_violation", "hard_segment_intersection_count", "clearance_segment_intersection_count"))
                self.assertEqual(pair_results.setdefault(key, value), value)
        self.assertEqual(self.result, evaluate_b0_conflicts(SCENARIOS, DEMANDS, SYSTEMS))
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            write_b0_conflicts(SCENARIOS, DEMANDS, SYSTEMS, first)
            write_b0_conflicts(SCENARIOS, DEMANDS, SYSTEMS, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_all_nested_inter_system_pair_classifications_are_invariant(self) -> None:
        grids = build_occupancy_grids(SCENARIOS, SYSTEMS)
        definitions = load_service_definitions(SYSTEMS)
        classifications = {}
        for case in self.result["cases"]:
            spec = grids[(case["scenario_id"], "hvac")].spec
            routes = sorted(case["routes"], key=lambda item: (item["system"], item["connection_id"]))
            for first_index, first in enumerate(routes):
                for second in routes[first_index + 1 :]:
                    if first["system"] == second["system"]:
                        continue
                    record = _evaluate_route_pair(case["case_id"], first, second, spec, definitions)
                    key = (case["scenario_id"], record["system_pair"], record["route_a_connection_id"], record["route_b_connection_id"])
                    value = tuple(record[field] for field in (
                        "hard_envelope_conflict", "clearance_violation",
                        "hard_segment_intersection_count", "clearance_segment_intersection_count",
                    ))
                    self.assertEqual(classifications.setdefault(key, value), value)
