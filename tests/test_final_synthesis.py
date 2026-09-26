"""Focused Phase 8 final synthesis and report tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.final_synthesis import (
    percentage_change,
    render_final_report,
    run_final_synthesis,
    write_final_synthesis,
)
from src.method_comparison import METHOD_ORDER


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"
CONSTRAINTS = ROOT / "config" / "constructability.json"
COORDINATOR = ROOT / "config" / "coordinator.json"


class PercentageChangeTests(unittest.TestCase):
    def test_percentage_change_uses_seed_and_exact_delta(self) -> None:
        change = percentage_change(200, 150)
        self.assertEqual(-50, change["delta"])
        self.assertEqual(-25.0, change["percentage_change"])
        self.assertEqual("seed", change["percentage_denominator"])
        self.assertFalse(change["zero_denominator"])

    def test_percentage_change_handles_zero_denominator(self) -> None:
        change = percentage_change(0, 4)
        self.assertEqual(4, change["delta"])
        self.assertIsNone(change["percentage_change"])
        self.assertTrue(change["zero_denominator"])


class FinalSynthesisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.synthesis = run_final_synthesis(SCENARIOS, DEMANDS, SYSTEMS, CONSTRAINTS, COORDINATOR)
        cls.report = render_final_report(cls.synthesis)

    def test_scope_methods_and_deterministic_orders(self) -> None:
        self.assertEqual(list(METHOD_ORDER), self.synthesis["metadata"]["method_order"])
        self.assertEqual(list(METHOD_ORDER), [row["method"] for row in self.synthesis["global_metrics"]])
        self.assertEqual(12, self.synthesis["benchmark_scope"]["benchmark_cases"])
        self.assertEqual(4, self.synthesis["benchmark_scope"]["architectural_scenarios"])
        self.assertEqual(3, self.synthesis["benchmark_scope"]["demand_profiles"])
        self.assertEqual(["s01", "s02", "s03", "s04"], [row["scenario_id"] for row in self.synthesis["scenario_summary"]])
        self.assertEqual(["d01", "d02", "d03"], [row["demand_profile_id"] for row in self.synthesis["demand_summary"]])
        self.assertEqual(["hvac", "drainage", "water", "fire", "electrical"], [row["system"] for row in self.synthesis["system_summary"]])

    def test_checkpoint_global_metrics_and_c0_visibility(self) -> None:
        metrics = {row["method"]: row for row in self.synthesis["global_metrics"]}
        self.assertEqual(1256, metrics["B0"]["hard_conflicts"])
        self.assertEqual(144, metrics["B1"]["successful_routes"])
        self.assertEqual(292, metrics["B1"]["requested_routes"])
        self.assertEqual(936, metrics["B2"]["hard_conflicts"])
        self.assertEqual(1020, metrics["P-CORE-SEED"]["hard_conflicts"])
        self.assertEqual(747, metrics["P-CORE-6C2B"]["hard_conflicts"])
        self.assertEqual(926, metrics["P-CORE-6C2B"]["clearance_violations"])
        self.assertEqual(16, metrics["P-CORE-6C2B"]["coordination"]["accepted_rounds"])
        self.assertEqual(144, self.synthesis["benchmark_scope"]["common_success_routes"])
        self.assertTrue(all(row["benchmark_hard_feasible_C0_cases"] == 0 for row in metrics.values()))

    def test_pcore_delta_arithmetic_and_percentages(self) -> None:
        metrics = {row["method"]: row for row in self.synthesis["global_metrics"]}
        seed, final = metrics["P-CORE-SEED"], metrics["P-CORE-6C2B"]
        source_fields = {
            "hard_conflicts": "hard_conflicts",
            "clearance_violations": "clearance_violations",
            "total_routed_length_m": "total_routed_length_m",
            "total_bends": "total_bends",
            "total_vertical_travel_m": "total_vertical_travel_m",
        }
        for delta_name, field in source_fields.items():
            change = self.synthesis["pcore_delta"][delta_name]
            expected_delta = final[field] - seed[field]
            self.assertEqual(expected_delta, change["delta"])
            self.assertEqual(expected_delta / seed[field] * 100, change["percentage_change"])

    def test_phase7_pareto_evidence_is_preserved(self) -> None:
        expected_all = list(METHOD_ORDER)
        pareto = self.synthesis["pareto_summary"]
        self.assertEqual(expected_all, pareto["global_operational_non_dominated_methods"])
        self.assertEqual(expected_all, pareto["global_common_success_non_dominated_methods"])
        sensitivity = {row["objective_set"]: row["non_dominated_methods"] for row in pareto["objective_set_sensitivity"]}
        self.assertEqual(["B1"], sensitivity["conflict_only"])
        self.assertEqual(expected_all, sensitivity["conflict_geometry"])
        self.assertEqual(expected_all, sensitivity["operational"])
        self.assertEqual(
            {"regression": 0, "strict_multiobjective_improvement": 0, "tradeoff": 11, "unchanged": 1},
            pareto["pcore_transition_classification_counts"],
        )

    def test_limitations_threats_findings_and_conclusion_exist(self) -> None:
        self.assertEqual(
            {"benchmark_scope", "routing_abstraction", "engineering_semantics", "ifc_scope", "optimization_scope", "external_validity"},
            set(self.synthesis["limitations"]),
        )
        self.assertEqual(
            {"internal_validity", "construct_validity", "external_validity", "reproducibility"},
            set(self.synthesis["threats_to_validity"]),
        )
        self.assertGreaterEqual(len(self.synthesis["research_findings"]), 4)
        for finding in self.synthesis["research_findings"]:
            self.assertEqual({"finding", "evidence", "scope", "interpretation"}, set(finding))
        conclusion = self.synthesis["conclusion"]["statement"].lower()
        self.assertIsNone(re.search(r"\b(winner|rank|ranking|recommendation|preferred)\b", conclusion))

    def test_research_integrity_and_source_immutability(self) -> None:
        disallowed = {"winner", "rank", "ranking", "score", "recommendation", "preferred_method"}

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value)) if value else set()
            return set()

        self.assertTrue(disallowed.isdisjoint(keys(self.synthesis)))
        integrity = self.synthesis["integrity_checks"]
        self.assertTrue(integrity["comparison_input_unchanged"])
        self.assertTrue(integrity["stratified_input_unchanged"])
        self.assertTrue(integrity["pareto_input_unchanged"])
        self.assertTrue(integrity["scenario_demand_and_config_inputs_unchanged"])
        self.assertTrue(integrity["base_occupancy_grids_unchanged"])

    def test_json_and_markdown_serialization_are_byte_identical(self) -> None:
        self.assertEqual(
            json.dumps(self.synthesis, indent=2, sort_keys=True) + "\n",
            json.dumps(self.synthesis, indent=2, sort_keys=True) + "\n",
        )
        self.assertEqual(self.report, render_final_report(self.synthesis))
        with TemporaryDirectory() as directory:
            first_json = Path(directory) / "first.json"
            first_md = Path(directory) / "first.md"
            second_json = Path(directory) / "second.json"
            second_md = Path(directory) / "second.md"
            write_final_synthesis(self.synthesis, first_json, first_md)
            write_final_synthesis(self.synthesis, second_json, second_md)
            self.assertEqual(first_json.read_bytes(), second_json.read_bytes())
            self.assertEqual(first_md.read_bytes(), second_md.read_bytes())

    def test_generated_artifacts_follow_repository_policy(self) -> None:
        self.assertTrue(self.synthesis["metadata"]["generated_outputs_are_reproducible_only"])
        self.assertIn("# MEP Routing Research Final Experimental Report", self.report)
        self.assertIn("## Limitations", self.report)
        self.assertIn("## Threats to Validity", self.report)


if __name__ == "__main__":
    unittest.main()
