"""Deterministic Phase 6C3B stratified evidence from the 6C3A harness.

The module only aggregates authoritative comparison outputs.  It neither
routes nor selects a preferred method, score, ranking, or Pareto frontier.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .baseline_comparison import _quality, _route_key, _subset_result, _successful_keys
from .benchmark import load_scenario_results
from .method_comparison import METHOD_ORDER, _conflict_metrics, _conflicts, compare_methods
from .demand import SYSTEM_ORDER


SCENARIO_ORDER = ("s01", "s02", "s03", "s04")
DEMAND_ORDER = ("d01", "d02", "d03")


def _routes(result: dict[str, Any], case_ids: set[str], system: str | None = None) -> list[dict[str, Any]]:
    return [
        route for case in result["cases"] if case["case_id"] in case_ids
        for route in case["routes"] if system is None or route["system"] == system
    ]


def _routing(routes: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [route for route in routes if route["path_found"]]
    return {
        "requested_connections": len(routes), "successful_routes": len(successful),
        "failed_routes": len(routes) - len(successful),
        "connection_success_rate": len(successful) / len(routes) if routes else 0.0,
        "fully_routed_case_count": sum(all(route["path_found"] for route in case["routes"]) for case in cases),
    }


def _aggregate_conflicts(conflicts: dict[str, Any], case_ids: set[str]) -> dict[str, Any]:
    rows = [case for case in conflicts["cases"] if case["case_id"] in case_ids]
    pairs = sum(case["route_pair_count"] for case in rows)
    hard = sum(case["hard_conflicting_route_pair_count"] for case in rows)
    clearance = sum(case["clearance_violating_route_pair_count"] for case in rows)
    return {
        "evaluated_successful_inter_system_route_pairs": pairs,
        "hard_conflicts": hard, "clearance_violations": clearance,
        "hard_conflict_rate": hard / pairs if pairs else 0.0,
        "clearance_violation_rate": clearance / pairs if pairs else 0.0,
    }


def _stratified_rows(results: dict[str, dict[str, Any]], conflicts: dict[str, dict[str, Any]], key: str, order: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for value in order:
        method_rows = {}
        for method in METHOD_ORDER:
            cases = [case for case in results[method]["cases"] if case[key] == value]
            ids = {case["case_id"] for case in cases}
            routes = _routes(results[method], ids)
            method_rows[method] = {
                "routing": _routing(routes, cases),
                "authoritative_conflict_evaluation": _aggregate_conflicts(conflicts[method], ids),
                "route_geometry_quality_successful_routes_only": _quality(routes),
            }
        rows.append({key: value, "methods": method_rows})
    return rows


def _per_system(results: dict[str, dict[str, Any]], conflicts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for system in SYSTEM_ORDER:
        method_rows = {}
        for method in METHOD_ORDER:
            routes = _routes(results[method], {case["case_id"] for case in results[method]["cases"]}, system)
            enriched = [route for case in conflicts[method]["cases"] for route in case["routes"] if route["system"] == system and route["path_found"]]
            successful = [route for route in routes if route["path_found"]]
            method_rows[method] = {
                "attempted_connections": len(routes), "successful_routes": len(successful),
                "failed_routes": len(routes) - len(successful),
                "success_rate": len(successful) / len(routes) if routes else 0.0,
                "route_geometry_quality_successful_routes_only": _quality(routes),
                "inter_system_conflict_burden_partner_incidences": {
                    "hard": sum(route.get("inter_system_hard_conflict_partner_count", 0) for route in enriched),
                    "clearance": sum(route.get("inter_system_clearance_violation_partner_count", 0) for route in enriched),
                    "note": "Pairwise partner incidences are symmetric burden observations, not directional fault attribution.",
                },
            }
        rows.append({"system": system, "methods": method_rows})
    return rows


def _common_keys(results: dict[str, dict[str, Any]]) -> dict[str, set[tuple[str, str]]]:
    cases = {method: {case["case_id"]: case for case in result["cases"]} for method, result in results.items()}
    return {
        case_id: set.intersection(*(_successful_keys(cases[method][case_id]) for method in METHOD_ORDER))
        for case_id in sorted(cases["B0"])
    }


def _common_strata(
    results: dict[str, dict[str, Any]], grids: dict[tuple[str, str], Any], definitions: dict[str, Any],
    keys: dict[str, set[tuple[str, str]]], key: str, order: tuple[str, ...], system: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for value in order:
        selected = {case["case_id"] for case in results["B0"]["cases"] if case[key] == value}
        method_rows = {}
        for method in METHOD_ORDER:
            subset = _subset_result(results[method], keys)
            subset = {"cases": [case for case in subset["cases"] if case["case_id"] in selected]}
            routes = _routes(subset, selected, system)
            if system is not None:
                subset = {"cases": [{**case, "routes": [route for route in case["routes"] if route["system"] == system]} for case in subset["cases"]]}
            conflict = _conflicts(subset, grids, definitions, f"{method}-COMMON-{key}-{value}{'-' + system if system else ''}")
            method_rows[method] = {
                "route_count": len(routes), "authoritative_conflict_evaluation": _conflict_metrics(conflict),
                "route_geometry_quality": _quality(routes),
            }
        rows.append({key: value, "methods": method_rows})
    return rows


def _common_systems(
    results: dict[str, dict[str, Any]], grids: dict[tuple[str, str], Any], definitions: dict[str, Any],
    keys: dict[str, set[tuple[str, str]]],
) -> list[dict[str, Any]]:
    rows = []
    for system in SYSTEM_ORDER:
        method_rows = {}
        for method in METHOD_ORDER:
            subset = _subset_result(results[method], keys)
            subset = {"cases": [{**case, "routes": [route for route in case["routes"] if route["system"] == system]} for case in subset["cases"]]}
            routes = [route for case in subset["cases"] for route in case["routes"]]
            conflict = _conflicts(subset, grids, definitions, f"{method}-COMMON-SYSTEM-{system}")
            method_rows[method] = {"route_count": len(routes), "authoritative_conflict_evaluation": _conflict_metrics(conflict), "route_geometry_quality": _quality(routes)}
        rows.append({"system": system, "methods": method_rows})
    return rows


def _case_metrics(result_case: dict[str, Any], conflict_case: dict[str, Any]) -> dict[str, Any]:
    routes = result_case["routes"]
    return {
        "successful_routes": sum(route["path_found"] for route in routes),
        "evaluated_pairs": conflict_case["route_pair_count"],
        "hard_conflicts": conflict_case["hard_conflicting_route_pair_count"],
        "clearance_violations": conflict_case["clearance_violating_route_pair_count"],
        "routed_length_m": _quality(routes)["total_grid_route_length_m"],
        "bend_count": _quality(routes)["total_bend_count"],
        "vertical_travel_m": _quality(routes)["total_vertical_travel_m"],
    }


def _pcore_decomposition(results: dict[str, dict[str, Any]], conflicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    seed_conflicts = {case["case_id"]: case for case in conflicts["P-CORE-SEED"]["cases"]}
    final_conflicts = {case["case_id"]: case for case in conflicts["P-CORE-6C2B"]["cases"]}
    final_cases = {case["case_id"]: case for case in results["P-CORE-6C2B"]["cases"]}
    states = {case["case_id"]: state["summary"] for case, state in zip(results["P-CORE-6C2B"]["cases"], results["P-CORE-6C2B"]["coordination_results"])}
    rows = []
    for case_id in sorted(final_cases):
        seed, final, state = seed_conflicts[case_id], final_conflicts[case_id], states[case_id]
        rows.append({
            "case_id": case_id, "scenario_id": final_cases[case_id]["scenario_id"], "demand_profile_id": final_cases[case_id]["demand_profile_id"],
            "initial_hard_conflicts": seed["hard_conflicting_route_pair_count"], "final_hard_conflicts": final["hard_conflicting_route_pair_count"],
            "hard_conflict_delta": final["hard_conflicting_route_pair_count"] - seed["hard_conflicting_route_pair_count"],
            "initial_clearance_violations": seed["clearance_violating_route_pair_count"], "final_clearance_violations": final["clearance_violating_route_pair_count"],
            "clearance_delta": final["clearance_violating_route_pair_count"] - seed["clearance_violating_route_pair_count"],
            "accepted_repair_rounds": state["rounds_accepted"], "rolled_back_rounds": state["rounds_rolled_back"], "final_stop_reason": state["final_stop_reason"],
            "route_length_delta_m": state["final_total_routed_length_m"] - state["initial_total_routed_length_m"],
            "bend_count_delta": state["final_total_bend_count"] - state["initial_total_bend_count"],
            "vertical_travel_delta_m": state["final_total_vertical_travel_m"] - state["initial_total_vertical_travel_m"],
        })
    metrics = ("hard_conflict_delta", "clearance_delta", "route_length_delta_m", "bend_count_delta", "vertical_travel_delta_m")
    def aggregate(group: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "totals": {field: sum(row[field] for row in group) for field in ("initial_hard_conflicts", "final_hard_conflicts", "hard_conflict_delta", "initial_clearance_violations", "final_clearance_violations", "clearance_delta", "accepted_repair_rounds", "rolled_back_rounds", "route_length_delta_m", "bend_count_delta", "vertical_travel_delta_m")},
            "directional_counts": {metric: {"decreased": sum(row[metric] < 0 for row in group), "equal": sum(row[metric] == 0 for row in group), "increased": sum(row[metric] > 0 for row in group)} for metric in metrics},
            "final_stop_reason_counts": {reason: sum(row["final_stop_reason"] == reason for row in group) for reason in sorted({row["final_stop_reason"] for row in group})},
        }
    return {"cases": rows, "directional_counts_all_cases": aggregate(rows),
            "by_scenario": [{"scenario_id": value, "aggregate": aggregate([row for row in rows if row["scenario_id"] == value])} for value in SCENARIO_ORDER],
            "by_demand_profile": [{"demand_profile_id": value, "aggregate": aggregate([row for row in rows if row["demand_profile_id"] == value])} for value in DEMAND_ORDER]}


def analyze_comparison(comparison: dict[str, Any], scenarios: Path) -> dict[str, Any]:
    """Create analysis from an extended 6C3A result without modifying it."""
    snapshot = deepcopy(comparison)
    source = comparison["_analysis_source"]
    results, conflicts = source["results"], source["conflicts"]
    case_rows = []
    conflict_cases = {method: {case["case_id"]: case for case in conflicts[method]["cases"]} for method in METHOD_ORDER}
    pcore_states = {case["case_id"]: state["summary"] for case, state in zip(results["P-CORE-6C2B"]["cases"], results["P-CORE-6C2B"]["coordination_results"])}
    for case in results["B0"]["cases"]:
        case_id = case["case_id"]
        row = {"case_id": case_id, "scenario_id": case["scenario_id"], "demand_profile_id": case["demand_profile_id"], "methods": {}}
        for method in METHOD_ORDER:
            method_case = next(item for item in results[method]["cases"] if item["case_id"] == case_id)
            row["methods"][method] = _case_metrics(method_case, conflict_cases[method][case_id])
        row["P-CORE-6C2B_coordination"] = {"accepted_rounds": pcore_states[case_id]["rounds_accepted"], "final_stop_reason": pcore_states[case_id]["final_stop_reason"]}
        case_rows.append(row)
    keys = _common_keys(results)
    analysis = {
        "analysis": {"phase": "6C3B", "method_order": list(METHOD_ORDER), "scenario_order": list(SCENARIO_ORDER), "demand_profile_order": list(DEMAND_ORDER), "system_order": list(SYSTEM_ORDER), "case_count": len(case_rows), "no_ranking_scoring_or_pareto_analysis": True},
        "scenario_stratified": _stratified_rows(results, conflicts, "scenario_id", SCENARIO_ORDER),
        "demand_stratified": _stratified_rows(results, conflicts, "demand_profile_id", DEMAND_ORDER),
        "scenario_geometric_benchmark": load_scenario_results(scenarios),
        "per_system": _per_system(results, conflicts),
        "coverage_vs_conflict_visibility": [{"method": method, **comparison["methods"][method]["metrics"]["routing"], **comparison["methods"][method]["metrics"]["authoritative_conflict_evaluation"]} for method in METHOD_ORDER],
        "all_method_common_success": {"denominator_explanation": comparison["all_method_common_success_subset"]["denominator_explanation"], "scenario_stratified": _common_strata(results, source["grids"], source["definitions"], keys, "scenario_id", SCENARIO_ORDER), "demand_stratified": _common_strata(results, source["grids"], source["definitions"], keys, "demand_profile_id", DEMAND_ORDER), "system_stratified": _common_systems(results, source["grids"], source["definitions"], keys)},
        "pcore_seed_to_final_decomposition": _pcore_decomposition(results, conflicts),
        "case_level_evidence": case_rows,
        "integrity_checks": {"comparison_result_unchanged": comparison == snapshot, "base_occupancy_grids_unchanged": comparison["integrity_checks"]["base_occupancy_grids_unchanged"], "scenario_demand_and_config_inputs_unchanged": comparison["integrity_checks"]["scenario_demand_and_config_inputs_unchanged"]},
    }
    if comparison != snapshot:
        raise RuntimeError("Comparison analysis must not mutate the Phase 6C3A result.")
    return analysis


def run_comparison_analysis(scenarios: Path, demands: Path, systems: Path, constraints: Path, coordinator: Path) -> dict[str, Any]:
    comparison = compare_methods(scenarios, demands, systems, constraints, coordinator, include_analysis_source=True)
    return analyze_comparison(comparison, scenarios)


def write_comparison_analysis(analysis: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic stratified MEP comparison evidence.")
    for name in ("scenarios", "demands", "systems", "constraints", "coordinator", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    write_comparison_analysis(run_comparison_analysis(args.scenarios, args.demands, args.systems, args.constraints, args.coordinator), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
