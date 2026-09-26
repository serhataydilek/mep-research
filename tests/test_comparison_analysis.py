"""Focused Phase 6C3B stratified-analysis tests."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.comparison_analysis import analyze_comparison, run_comparison_analysis, write_comparison_analysis
from src.method_comparison import compare_methods


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"
CONSTRAINTS = ROOT / "config" / "constructability.json"
COORDINATOR = ROOT / "config" / "coordinator.json"


class ComparisonAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.analysis = run_comparison_analysis(SCENARIOS, DEMANDS, SYSTEMS, CONSTRAINTS, COORDINATOR)

    def test_orders_strata_case_evidence_and_checkpoint_metrics(self) -> None:
        self.assertEqual(12, self.analysis["analysis"]["case_count"])
        self.assertEqual(["B0", "B1", "B2", "P-CORE-SEED", "P-CORE-6C2B"], self.analysis["analysis"]["method_order"])
        self.assertEqual(["s01", "s02", "s03", "s04"], [row["scenario_id"] for row in self.analysis["scenario_stratified"]])
        self.assertEqual(["d01", "d02", "d03"], [row["demand_profile_id"] for row in self.analysis["demand_stratified"]])
        self.assertEqual(["hvac", "drainage", "water", "fire", "electrical"], [row["system"] for row in self.analysis["per_system"]])
        self.assertEqual(12, len(self.analysis["case_level_evidence"]))
        coverage = {row["method"]: row for row in self.analysis["coverage_vs_conflict_visibility"]}
        self.assertEqual(1256, coverage["B0"]["hard_conflicting_route_pairs"])
        self.assertEqual(144, coverage["B1"]["successful_routes"])
        self.assertEqual(936, coverage["B2"]["hard_conflicting_route_pairs"])
        self.assertEqual(1020, coverage["P-CORE-SEED"]["hard_conflicting_route_pairs"])
        self.assertEqual(747, coverage["P-CORE-6C2B"]["hard_conflicting_route_pairs"])
        self.assertEqual(926, coverage["P-CORE-6C2B"]["clearance_violating_route_pairs"])

    def test_analysis_is_deterministic_and_serializable(self) -> None:
        self.assertTrue(self.analysis["integrity_checks"]["comparison_result_unchanged"])
        self.assertTrue(self.analysis["integrity_checks"]["base_occupancy_grids_unchanged"])
        with TemporaryDirectory() as directory:
            first, second = Path(directory) / "first.json", Path(directory) / "second.json"
            write_comparison_analysis(self.analysis, first)
            write_comparison_analysis(self.analysis, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
