"""C0 controlled benchmark hard-constraint verification for existing routes.

C0 is a reproducible research constraint set, not building-code or fabrication
compliance.  It validates existing B0/B1 results without changing any route.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .clash import evaluate_route_result_conflicts
from .demand import generate_benchmark_cases, load_service_definitions
from .routing import run_b0_benchmark
from .sequential import run_b1_benchmark
from .voxel import FREE, build_occupancy_grids


TOLERANCE = 1e-9
FAILURE_ORDER = (
    "CONNECTION_INCOMPLETE", "ENDPOINT_INTEGRITY", "PATH_DISCONTINUITY",
    "FIXED_OBSTACLE_VIOLATION", "INTER_SYSTEM_HARD_CONFLICT",
    "INTER_SYSTEM_CLEARANCE_VIOLATION", "DRAINAGE_SLOPE_VIOLATION",
    "DRAINAGE_UPHILL_VIOLATION",
)


def load_constructability_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    drainage = config.get("drainage") if isinstance(config, dict) else None
    if not (
        config.get("constraint_set_id") == "C0"
        and config.get("constraint_set_name") == "Core Heterogeneous Constructability Constraints"
        and config.get("benchmark_only") is True
        and isinstance(drainage, dict)
        and isinstance(drainage.get("minimum_slope_ratio"), (int, float))
        and drainage["minimum_slope_ratio"] > 0
        and drainage.get("allow_uphill_steps") is False
    ):
        raise ValueError("constructability.json must define the controlled C0 constraint set.")
    return config


def drainage_gravity_metrics(route: dict[str, Any], voxel_size: float, slope: float) -> dict[str, Any] | None:
    if not route["path_found"]:
        return None
    cells = route["path_cells"]
    horizontal = sum(cell[2] == next_cell[2] for cell, next_cell in zip(cells, cells[1:]))
    run = horizontal * voxel_size
    start_z = (cells[0][2] + 0.5) * voxel_size  # Relative origin cancels for net drop.
    end_z = (cells[-1][2] + 0.5) * voxel_size
    actual = start_z - end_z
    required = slope * run
    uphill = sum(next_cell[2] > cell[2] for cell, next_cell in zip(cells, cells[1:]))
    slope_pass = actual + TOLERANCE >= required
    monotonic = uphill == 0
    return {
        "horizontal_run_m": run, "required_net_drop_m": required,
        "actual_net_drop_m": actual,
        "effective_average_slope_ratio": actual / run if run else None,
        "uphill_step_count": uphill, "minimum_slope_pass": slope_pass,
        "monotonic_flow_pass": monotonic,
        "drainage_gravity_pass": slope_pass and monotonic,
    }


_drainage_metrics = drainage_gravity_metrics  # Backward-compatible private test alias.


def _verify_route(route: dict[str, Any], grid: Any, slope: float) -> dict[str, Any]:
    if not route["path_found"]:
        return {
            "case_id": route["case_id"], "system": route["system"], "connection_id": route["connection_id"],
            "path_found": False, "endpoint_integrity_pass": False, "path_continuity_pass": False,
            "fixed_obstacle_compliance_pass": False, "drainage_gravity": None,
            "route_hard_constraint_pass": False,
        }
    path = route["path_cells"]
    start = route["start"]["grid_index"]
    end = route["end"]["grid_index"]
    endpoints = bool(path) and path[0] == start and path[-1] == end and all(
        item["snap_distance_m"] <= 2 * grid.spec.voxel_size_m + TOLERANCE for item in (route["start"], route["end"])
    )
    continuous = all(sum(abs(a - b) for a, b in zip(cell, following)) == 1 for cell, following in zip(path, path[1:]))
    fixed = all(
        0 <= cell[0] < grid.spec.nx and 0 <= cell[1] < grid.spec.ny and 0 <= cell[2] < grid.spec.nz
        and grid.state_at(*cell) == FREE for cell in path
    )
    drainage = drainage_gravity_metrics(route, grid.spec.voxel_size_m, slope) if route["system"] == "drainage" else None
    return {
        "case_id": route["case_id"], "system": route["system"], "connection_id": route["connection_id"],
        "path_found": True, "endpoint_integrity_pass": endpoints, "path_continuity_pass": continuous,
        "fixed_obstacle_compliance_pass": fixed, "drainage_gravity": drainage,
        "route_hard_constraint_pass": endpoints and continuous and fixed and (drainage is None or drainage["drainage_gravity_pass"]),
    }


def verify_route_result(routing_result: dict[str, Any], grids: dict[tuple[str, str], Any], constraints: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    """Generic C0 verification usable by B0, B1, and future compatible results."""
    conflicts = evaluate_route_result_conflicts(
        routing_result, grids, definitions, "C0", "C0 Geometry Evaluation", "routing_summary", {},
        include_requested_counts=True,
    )
    conflict_cases = {case["case_id"]: case for case in conflicts["cases"]}
    slope = float(constraints["drainage"]["minimum_slope_ratio"])
    cases = []
    for case in routing_result["cases"]:
        expected = {(request["system"], request["connection_id"]) for request in case["connection_requests"]} if "connection_requests" in case else {(route["system"], route["connection_id"]) for route in case["routes"]}
        routes = sorted(case["routes"], key=lambda route: (route["system"], route["connection_id"]))
        actual = [(route["system"], route["connection_id"]) for route in routes]
        completeness = len(actual) == len(set(actual)) and set(actual) == expected
        records = [_verify_route(route, grids[(case["scenario_id"], route["system"])], slope) for route in routes]
        conflict = conflict_cases[case["case_id"]]
        drainage_records = [record for record in records if record["system"] == "drainage"]
        all_drainage = bool(drainage_records) and all(record["drainage_gravity"] and record["drainage_gravity"]["drainage_gravity_pass"] for record in drainage_records)
        reasons = []
        if not completeness or any(not record["path_found"] for record in records): reasons.append("CONNECTION_INCOMPLETE")
        if any(record["path_found"] and not record["endpoint_integrity_pass"] for record in records): reasons.append("ENDPOINT_INTEGRITY")
        if any(record["path_found"] and not record["path_continuity_pass"] for record in records): reasons.append("PATH_DISCONTINUITY")
        if any(record["path_found"] and not record["fixed_obstacle_compliance_pass"] for record in records): reasons.append("FIXED_OBSTACLE_VIOLATION")
        if conflict["hard_conflicting_route_pair_count"]: reasons.append("INTER_SYSTEM_HARD_CONFLICT")
        if conflict["clearance_violating_route_pair_count"]: reasons.append("INTER_SYSTEM_CLEARANCE_VIOLATION")
        if any(record["drainage_gravity"] and not record["drainage_gravity"]["minimum_slope_pass"] for record in drainage_records): reasons.append("DRAINAGE_SLOPE_VIOLATION")
        if any(record["drainage_gravity"] and not record["drainage_gravity"]["monotonic_flow_pass"] for record in drainage_records): reasons.append("DRAINAGE_UPHILL_VIOLATION")
        reasons.sort(key=FAILURE_ORDER.index)
        cases.append({
            "case_id": case["case_id"], "scenario_id": case["scenario_id"], "demand_profile_id": case["demand_profile_id"],
            "requested_connection_count": len(expected), "successful_connection_count": sum(record["path_found"] for record in records),
            "failed_connection_count": sum(not record["path_found"] for record in records),
            "all_required_connections_routed": completeness and all(record["path_found"] for record in records),
            "all_successful_routes_endpoint_valid": all(not record["path_found"] or record["endpoint_integrity_pass"] for record in records),
            "all_successful_routes_continuous": all(not record["path_found"] or record["path_continuity_pass"] for record in records),
            "all_successful_routes_fixed_obstacle_compliant": all(not record["path_found"] or record["fixed_obstacle_compliance_pass"] for record in records),
            "inter_system_hard_conflict_count": conflict["hard_conflicting_route_pair_count"],
            "inter_system_clearance_violation_count": conflict["clearance_violating_route_pair_count"],
            "all_drainage_routes_gravity_compliant": all_drainage,
            "benchmark_hard_feasible_C0": not reasons,
            "failure_reasons": reasons, "route_verifications": records,
        })
    return {"cases": cases, "conflicts": conflicts}


def _method_summary(verification: dict[str, Any]) -> dict[str, Any]:
    cases = verification["cases"]
    records = [record for case in cases for record in case["route_verifications"]]
    drainage = [record for record in records if record["system"] == "drainage"]
    return {
        "total_cases": len(cases), "benchmark_hard_feasible_case_count": sum(case["benchmark_hard_feasible_C0"] for case in cases),
        "benchmark_hard_feasible_case_rate": sum(case["benchmark_hard_feasible_C0"] for case in cases) / len(cases),
        "routing_complete_case_count": sum(case["all_required_connections_routed"] for case in cases),
        "hard_conflict_free_case_count": sum(not case["inter_system_hard_conflict_count"] for case in cases),
        "clearance_compliant_case_count": sum(not case["inter_system_clearance_violation_count"] for case in cases),
        "drainage_gravity_compliant_case_count": sum(case["all_drainage_routes_gravity_compliant"] for case in cases),
        "total_requested_drainage_routes": len(drainage), "successful_drainage_routes": sum(record["path_found"] for record in drainage),
        "slope_compliant_successful_drainage_routes": sum(bool(record["drainage_gravity"] and record["drainage_gravity"]["minimum_slope_pass"]) for record in drainage),
        "uphill_violating_drainage_routes": sum(bool(record["drainage_gravity"] and not record["drainage_gravity"]["monotonic_flow_pass"]) for record in drainage),
        "failure_reason_counts": {reason: sum(reason in case["failure_reasons"] for case in cases) for reason in FAILURE_ORDER if any(reason in case["failure_reasons"] for case in cases)},
    }


def run_c0_verification(scenarios: Path, demands: Path, systems_path: Path, constraints_path: Path) -> dict[str, Any]:
    constraints = load_constructability_config(constraints_path)
    grids = build_occupancy_grids(scenarios, systems_path)
    definitions = load_service_definitions(systems_path)
    b0, b1 = run_b0_benchmark(scenarios, demands, systems_path, grids=grids), run_b1_benchmark(scenarios, demands, systems_path, grids=grids)
    # Add demanded connection identities without duplicating route geometry in output.
    expected = {case["case_id"]: case["connection_requests"] for case in generate_benchmark_cases(scenarios, demands, systems_path)}
    for result in (b0, b1):
        for case in result["cases"]: case["connection_requests"] = expected[case["case_id"]]
    b0_verify, b1_verify = verify_route_result(b0, grids, constraints, definitions), verify_route_result(b1, grids, constraints, definitions)
    return {
        "constraint_set": {"id": constraints["constraint_set_id"], "name": constraints["constraint_set_name"], "benchmark_only": True,
            "limitations": ["Not building-code compliance", "Not fabrication-ready geometry", "No sizing, support, access, or fitting checks"]},
        "methods": {"B0": {"global_summary": _method_summary(b0_verify), "cases": b0_verify["cases"]}, "B1": {"global_summary": _method_summary(b1_verify), "cases": b1_verify["cases"]}},
        "comparison": {"B0_vs_B1_same_constraints": True},
    }


def write_c0_verification(scenarios: Path, demands: Path, systems: Path, constraints: Path, output: Path) -> dict[str, Any]:
    result = run_c0_verification(scenarios, demands, systems, constraints)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify B0 and B1 under C0 benchmark hard constraints.")
    for name in ("scenarios", "demands", "systems", "constraints", "output"): parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    result = write_c0_verification(args.scenarios, args.demands, args.systems, args.constraints, args.output)
    print("method cases routing-complete hard-free clearance-compliant drainage-compliant C0-feasible C0%")
    for name in ("B0", "B1"):
        summary = result["methods"][name]["global_summary"]
        print(f"{name:<6} {summary['total_cases']:5} {summary['routing_complete_case_count']:16} {summary['hard_conflict_free_case_count']:9} {summary['clearance_compliant_case_count']:20} {summary['drainage_gravity_compliant_case_count']:18} {summary['benchmark_hard_feasible_case_count']:11} {summary['benchmark_hard_feasible_case_rate'] * 100:4.1f}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
