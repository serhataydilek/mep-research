"""Phase 8 deterministic synthesis of the completed MEP research benchmark.

The module composes existing Phase 6C3A, 6C3B, and Phase 7 outputs.  It adds no
routing, evaluation, coordination, or optimization behavior.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .comparison_analysis import DEMAND_ORDER, SCENARIO_ORDER, analyze_comparison
from .demand import SYSTEM_ORDER
from .method_comparison import METHOD_ORDER, compare_methods
from .pareto_analysis import analyze_pareto


CHECKPOINTS = {
    "B0_hard_conflicts": 1256,
    "B1_successful_routes": 144,
    "B2_hard_conflicts": 936,
    "P-CORE-SEED_hard_conflicts": 1020,
    "P-CORE-6C2B_hard_conflicts": 747,
    "P-CORE-6C2B_clearance_violations": 926,
    "P-CORE-6C2B_accepted_rounds": 16,
    "all_method_common_success_routes": 144,
}


def percentage_change(seed: int | float, final: int | float) -> dict[str, Any]:
    """Describe an exact final-minus-seed change with an explicit denominator."""
    delta = final - seed
    return {
        "seed": seed,
        "final": final,
        "delta": delta,
        "percentage_change": None if seed == 0 else delta / seed * 100,
        "percentage_denominator": "seed",
        "zero_denominator": seed == 0,
    }


def _global_metrics(comparison: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for method in METHOD_ORDER:
        metrics = comparison["methods"][method]["metrics"]
        routing = metrics["routing"]
        conflicts = metrics["authoritative_conflict_evaluation"]
        geometry = metrics["route_geometry_quality_successful_routes_only"]
        row: dict[str, Any] = {
            "method": method,
            "requested_routes": routing["total_requested_connections"],
            "successful_routes": routing["successful_routes"],
            "failed_routes": routing["failed_routes"],
            "evaluated_successful_inter_system_pairs": conflicts["evaluated_inter_system_successful_route_pairs"],
            "hard_conflicts": conflicts["hard_conflicting_route_pairs"],
            "clearance_violations": conflicts["clearance_violating_route_pairs"],
            "total_routed_length_m": geometry["total_grid_route_length_m"],
            "total_bends": geometry["total_bend_count"],
            "total_vertical_travel_m": geometry["total_vertical_travel_m"],
            "benchmark_hard_feasible_C0_cases": metrics["constructability_C0"]["benchmark_hard_feasible_C0_case_count"],
        }
        if method == "P-CORE-6C2B":
            diagnostics = comparison["pcore_coordination_diagnostics"]
            row["coordination"] = {
                "attempted_rounds": diagnostics["attempted_rounds"],
                "accepted_rounds": diagnostics["accepted_rounds"],
                "rolled_back_rounds": diagnostics["rolled_back_rounds"],
                "stop_reason_counts": diagnostics["stop_reasons"],
                "selected_repair_candidates": diagnostics["selected_repair_candidate_count"],
                "successful_reroutes": diagnostics["successful_reroute_count"],
            }
        rows.append(row)
    return rows


def _stratum_summary(rows: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        methods = []
        for method in METHOD_ORDER:
            source = row["methods"][method]
            routing = source["routing"]
            conflicts = source["authoritative_conflict_evaluation"]
            geometry = source["route_geometry_quality_successful_routes_only"]
            methods.append({
                "method": method,
                "requested_routes": routing["requested_connections"],
                "successful_routes": routing["successful_routes"],
                "failed_routes": routing["failed_routes"],
                "evaluated_successful_inter_system_pairs": conflicts["evaluated_successful_inter_system_route_pairs"],
                "hard_conflicts": conflicts["hard_conflicts"],
                "clearance_violations": conflicts["clearance_violations"],
                "total_routed_length_m": geometry["total_grid_route_length_m"],
                "total_bends": geometry["total_bend_count"],
                "total_vertical_travel_m": geometry["total_vertical_travel_m"],
            })
        output.append({key: row[key], "methods": methods})
    return output


def _system_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        methods = []
        for method in METHOD_ORDER:
            source = row["methods"][method]
            geometry = source["route_geometry_quality_successful_routes_only"]
            burden = source["inter_system_conflict_burden_partner_incidences"]
            methods.append({
                "method": method,
                "attempted_routes": source["attempted_connections"],
                "successful_routes": source["successful_routes"],
                "failed_routes": source["failed_routes"],
                "hard_conflict_partner_incidences": burden["hard"],
                "clearance_violation_partner_incidences": burden["clearance"],
                "total_routed_length_m": geometry["total_grid_route_length_m"],
                "total_bends": geometry["total_bend_count"],
                "total_vertical_travel_m": geometry["total_vertical_travel_m"],
            })
        output.append({"system": row["system"], "methods": methods})
    return output


def _pareto_summary(pareto: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "global_operational_non_dominated_methods": pareto["global"]["operational"]["non_dominated_methods"],
        "global_common_success_non_dominated_methods": pareto["global"]["common_success"]["non_dominated_methods"],
        "case_non_dominated_frequency_counts": pareto["case_level"]["non_dominated_frequency_counts"],
        "scenario_non_dominated_methods": [
            {
                "scenario_id": row["scenario_id"],
                "operational": row["operational"]["non_dominated_methods"],
                "common_success": row["common_success"]["non_dominated_methods"],
            }
            for row in pareto["scenario_level"]
        ],
        "demand_non_dominated_methods": [
            {
                "demand_profile_id": row["demand_profile_id"],
                "operational": row["operational"]["non_dominated_methods"],
                "common_success": row["common_success"]["non_dominated_methods"],
            }
            for row in pareto["demand_level"]
        ],
        "objective_set_sensitivity": [
            {
                "objective_set": name,
                "population": pareto["objective_set_sensitivity"][name]["population"],
                "included_objectives": pareto["objective_set_sensitivity"][name]["objective_definitions"],
                "non_dominated_methods": pareto["objective_set_sensitivity"][name]["non_dominated_methods"],
            }
            for name in ("conflict_only", "conflict_geometry", "operational")
        ],
        "pcore_transition_classification_counts": pareto["pcore_seed_to_final_transitions"]["classification_counts"],
    }


def _validate_checkpoints(global_rows: Sequence[Mapping[str, Any]], comparison: Mapping[str, Any]) -> None:
    methods = {row["method"]: row for row in global_rows}
    observed = {
        "B0_hard_conflicts": methods["B0"]["hard_conflicts"],
        "B1_successful_routes": methods["B1"]["successful_routes"],
        "B2_hard_conflicts": methods["B2"]["hard_conflicts"],
        "P-CORE-SEED_hard_conflicts": methods["P-CORE-SEED"]["hard_conflicts"],
        "P-CORE-6C2B_hard_conflicts": methods["P-CORE-6C2B"]["hard_conflicts"],
        "P-CORE-6C2B_clearance_violations": methods["P-CORE-6C2B"]["clearance_violations"],
        "P-CORE-6C2B_accepted_rounds": methods["P-CORE-6C2B"]["coordination"]["accepted_rounds"],
        "all_method_common_success_routes": comparison["all_method_common_success_subset"]["methods"]["B0"]["common_successful_connection_count"],
    }
    if observed != CHECKPOINTS:
        raise RuntimeError(f"Final synthesis checkpoint regression: expected {CHECKPOINTS}, observed {observed}")


def _findings(global_rows: Sequence[Mapping[str, Any]], pareto: Mapping[str, Any]) -> list[dict[str, Any]]:
    methods = {row["method"]: row for row in global_rows}
    seed, final = methods["P-CORE-SEED"], methods["P-CORE-6C2B"]
    transition_counts = pareto["pcore_seed_to_final_transitions"]["classification_counts"]
    return [
        {
            "finding": "routing_coverage_and_conflict_visibility",
            "evidence": {
                "B1_successful_routes": methods["B1"]["successful_routes"],
                "B1_requested_routes": methods["B1"]["requested_routes"],
                "B1_evaluated_successful_inter_system_pairs": methods["B1"]["evaluated_successful_inter_system_pairs"],
                "B1_hard_conflicts": methods["B1"]["hard_conflicts"],
                "complete_routing_method_successful_routes": methods["B0"]["successful_routes"],
            },
            "scope": "The fixed 12-case synthetic benchmark and the shared conflict evaluator.",
            "interpretation": "B1's zero observed conflicts must be read together with its reduced route completion and smaller evaluated-pair population.",
        },
        {
            "finding": "pcore_conflict_reduction",
            "evidence": {
                "seed_successful_routes": seed["successful_routes"],
                "final_successful_routes": final["successful_routes"],
                "hard_conflicts_seed": seed["hard_conflicts"],
                "hard_conflicts_final": final["hard_conflicts"],
                "clearance_violations_seed": seed["clearance_violations"],
                "clearance_violations_final": final["clearance_violations"],
            },
            "scope": "P-CORE-SEED and P-CORE-6C2B on the established benchmark.",
            "interpretation": "The coordination rounds reduced both conflict measures without reducing route completion in this benchmark.",
        },
        {
            "finding": "pcore_geometry_tradeoff",
            "evidence": transition_counts,
            "scope": "Exact five-metric seed-to-final vectors for the 12 benchmark cases.",
            "interpretation": "Conflict reduction was usually accompanied by worsening in at least one tracked geometry metric; one case was unchanged.",
        },
        {
            "finding": "pareto_objective_sensitivity",
            "evidence": {
                "conflict_only": pareto["objective_set_sensitivity"]["conflict_only"]["non_dominated_methods"],
                "conflict_geometry": pareto["objective_set_sensitivity"]["conflict_geometry"]["non_dominated_methods"],
                "operational": pareto["objective_set_sensitivity"]["operational"]["non_dominated_methods"],
            },
            "scope": "The implemented methods, populations, and exact Phase 7 objective definitions.",
            "interpretation": "Pareto membership depends materially on the included objectives and comparison population.",
        },
        {
            "finding": "benchmark_C0_feasibility",
            "evidence": {row["method"]: row["benchmark_hard_feasible_C0_cases"] for row in global_rows},
            "scope": "The project-specific C0 benchmark definition across 12 cases.",
            "interpretation": "No implemented method produced a complete hard-feasible C0 case under the current benchmark definition.",
        },
    ]


def synthesize_final(
    comparison: dict[str, Any],
    stratified: dict[str, Any],
    pareto: dict[str, Any],
) -> dict[str, Any]:
    """Create the final compact evidence artifact without mutating inputs."""
    comparison_snapshot = deepcopy(comparison)
    stratified_snapshot = deepcopy(stratified)
    pareto_snapshot = deepcopy(pareto)
    global_rows = _global_metrics(comparison)
    _validate_checkpoints(global_rows, comparison)
    methods = {row["method"]: row for row in global_rows}
    seed, final = methods["P-CORE-SEED"], methods["P-CORE-6C2B"]
    pcore_delta = {
        "hard_conflicts": percentage_change(seed["hard_conflicts"], final["hard_conflicts"]),
        "clearance_violations": percentage_change(seed["clearance_violations"], final["clearance_violations"]),
        "total_routed_length_m": percentage_change(seed["total_routed_length_m"], final["total_routed_length_m"]),
        "total_bends": percentage_change(seed["total_bends"], final["total_bends"]),
        "total_vertical_travel_m": percentage_change(seed["total_vertical_travel_m"], final["total_vertical_travel_m"]),
    }
    result = {
        "metadata": {
            "phase": "8",
            "artifact": "final_experiment_synthesis",
            "method_order": list(METHOD_ORDER),
            "scenario_order": list(SCENARIO_ORDER),
            "demand_order": list(DEMAND_ORDER),
            "system_order": list(SYSTEM_ORDER),
            "source_phases": ["6C3A", "6C3B", "7"],
            "generated_outputs_are_reproducible_only": True,
            "unsupported_causal_claims_excluded_from_stratified_evidence": True,
        },
        "research_question": "How do the implemented deterministic MEP routing and coordination methods compare across routing coverage, conflict burden, route geometry, and exact multi-objective trade-offs in the controlled benchmark?",
        "benchmark_scope": {
            "architectural_scenarios": 4,
            "demand_profiles": 3,
            "benchmark_cases": comparison["comparison"]["case_count"],
            "requested_routes": methods["B0"]["requested_routes"],
            "common_success_routes": comparison["all_method_common_success_subset"]["methods"]["B0"]["common_successful_connection_count"],
            "geometry": "controlled synthetic architectural and structural benchmark geometry",
        },
        "methods": [
            {"method": method, "source_metadata": comparison["methods"][method]["method"]}
            for method in METHOD_ORDER
        ],
        "global_metrics": global_rows,
        "pcore_delta": pcore_delta,
        "scenario_summary": _stratum_summary(stratified["scenario_stratified"], "scenario_id"),
        "demand_summary": _stratum_summary(stratified["demand_stratified"], "demand_profile_id"),
        "system_summary": _system_summary(stratified["per_system"]),
        "pareto_summary": _pareto_summary(pareto),
        "research_findings": _findings(global_rows, pareto),
        "limitations": {
            "benchmark_scope": [
                "Four architectural scenarios and three demand profiles produce only 12 controlled synthetic cases.",
                "The benchmark does not sample the diversity of real building types, project scales, or construction conditions.",
            ],
            "routing_abstraction": [
                "Routing uses a discrete voxel grid and simplified centerline geometry.",
                "Explicit fabrication fitting geometry and many installation details are not represented.",
                "System dimensions and clearance envelopes are controlled benchmark abstractions where noted by earlier phases.",
            ],
            "engineering_semantics": [
                "Engineering-code coverage and discipline-specific requirements are limited.",
                "C0 is a simplified benchmark feasibility definition, not real-world engineering approval.",
                "Constructability checks do not cover the full set of supports, access, fabrication, maintenance, or jurisdictional constraints.",
            ],
            "ifc_scope": [
                "IFC is used to generate and reopen the controlled architecture and to derive fixed obstacle and shaft-reservation bounds from tessellated geometry.",
                "General compatibility with arbitrary real-world IFC authoring patterns, complex geometry, or external project models has not been validated.",
            ],
            "optimization_scope": [
                "The implemented methods are deterministic algorithms and heuristics; no global optimum is proved.",
                "Pareto analysis compares only the five implemented methods and the declared objectives.",
                "No human preference model or scalar utility is included.",
            ],
            "external_validity": [
                "Observed results from this controlled benchmark are not automatically representative of all buildings or real construction projects.",
            ],
        },
        "threats_to_validity": {
            "internal_validity": [
                "Results can depend on deterministic routing order, fixed configuration, conflict-envelope definitions, and implementation assumptions.",
                "Regression checks reduce unintended drift but do not remove dependence on those design choices.",
            ],
            "construct_validity": [
                "Route length, bends, vertical travel, conflict counts, and simplified C0 feasibility do not fully represent real MEP layout quality.",
            ],
            "external_validity": [
                "The limited synthetic benchmark constrains generalization to real projects and other building typologies.",
            ],
            "reproducibility": [
                "Fixed scenario and demand inputs, stable ordering, byte-identical serialization, and regression tests reduce reproducibility risk.",
                "Reproduction still depends on the documented software environment and the preserved repository inputs.",
            ],
        },
        "conclusion": {
            "scope": "The controlled 12-case benchmark only.",
            "statement": "Independent routing retained complete coverage with substantial conflicts. Fixed-priority sequential reservation removed observed conflicts while completing fewer routes. Selective repair reduced conflicts while retaining complete coverage, and P-CORE further reduced conflict burden without reducing completion, usually with geometry trade-offs. No single implemented method dominated across every tracked objective, and changing the objective set materially changed the Pareto frontier.",
        },
        "integrity_checks": {
            "checkpoint_metrics_match": True,
            "comparison_input_unchanged": comparison == comparison_snapshot,
            "stratified_input_unchanged": stratified == stratified_snapshot,
            "pareto_input_unchanged": pareto == pareto_snapshot,
            "scenario_demand_and_config_inputs_unchanged": comparison["integrity_checks"]["scenario_demand_and_config_inputs_unchanged"],
            "base_occupancy_grids_unchanged": comparison["integrity_checks"]["base_occupancy_grids_unchanged"],
        },
    }
    if comparison != comparison_snapshot or stratified != stratified_snapshot or pareto != pareto_snapshot:
        raise RuntimeError("Final synthesis must not mutate source analysis structures.")
    return result


def run_final_synthesis(
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
    pareto = analyze_pareto(comparison, stratified)
    return synthesize_final(comparison, stratified, pareto)


def _format_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _format_percentage(change: Mapping[str, Any]) -> str:
    value = change["percentage_change"]
    return "undefined (zero seed denominator)" if value is None else f"{value:.2f}%"


def render_final_report(synthesis: Mapping[str, Any]) -> str:
    """Render a deterministic human-readable report from the final artifact."""
    lines = [
        "# MEP Routing Research Final Experimental Report",
        "",
        "## Research Question",
        "",
        synthesis["research_question"],
        "",
        "## Benchmark",
        "",
        "The authoritative benchmark contains 4 architectural scenarios, 3 demand profiles, 12 cases, and 292 requested routes. It uses controlled synthetic geometry.",
        "",
        "## Methods",
        "",
        "The compared methods are B0, B1, B2, P-CORE-SEED, and P-CORE-6C2B. All results use the existing shared evaluators and deterministic inputs.",
        "",
        "## Global Results",
        "",
        "| Method | Successful / requested | Evaluated pairs | Hard conflicts | Clearance violations | Length (m) | Bends | Vertical (m) | C0-feasible cases |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in synthesis["global_metrics"]:
        lines.append(
            f"| {row['method']} | {row['successful_routes']} / {row['requested_routes']} | "
            f"{row['evaluated_successful_inter_system_pairs']} | {row['hard_conflicts']} | "
            f"{row['clearance_violations']} | {_format_number(row['total_routed_length_m'])} | "
            f"{row['total_bends']} | {_format_number(row['total_vertical_travel_m'])} | "
            f"{row['benchmark_hard_feasible_C0_cases']} |"
        )
    coordination = next(row["coordination"] for row in synthesis["global_metrics"] if row["method"] == "P-CORE-6C2B")
    lines.extend([
        "",
        f"P-CORE-6C2B attempted {coordination['attempted_rounds']} rounds, accepted {coordination['accepted_rounds']}, and rolled back {coordination['rolled_back_rounds']}.",
        "",
        "## P-CORE Seed-to-Final Changes",
        "",
        "| Metric | Seed | Final | Delta | Percentage change |",
        "|---|---:|---:|---:|---:|",
    ])
    labels = {
        "hard_conflicts": "Hard conflicts",
        "clearance_violations": "Clearance violations",
        "total_routed_length_m": "Routed length (m)",
        "total_bends": "Bends",
        "total_vertical_travel_m": "Vertical travel (m)",
    }
    for key, label in labels.items():
        change = synthesis["pcore_delta"][key]
        lines.append(
            f"| {label} | {_format_number(change['seed'])} | {_format_number(change['final'])} | "
            f"{_format_number(change['delta'])} | {_format_percentage(change)} |"
        )

    def add_strata(title: str, rows: Sequence[Mapping[str, Any]], key: str) -> None:
        lines.extend([
            "",
            f"### {title}",
            "",
            "| Stratum | Method | Successful / requested | Evaluated pairs | Hard conflicts | Clearance violations |",
            "|---|---|---:|---:|---:|---:|",
        ])
        for row in rows:
            for method in row["methods"]:
                lines.append(
                    f"| {row[key]} | {method['method']} | {method['successful_routes']} / {method['requested_routes']} | "
                    f"{method['evaluated_successful_inter_system_pairs']} | {method['hard_conflicts']} | {method['clearance_violations']} |"
                )

    lines.extend(["", "## Scenario and Demand Behavior"])
    add_strata("Scenario summary", synthesis["scenario_summary"], "scenario_id")
    add_strata("Demand summary", synthesis["demand_summary"], "demand_profile_id")
    lines.extend([
        "",
        "### System summary",
        "",
        "Conflict values below are symmetric partner incidences, not directional fault attribution.",
        "",
        "| System | Method | Successful / attempted | Hard partner incidences | Clearance partner incidences |",
        "|---|---|---:|---:|---:|",
    ])
    for row in synthesis["system_summary"]:
        for method in row["methods"]:
            lines.append(
                f"| {row['system']} | {method['method']} | {method['successful_routes']} / {method['attempted_routes']} | "
                f"{method['hard_conflict_partner_incidences']} | {method['clearance_violation_partner_incidences']} |"
            )
    pareto = synthesis["pareto_summary"]
    lines.extend([
        "",
        "## Multi-Objective / Pareto Analysis",
        "",
        "The global operational and common-success non-dominated sets both contain B0, B1, B2, P-CORE-SEED, and P-CORE-6C2B. Non-dominated membership is not an overall ordering or a claim of automatic superiority.",
        "",
        "Objective-set sensitivity:",
        "",
    ])
    for row in pareto["objective_set_sensitivity"]:
        lines.append(f"- `{row['objective_set']}`: {', '.join(row['non_dominated_methods'])}")
    counts = pareto["pcore_transition_classification_counts"]
    lines.extend([
        "",
        f"P-CORE case transitions: {counts['tradeoff']} tradeoffs, {counts['unchanged']} unchanged, "
        f"{counts['strict_multiobjective_improvement']} strict multi-objective improvements, and {counts['regression']} regressions.",
        "",
        "## Main Findings",
        "",
    ])
    for finding in synthesis["research_findings"]:
        lines.append(f"- **{finding['finding'].replace('_', ' ').title()}:** {finding['interpretation']} Scope: {finding['scope']}")
    lines.extend(["", "## Limitations", ""])
    for category, entries in synthesis["limitations"].items():
        lines.append(f"### {category.replace('_', ' ').title()}")
        lines.append("")
        lines.extend(f"- {entry}" for entry in entries)
        lines.append("")
    lines.extend(["## Threats to Validity", ""])
    for category, entries in synthesis["threats_to_validity"].items():
        lines.append(f"### {category.replace('_', ' ').title()}")
        lines.append("")
        lines.extend(f"- {entry}" for entry in entries)
        lines.append("")
    lines.extend([
        "## Conclusion",
        "",
        synthesis["conclusion"]["statement"],
        "",
        f"Scope: {synthesis['conclusion']['scope']}",
        "",
    ])
    return "\n".join(lines)


def write_final_synthesis(synthesis: Mapping[str, Any], json_output: Path, markdown_output: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(synthesis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_output.write_text(render_final_report(synthesis), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the deterministic final MEP research synthesis and report.")
    for name in ("scenarios", "demands", "systems", "constraints", "coordinator"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("experiments/results/phase8_final_synthesis.json"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("experiments/results/phase8_final_report.md"),
    )
    args = parser.parse_args()
    synthesis = run_final_synthesis(args.scenarios, args.demands, args.systems, args.constraints, args.coordinator)
    write_final_synthesis(synthesis, args.json_output, args.markdown_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
