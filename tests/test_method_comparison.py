"""Focused regression tests for the Phase 6C3A five-method comparison."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.method_comparison import METHOD_ORDER, compare_methods, write_method_comparison


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"
CONSTRAINTS = ROOT / "config" / "constructability.json"
COORDINATOR = ROOT / "config" / "coordinator.json"


class MethodComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = compare_methods(SCENARIOS, DEMANDS, SYSTEMS, CONSTRAINTS, COORDINATOR)

    def test_cardinality_population_and_required_metrics(self) -> None:
        self.assertEqual(list(METHOD_ORDER), self.result["comparison"]["method_order"])
        self.assertEqual(12, self.result["comparison"]["case_count"])
        self.assertTrue(self.result["comparison"]["requested_connection_population_is_consistent"])
        for name in METHOD_ORDER:
            metrics = self.result["methods"][name]["metrics"]
            routing = metrics["routing"]
            self.assertEqual(292, routing["total_requested_connections"])
            self.assertEqual(292, routing["successful_routes"] + routing["failed_routes"])
            self.assertEqual(set(("routing", "authoritative_conflict_evaluation", "route_geometry_quality_successful_routes_only", "constructability_C0")), set(metrics))
        common = self.result["all_method_common_success_subset"]
        self.assertEqual(12, len(common["per_case_common_successful_connection_counts"]))
        self.assertEqual(list(METHOD_ORDER), list(common["methods"]))

    def test_pcore_regression_and_integrity(self) -> None:
        diagnostics = self.result["pcore_coordination_diagnostics"]
        self.assertEqual(1020, diagnostics["initial_conflict_objective"]["hard_conflicting_route_pair_count"])
        self.assertEqual(747, diagnostics["final_conflict_objective"]["hard_conflicting_route_pair_count"])
        self.assertEqual(1209, diagnostics["initial_conflict_objective"]["clearance_violating_route_pair_count"])
        self.assertEqual(926, diagnostics["final_conflict_objective"]["clearance_violating_route_pair_count"])
        self.assertEqual(16, diagnostics["accepted_rounds"])
        self.assertEqual({"base_occupancy_grids_unchanged": True, "scenario_demand_and_config_inputs_unchanged": True}, self.result["integrity_checks"])
        self.assertEqual(12, self.result["methods"]["P-CORE-6C2B"]["metrics"]["constructability_C0"]["drainage_gravity_compliant_case_count"])

    def test_deterministic_serialization(self) -> None:
        with TemporaryDirectory() as directory:
            first, second = Path(directory) / "first.json", Path(directory) / "second.json"
            write_method_comparison(SCENARIOS, DEMANDS, SYSTEMS, CONSTRAINTS, COORDINATOR, first)
            write_method_comparison(SCENARIOS, DEMANDS, SYSTEMS, CONSTRAINTS, COORDINATOR, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
