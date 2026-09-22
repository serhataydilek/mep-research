"""Phase 6B integration checks: one B0-derived repair pass, no route deletion."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from src.clash import evaluate_route_result_conflicts
from src.conflict_diagnosis import diagnose_route_conflicts
from src.constructability import load_constructability_config, verify_route_result
from src.demand import load_service_definitions
from src.routing import run_b0_benchmark
from src.selective_repair import run_b2_benchmark, selective_repair_pass
from src.voxel import build_occupancy_grids


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS, DEMANDS = ROOT / "experiments" / "scenarios", ROOT / "experiments" / "demands"
SYSTEMS, CONSTRAINTS = ROOT / "config" / "mep_systems.json", ROOT / "config" / "constructability.json"


class SelectiveRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grids = build_occupancy_grids(SCENARIOS, SYSTEMS)
        cls.definitions = load_service_definitions(SYSTEMS)
        cls.b0 = run_b0_benchmark(SCENARIOS, DEMANDS, SYSTEMS, grids=cls.grids)
        cls.conflicts = evaluate_route_result_conflicts(cls.b0, cls.grids, cls.definitions, "B0-3C", "B0", "stats", {})
        cls.diagnosis = diagnose_route_conflicts(cls.b0, cls.conflicts)
        cls.b2 = selective_repair_pass(cls.b0, cls.diagnosis, cls.grids, cls.definitions)

    def test_edge_cover_leaves_no_nonselected_initial_edge(self):
        selected = {case["case_id"]: {(row["system"], row["connection_id"]) for row in case["selected_repair_candidates"]}
                    for case in self.diagnosis["cases"]}
        for case in self.conflicts["cases"]:
            for edge in case["conflict_details"]:
                first = edge["route_a_system"], edge["route_a_connection_id"]
                second = edge["route_b_system"], edge["route_b_connection_id"]
                self.assertTrue(first in selected[case["case_id"]] or second in selected[case["case_id"]])

    def test_connectivity_noncandidate_and_failed_fallback_invariance(self):
        originals = {(case["case_id"], route["system"], route["connection_id"]): route for case in self.b0["cases"] for route in case["routes"]}
        for case in self.b2["cases"]:
            self.assertEqual(case["connection_count"], case["successful_route_count"])
            for route in case["routes"]:
                original = originals[(case["case_id"], route["system"], route["connection_id"])]
                if not route["was_selected_for_repair"] or not route["repair_attempt_succeeded"]:
                    for field in ("start", "end", "path_cells", "grid_route_length_m", "bend_count", "vertical_travel_m", "endpoint_snap_distance_total_m"):
                        self.assertEqual(original[field], route[field])

    def test_input_immutability_final_evaluator_and_c0_compatibility(self):
        b0, conflicts, diagnosis = deepcopy(self.b0), deepcopy(self.conflicts), deepcopy(self.diagnosis)
        selective_repair_pass(self.b0, self.diagnosis, self.grids, self.definitions)
        self.assertEqual(b0, self.b0); self.assertEqual(conflicts, self.conflicts); self.assertEqual(diagnosis, self.diagnosis)
        final = evaluate_route_result_conflicts(self.b2, self.grids, self.definitions, "B2-6B", "B2", "summary", {})
        verified = verify_route_result(self.b2, self.grids, load_constructability_config(CONSTRAINTS), self.definitions)
        self.assertEqual(12, len(final["cases"])); self.assertEqual(12, len(verified["cases"]))
        self.assertEqual(292, sum(case["successful_route_count"] for case in self.b2["cases"]))

    def test_benchmark_wrapper_is_single_pass_and_complete(self):
        result = run_b2_benchmark(SCENARIOS, DEMANDS, SYSTEMS, CONSTRAINTS)
        self.assertEqual(169, result["global_summary"]["repair_attempts"])
        self.assertEqual(292, result["global_summary"]["successful_routes"])
        self.assertTrue(any(route["was_selected_for_repair"] for case in result["cases"] for route in case["routes"]))
