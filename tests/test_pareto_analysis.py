"""Focused unit and benchmark regressions for Phase 7 Pareto analysis."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.pareto_analysis import (
    MAXIMIZE,
    MINIMIZE,
    classify_transition,
    dominance_relations,
    dominates,
    non_dominated_methods,
    run_pareto_analysis,
    write_pareto_analysis,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"
CONSTRAINTS = ROOT / "config" / "constructability.json"
COORDINATOR = ROOT / "config" / "coordinator.json"
METHOD_ORDER = ("B0", "B1", "B2", "P-CORE-SEED", "P-CORE-6C2B")


class ExactParetoHelperTests(unittest.TestCase):
    def test_minimization_dominance(self) -> None:
        objectives = (("cost", MINIMIZE), ("bends", MINIMIZE))
        self.assertTrue(dominates({"cost": 1, "bends": 2}, {"cost": 2, "bends": 3}, objectives))

    def test_maximization_dominance(self) -> None:
        objectives = (("coverage", MAXIMIZE),)
        self.assertTrue(dominates({"coverage": 2}, {"coverage": 1}, objectives))

    def test_mixed_directions(self) -> None:
        objectives = (("coverage", MAXIMIZE), ("cost", MINIMIZE))
        self.assertTrue(dominates({"coverage": 3, "cost": 1}, {"coverage": 2, "cost": 2}, objectives))

    def test_equal_vectors_do_not_dominate(self) -> None:
        objectives = (("cost", MINIMIZE), ("coverage", MAXIMIZE))
        vector = {"cost": 1, "coverage": 2}
        self.assertFalse(dominates(vector, vector, objectives))

    def test_one_strict_improvement_with_other_values_equal(self) -> None:
        objectives = (("a", MINIMIZE), ("b", MINIMIZE))
        self.assertTrue(dominates({"a": 1, "b": 2}, {"a": 1, "b": 3}, objectives))

    def test_tradeoff_vectors_are_mutually_non_dominating(self) -> None:
        objectives = (("a", MINIMIZE), ("b", MINIMIZE))
        first, second = {"a": 1, "b": 2}, {"a": 2, "b": 1}
        self.assertFalse(dominates(first, second, objectives))
        self.assertFalse(dominates(second, first, objectives))

    def test_deterministic_ordering(self) -> None:
        objectives = (("value", MINIMIZE),)
        vectors = {"second": {"value": 1}, "first": {"value": 1}, "third": {"value": 2}}
        order = ("first", "second", "third")
        self.assertEqual(["first", "second"], non_dominated_methods(vectors, objectives, order))
        self.assertEqual(["third"], dominance_relations(vectors, objectives, order)["first"]["dominates"])

    def test_helpers_do_not_mutate_inputs(self) -> None:
        objectives = [("coverage", MAXIMIZE), ("cost", MINIMIZE)]
        vectors = {"a": {"coverage": 2, "cost": 2}, "b": {"coverage": 1, "cost": 3}}
        expected_vectors, expected_objectives = deepcopy(vectors), deepcopy(objectives)
        dominance_relations(vectors, objectives, ("a", "b"))
        self.assertEqual(expected_vectors, vectors)
        self.assertEqual(expected_objectives, objectives)

    def test_transition_classifications(self) -> None:
        self.assertEqual("strict_multiobjective_improvement", classify_transition({"a": -1, "b": 0})["classification"])
        self.assertEqual("tradeoff", classify_transition({"a": -1, "b": 1})["classification"])
        self.assertEqual("unchanged", classify_transition({"a": 0, "b": 0})["classification"])
        self.assertEqual("regression", classify_transition({"a": 1, "b": 0})["classification"])


class ParetoBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.analysis = run_pareto_analysis(SCENARIOS, DEMANDS, SYSTEMS, CONSTRAINTS, COORDINATOR)

    def test_cardinality_and_separate_populations(self) -> None:
        self.assertEqual(list(METHOD_ORDER), self.analysis["analysis"]["method_order"])
        self.assertEqual(12, self.analysis["analysis"]["case_count"])
        self.assertEqual(12, len(self.analysis["case_level"]["cases"]))
        self.assertEqual(5, len(self.analysis["global"]["operational"]["methods"]))
        operational_names = [item["name"] for item in self.analysis["global"]["operational"]["objective_definitions"]]
        common_names = [item["name"] for item in self.analysis["global"]["common_success"]["objective_definitions"]]
        self.assertIn("successful_routes", operational_names)
        self.assertNotIn("successful_routes", common_names)
        for row in self.analysis["global"]["common_success"]["methods"]:
            self.assertNotIn("successful_routes", row["objective_vector"])
            self.assertEqual(144, row["context"]["common_successful_routes"])

    def test_checkpoint_and_phase_6c3b_regressions(self) -> None:
        operational = {
            row["method"]: {**row["objective_vector"], **row["context"]}
            for row in self.analysis["global"]["operational"]["methods"]
        }
        self.assertEqual(1256, operational["B0"]["hard_conflicts"])
        self.assertEqual(144, operational["B1"]["successful_routes"])
        self.assertEqual(936, operational["B2"]["hard_conflicts"])
        self.assertEqual(1020, operational["P-CORE-SEED"]["hard_conflicts"])
        self.assertEqual(747, operational["P-CORE-6C2B"]["hard_conflicts"])
        self.assertEqual(926, operational["P-CORE-6C2B"]["clearance_violations"])
        self.assertEqual(16, self.analysis["checkpoint_regression_observations"]["pcore_accepted_rounds"])
        self.assertEqual(144, self.analysis["checkpoint_regression_observations"]["all_method_common_success_routes"])
        transitions = self.analysis["pcore_seed_to_final_transitions"]
        self.assertTrue(transitions["no_case_worsened_hard_conflicts"])
        self.assertTrue(transitions["no_case_worsened_clearance_violations"])
        self.assertEqual(12, sum(transitions["classification_counts"].values()))
        self.assertEqual(["s01", "s02", "s03", "s04"], [row["scenario_id"] for row in self.analysis["scenario_level"]])
        self.assertEqual(["d01", "d02", "d03"], [row["demand_profile_id"] for row in self.analysis["demand_level"]])

    def test_output_is_deterministic_and_byte_identical(self) -> None:
        first_render = json.dumps(self.analysis, indent=2, sort_keys=True) + "\n"
        second_render = json.dumps(self.analysis, indent=2, sort_keys=True) + "\n"
        self.assertEqual(first_render, second_render)
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            write_pareto_analysis(self.analysis, first)
            write_pareto_analysis(self.analysis, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_no_disallowed_interpretive_fields(self) -> None:
        disallowed = {
            "winner", "best_method", "recommended_method", "preferred_method",
            "rank", "ranking", "score", "weighted_score", "utility",
        }

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value)) if value else set()
            return set()

        self.assertTrue(disallowed.isdisjoint(keys(self.analysis)))
        self.assertEqual(
            {"conflict_only", "conflict_geometry", "operational"},
            set(self.analysis["objective_set_sensitivity"]),
        )
        self.assertTrue(self.analysis["integrity_checks"]["comparison_input_unchanged"])
        self.assertTrue(self.analysis["integrity_checks"]["stratified_input_unchanged"])


if __name__ == "__main__":
    unittest.main()
