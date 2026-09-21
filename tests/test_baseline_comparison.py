"""Failure-aware B0/B1 comparison and generic conflict-evaluator tests."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.baseline_comparison import compare_baselines, write_baseline_comparison
from src.clash import evaluate_b0_conflicts, evaluate_b1_conflicts, evaluate_route_result_conflicts
from src.demand import load_service_definitions
from src.routing import run_b0_benchmark
from src.voxel import build_occupancy_grids


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"


class BaselineComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.comparison = compare_baselines(SCENARIOS, DEMANDS, SYSTEMS)

    def test_generic_evaluator_and_b0_regression(self) -> None:
        grids = build_occupancy_grids(SCENARIOS, SYSTEMS)
        b0 = run_b0_benchmark(SCENARIOS, DEMANDS, SYSTEMS, grids=grids)
        generic = evaluate_route_result_conflicts(
            b0, grids, load_service_definitions(SYSTEMS), "B0-3C",
            "B0 Inter-System Conflict Evaluation", "b0_search_statistics", b0["search_statistics"],
        )
        wrapped = evaluate_b0_conflicts(SCENARIOS, DEMANDS, SYSTEMS)
        self.assertEqual(generic, wrapped)
        metrics = wrapped["global_metrics"]
        self.assertEqual(metrics["total_inter_system_route_pairs_evaluated"], 3248)
        self.assertEqual(metrics["total_hard_conflicting_route_pairs"], 1256)
        self.assertEqual(metrics["total_clearance_violating_route_pairs"], 1257)
        self.assertEqual(metrics["total_clearance_only_route_pairs"], 1)

    def test_b1_conflicts_only_use_successful_geometry(self) -> None:
        b1 = evaluate_b1_conflicts(SCENARIOS, DEMANDS, SYSTEMS)
        for case in b1["cases"]:
            self.assertEqual(case["requested_route_count"], case["successful_route_count"] + case["failed_route_count"])
            self.assertEqual(len(case["routes"]), case["requested_route_count"])
            self.assertEqual(sum(route["path_found"] for route in case["routes"]), case["successful_route_count"])

    def test_comparison_denominators_common_subset_and_completion(self) -> None:
        self.assertEqual(list(self.comparison["methods"]), ["B0", "B1"])
        self.assertEqual(len(self.comparison["cases"]), 12)
        for name in ("B0", "B1"):
            routing = self.comparison["methods"][name]["routing"]
            self.assertEqual(routing["total_route_requests"], 292)
            self.assertEqual(routing["successful_routes"] + routing["failed_routes"], 292)
        for case in self.comparison["cases"]:
            common = case["common_success"]
            self.assertEqual(common["common_inter_system_pair_count"], common["B1_common_inter_system_pair_count"])
            self.assertEqual(common["common_successful_connection_count"], case["B1"]["successful"])
        b1_completion = self.comparison["methods"]["B1"]["completion"]
        self.assertLessEqual(b1_completion["routing_complete_and_hard_conflict_free_case_count"], b1_completion["routing_complete_case_count"])
        self.assertLessEqual(b1_completion["routing_complete_and_clearance_compliant_case_count"], b1_completion["routing_complete_case_count"])

    def test_ordering_determinism_and_serialization(self) -> None:
        self.assertEqual(self.comparison, compare_baselines(SCENARIOS, DEMANDS, SYSTEMS))
        self.assertEqual([row["system"] for row in self.comparison["per_system"]], ["hvac", "drainage", "water", "fire", "electrical"])
        self.assertEqual([row["priority_rank"] for row in self.comparison["priority_impact"]], [1, 2, 3, 4, 5])
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            write_baseline_comparison(SCENARIOS, DEMANDS, SYSTEMS, first)
            write_baseline_comparison(SCENARIOS, DEMANDS, SYSTEMS, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
