"""Deterministic exact Pareto analysis of the existing five-method benchmark.

This module consumes Phase 6C3A/6C3B evidence.  It does not route, alter any
benchmark semantics, combine objectives into a scalar, or select a method.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .baseline_comparison import _quality, _subset_result
from .comparison_analysis import (
    DEMAND_ORDER,
    SCENARIO_ORDER,
    _common_keys,
    analyze_comparison,
)
from .method_comparison import METHOD_ORDER, _conflict_metrics, _conflicts, compare_methods


MINIMIZE = "minimize"
MAXIMIZE = "maximize"

OPERATIONAL_OBJECTIVES = (
    ("successful_routes", MAXIMIZE),
    ("hard_conflicts", MINIMIZE),
    ("clearance_violations", MINIMIZE),
    ("routed_length_m", MINIMIZE),
    ("bend_count", MINIMIZE),
    ("vertical_travel_m", MINIMIZE),
)
COMMON_SUCCESS_OBJECTIVES = OPERATIONAL_OBJECTIVES[1:]
CONFLICT_ONLY_OBJECTIVES = COMMON_SUCCESS_OBJECTIVES[:2]


def dominates(
    vector_a: Mapping[str, int | float],
    vector_b: Mapping[str, int | float],
    objectives: Sequence[tuple[str, str]],
) -> bool:
    """Return exact Pareto dominance under explicit objective directions."""
    no_worse = True
    strictly_better = False
    for name, direction in objectives:
        if direction == MINIMIZE:
            no_worse = no_worse and vector_a[name] <= vector_b[name]
            strictly_better = strictly_better or vector_a[name] < vector_b[name]
        elif direction == MAXIMIZE:
            no_worse = no_worse and vector_a[name] >= vector_b[name]
            strictly_better = strictly_better or vector_a[name] > vector_b[name]
        else:
            raise ValueError(f"Unsupported objective direction for {name}: {direction}")
    return no_worse and strictly_better


def dominance_relations(
    vectors: Mapping[str, Mapping[str, int | float]],
    objectives: Sequence[tuple[str, str]],
    method_order: Iterable[str],
) -> dict[str, dict[str, list[str]]]:
    """Build deterministic pairwise dominance relations without mutation."""
    order = tuple(method_order)
    return {
        method: {
            "dominates": [other for other in order if other != method and dominates(vectors[method], vectors[other], objectives)],
            "dominated_by": [other for other in order if other != method and dominates(vectors[other], vectors[method], objectives)],
        }
        for method in order
    }


def non_dominated_methods(
    vectors: Mapping[str, Mapping[str, int | float]],
    objectives: Sequence[tuple[str, str]],
    method_order: Iterable[str],
) -> list[str]:
    relations = dominance_relations(vectors, objectives, method_order)
    return [method for method in method_order if not relations[method]["dominated_by"]]


def _objective_definitions(objectives: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": name, "direction": direction} for name, direction in objectives]


def _pareto_view(
    metrics: Mapping[str, Mapping[str, int | float]],
    objectives: Sequence[tuple[str, str]],
    *,
    context_fields: Sequence[str] = (),
) -> dict[str, Any]:
    vectors = {
        method: {name: metrics[method][name] for name, _ in objectives}
        for method in METHOD_ORDER
    }
    relations = dominance_relations(vectors, objectives, METHOD_ORDER)
    rows = []
    for method in METHOD_ORDER:
        row: dict[str, Any] = {
            "method": method,
            "objective_vector": vectors[method],
            **relations[method],
        }
        if context_fields:
            row["context"] = {name: metrics[method][name] for name in context_fields}
        rows.append(row)
    return {
        "objective_definitions": _objective_definitions(objectives),
        "methods": rows,
        "non_dominated_methods": [method for method in METHOD_ORDER if not relations[method]["dominated_by"]],
    }


def classify_transition(deltas: Mapping[str, int | float]) -> dict[str, Any]:
    """Classify a minimization transition from exact metric deltas."""
    directions = {
        name: "improved" if value < 0 else "worsened" if value > 0 else "unchanged"
        for name, value in deltas.items()
    }
    has_improvement = "improved" in directions.values()
    has_worsening = "worsened" in directions.values()
    if has_improvement and not has_worsening:
        classification = "strict_multiobjective_improvement"
    elif has_improvement and has_worsening:
        classification = "tradeoff"
    elif not has_improvement and not has_worsening:
        classification = "unchanged"
    else:
        classification = "regression"
    return {"metric_directions": directions, "classification": classification}


def _operational_global_metrics(comparison: Mapping[str, Any]) -> dict[str, dict[str, int | float]]:
    rows = {}
    for method in METHOD_ORDER:
        metrics = comparison["methods"][method]["metrics"]
        routing = metrics["routing"]
        conflicts = metrics["authoritative_conflict_evaluation"]
        geometry = metrics["route_geometry_quality_successful_routes_only"]
        rows[method] = {
            "requested_routes": routing["total_requested_connections"],
            "successful_routes": routing["successful_routes"],
            "evaluated_successful_inter_system_pairs": conflicts["evaluated_inter_system_successful_route_pairs"],
            "hard_conflicts": conflicts["hard_conflicting_route_pairs"],
            "clearance_violations": conflicts["clearance_violating_route_pairs"],
            "routed_length_m": geometry["total_grid_route_length_m"],
            "bend_count": geometry["total_bend_count"],
            "vertical_travel_m": geometry["total_vertical_travel_m"],
        }
    return rows


def _common_global_metrics(comparison: Mapping[str, Any]) -> dict[str, dict[str, int | float]]:
    rows = {}
    for method in METHOD_ORDER:
        metrics = comparison["all_method_common_success_subset"]["methods"][method]
        conflicts = metrics["authoritative_conflict_evaluation"]
        geometry = metrics["route_geometry_quality"]
        rows[method] = {
            "common_successful_routes": metrics["common_successful_connection_count"],
            "evaluated_successful_inter_system_pairs": conflicts["evaluated_inter_system_successful_route_pairs"],
            "hard_conflicts": conflicts["hard_conflicting_route_pairs"],
            "clearance_violations": conflicts["clearance_violating_route_pairs"],
            "routed_length_m": geometry["total_grid_route_length_m"],
            "bend_count": geometry["total_bend_count"],
            "vertical_travel_m": geometry["total_vertical_travel_m"],
        }
    return rows


def _operational_stratum_metrics(row: Mapping[str, Any]) -> dict[str, dict[str, int | float]]:
    metrics = {}
    for method in METHOD_ORDER:
        source = row["methods"][method]
        routing = source["routing"]
        conflicts = source["authoritative_conflict_evaluation"]
        geometry = source["route_geometry_quality_successful_routes_only"]
        metrics[method] = {
            "requested_routes": routing["requested_connections"],
            "successful_routes": routing["successful_routes"],
            "evaluated_successful_inter_system_pairs": conflicts["evaluated_successful_inter_system_route_pairs"],
            "hard_conflicts": conflicts["hard_conflicts"],
            "clearance_violations": conflicts["clearance_violations"],
            "routed_length_m": geometry["total_grid_route_length_m"],
            "bend_count": geometry["total_bend_count"],
            "vertical_travel_m": geometry["total_vertical_travel_m"],
        }
    return metrics


def _common_stratum_metrics(row: Mapping[str, Any]) -> dict[str, dict[str, int | float]]:
    metrics = {}
    for method in METHOD_ORDER:
        source = row["methods"][method]
        conflicts = source["authoritative_conflict_evaluation"]
        geometry = source["route_geometry_quality"]
        metrics[method] = {
            "common_successful_routes": source["route_count"],
            "evaluated_successful_inter_system_pairs": conflicts["evaluated_inter_system_successful_route_pairs"],
            "hard_conflicts": conflicts["hard_conflicting_route_pairs"],
            "clearance_violations": conflicts["clearance_violating_route_pairs"],
            "routed_length_m": geometry["total_grid_route_length_m"],
            "bend_count": geometry["total_bend_count"],
            "vertical_travel_m": geometry["total_vertical_travel_m"],
        }
    return metrics


def _case_common_metrics(comparison: Mapping[str, Any]) -> dict[str, dict[str, dict[str, int | float]]]:
    source = comparison["_analysis_source"]
    results = source["results"]
    keys = _common_keys(results)
    metrics_by_case: dict[str, dict[str, dict[str, int | float]]] = {case_id: {} for case_id in sorted(keys)}
    for method in METHOD_ORDER:
        subset = _subset_result(results[method], keys)
        conflict_result = _conflicts(subset, source["grids"], source["definitions"], f"{method}-PHASE7-COMMON")
        conflict_cases = {case["case_id"]: case for case in conflict_result["cases"]}
        for case in subset["cases"]:
            case_id = case["case_id"]
            geometry = _quality(case["routes"])
            conflict = conflict_cases[case_id]
            metrics_by_case[case_id][method] = {
                "common_successful_routes": len(case["routes"]),
                "evaluated_successful_inter_system_pairs": conflict["route_pair_count"],
                "hard_conflicts": conflict["hard_conflicting_route_pair_count"],
                "clearance_violations": conflict["clearance_violating_route_pair_count"],
                "routed_length_m": geometry["total_grid_route_length_m"],
                "bend_count": geometry["total_bend_count"],
                "vertical_travel_m": geometry["total_vertical_travel_m"],
            }
    return metrics_by_case


def _case_operational_metrics(case_row: Mapping[str, Any], requested: int) -> dict[str, dict[str, int | float]]:
    return {
        method: {
            "requested_routes": requested,
            "successful_routes": case_row["methods"][method]["successful_routes"],
            "evaluated_successful_inter_system_pairs": case_row["methods"][method]["evaluated_pairs"],
            "hard_conflicts": case_row["methods"][method]["hard_conflicts"],
            "clearance_violations": case_row["methods"][method]["clearance_violations"],
            "routed_length_m": case_row["methods"][method]["routed_length_m"],
            "bend_count": case_row["methods"][method]["bend_count"],
            "vertical_travel_m": case_row["methods"][method]["vertical_travel_m"],
        }
        for method in METHOD_ORDER
    }


def _transition_rows(case_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = ("hard_conflicts", "clearance_violations", "routed_length_m", "bend_count", "vertical_travel_m")
    rows = []
    for case in case_rows:
        seed = case["methods"]["P-CORE-SEED"]
        final = case["methods"]["P-CORE-6C2B"]
        deltas = {name: final[name] - seed[name] for name in fields}
        state = classify_transition(deltas)
        rows.append({
            "case_id": case["case_id"],
            "scenario_id": case["scenario_id"],
            "demand_profile_id": case["demand_profile_id"],
            "deltas": deltas,
            **state,
        })
    labels = ("strict_multiobjective_improvement", "tradeoff", "unchanged", "regression")
    return {
        "cases": rows,
        "classification_counts": {label: sum(row["classification"] == label for row in rows) for label in labels},
        "no_case_worsened_hard_conflicts": all(row["deltas"]["hard_conflicts"] <= 0 for row in rows),
        "no_case_worsened_clearance_violations": all(row["deltas"]["clearance_violations"] <= 0 for row in rows),
    }


def analyze_pareto(comparison: dict[str, Any], stratified: dict[str, Any]) -> dict[str, Any]:
    """Derive Phase 7 evidence without mutating either supplied structure."""
    comparison_snapshot = deepcopy(comparison)
    stratified_snapshot = deepcopy(stratified)
    operational_global = _operational_global_metrics(comparison)
    common_global = _common_global_metrics(comparison)
    common_cases = _case_common_metrics(comparison)
    source_cases = {case["case_id"]: case for case in comparison["_analysis_source"]["results"]["B0"]["cases"]}

    case_rows = []
    operational_frequency = {method: 0 for method in METHOD_ORDER}
    common_frequency = {method: 0 for method in METHOD_ORDER}
    for case in stratified["case_level_evidence"]:
        requested = len(source_cases[case["case_id"]]["routes"])
        operational = _pareto_view(
            _case_operational_metrics(case, requested),
            OPERATIONAL_OBJECTIVES,
            context_fields=("requested_routes", "evaluated_successful_inter_system_pairs"),
        )
        common = _pareto_view(
            common_cases[case["case_id"]],
            COMMON_SUCCESS_OBJECTIVES,
            context_fields=("common_successful_routes", "evaluated_successful_inter_system_pairs"),
        )
        for method in operational["non_dominated_methods"]:
            operational_frequency[method] += 1
        for method in common["non_dominated_methods"]:
            common_frequency[method] += 1
        case_rows.append({
            "case_id": case["case_id"],
            "scenario_id": case["scenario_id"],
            "demand_profile_id": case["demand_profile_id"],
            "operational": operational,
            "common_success": common,
        })

    def strata(section: str, common_section: str, key: str, order: Sequence[str]) -> list[dict[str, Any]]:
        operational_rows = {row[key]: row for row in stratified[section]}
        common_rows = {row[key]: row for row in stratified["all_method_common_success"][common_section]}
        return [{
            key: value,
            "operational": _pareto_view(
                _operational_stratum_metrics(operational_rows[value]),
                OPERATIONAL_OBJECTIVES,
                context_fields=("requested_routes", "evaluated_successful_inter_system_pairs"),
            ),
            "common_success": _pareto_view(
                _common_stratum_metrics(common_rows[value]),
                COMMON_SUCCESS_OBJECTIVES,
                context_fields=("common_successful_routes", "evaluated_successful_inter_system_pairs"),
            ),
        } for value in order]

    result = {
        "analysis": {
            "phase": "7",
            "method_order": list(METHOD_ORDER),
            "case_count": len(case_rows),
            "dominance_definition": "A is no worse in every included objective and strictly better in at least one included objective.",
            "uses_exact_stored_values_without_tolerance_or_normalization": True,
            "operational_and_common_success_populations_are_separate": True,
        },
        "global": {
            "operational": _pareto_view(
                operational_global,
                OPERATIONAL_OBJECTIVES,
                context_fields=("requested_routes", "evaluated_successful_inter_system_pairs"),
            ),
            "common_success": _pareto_view(
                common_global,
                COMMON_SUCCESS_OBJECTIVES,
                context_fields=("common_successful_routes", "evaluated_successful_inter_system_pairs"),
            ),
        },
        "case_level": {
            "cases": case_rows,
            "non_dominated_frequency_counts": {
                "operational": operational_frequency,
                "common_success": common_frequency,
            },
        },
        "scenario_level": strata("scenario_stratified", "scenario_stratified", "scenario_id", SCENARIO_ORDER),
        "demand_level": strata("demand_stratified", "demand_stratified", "demand_profile_id", DEMAND_ORDER),
        "pcore_seed_to_final_transitions": _transition_rows(stratified["case_level_evidence"]),
        "objective_set_sensitivity": {
            "conflict_only": {
                "population": "operational",
                **_pareto_view(operational_global, CONFLICT_ONLY_OBJECTIVES),
            },
            "conflict_geometry": {
                "population": "all_method_common_success",
                **_pareto_view(
                    common_global,
                    COMMON_SUCCESS_OBJECTIVES,
                    context_fields=("common_successful_routes", "evaluated_successful_inter_system_pairs"),
                ),
            },
            "operational": {
                "population": "actual_method_outputs",
                **_pareto_view(
                    operational_global,
                    OPERATIONAL_OBJECTIVES,
                    context_fields=("requested_routes", "evaluated_successful_inter_system_pairs"),
                ),
            },
        },
        "checkpoint_regression_observations": {
            "pcore_accepted_rounds": comparison["pcore_coordination_diagnostics"]["accepted_rounds"],
            "all_method_common_success_routes": common_global["B0"]["common_successful_routes"],
        },
        "integrity_checks": {
            "comparison_input_unchanged": comparison == comparison_snapshot,
            "stratified_input_unchanged": stratified == stratified_snapshot,
            "base_occupancy_grids_unchanged": comparison["integrity_checks"]["base_occupancy_grids_unchanged"],
            "scenario_demand_and_config_inputs_unchanged": comparison["integrity_checks"]["scenario_demand_and_config_inputs_unchanged"],
        },
    }
    if comparison != comparison_snapshot or stratified != stratified_snapshot:
        raise RuntimeError("Pareto analysis must not mutate Phase 6C3 inputs.")
    return result


def run_pareto_analysis(
    scenarios: Path,
    demands: Path,
    systems: Path,
    constraints: Path,
    coordinator: Path,
) -> dict[str, Any]:
    comparison = compare_methods(
        scenarios, demands, systems, constraints, coordinator, include_analysis_source=True
    )
    stratified = analyze_comparison(comparison, scenarios)
    return analyze_pareto(comparison, stratified)


def write_pareto_analysis(analysis: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic exact Pareto analysis for the five existing methods.")
    for name in ("scenarios", "demands", "systems", "constraints", "coordinator"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/phase7_pareto_analysis.json"),
    )
    args = parser.parse_args()
    write_pareto_analysis(
        run_pareto_analysis(args.scenarios, args.demands, args.systems, args.constraints, args.coordinator),
        args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
