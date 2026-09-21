"""Deterministic pre-routing MEP demand definitions for benchmark scenarios."""

from __future__ import annotations

import argparse
import json
from math import isfinite, pi
from pathlib import Path
from typing import Any

from .benchmark import calculate_metrics
from .config import (
    interior_grid_positions,
    load_building_config,
    shaft_clear_bounds,
    shaft_outer_bounds,
)


SYSTEM_ORDER = ("hvac", "drainage", "water", "fire", "electrical")
SOURCE_OFFSETS = {
    "hvac": (-0.25, 0.25),
    "drainage": (0.25, 0.25),
    "water": (-0.25, -0.25),
    "fire": (0.25, -0.25),
    "electrical": (0.0, 0.0),
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return value


def load_service_definitions(path: Path) -> dict[str, dict[str, Any]]:
    """Load and validate the five controlled nominal benchmark service envelopes."""
    systems = _load_json(path).get("systems")
    if not isinstance(systems, dict) or set(systems) != set(SYSTEM_ORDER):
        raise ValueError("MEP system definitions must contain the five expected systems in stable order.")
    for system, definition in systems.items():
        if not isinstance(definition, dict):
            raise ValueError(f"System definition for {system} must be an object.")
        envelope_type = definition.get("envelope_type")
        if envelope_type == "rectangular":
            dimensions = (definition.get("width_m"), definition.get("height_m"))
        elif envelope_type == "circular":
            dimensions = (definition.get("diameter_m"),)
        else:
            raise ValueError(f"System {system} must define a circular or rectangular envelope.")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 for value in dimensions):
            raise ValueError(f"System {system} has invalid nominal envelope dimensions.")
        if definition.get("flow_direction") not in {"source_to_terminal", "terminal_to_source"}:
            raise ValueError(f"System {system} must define a valid flow direction.")
    return systems


def load_demand_profiles(demands_directory: Path) -> list[dict[str, Any]]:
    """Load validated deterministic demand profiles in manifest order."""
    profiles = _load_json(demands_directory / "manifest.json").get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("Demand manifest must contain a non-empty profiles list.")
    loaded_profiles = []
    for entry in profiles:
        if not isinstance(entry, dict):
            raise ValueError("Each demand manifest entry must be an object.")
        profile_id = entry.get("profile_id")
        config_file = entry.get("config_file")
        intended_load = entry.get("intended_load")
        description = entry.get("description")
        if not all(isinstance(value, str) and value for value in (profile_id, config_file, intended_load, description)):
            raise ValueError("Demand manifest entries require id, file, intended load, and description.")
        profile = _load_json(demands_directory / config_file)
        if profile.get("profile_id") != profile_id or profile.get("intended_load") != intended_load:
            raise ValueError(f"Demand profile {config_file} does not match its manifest entry.")
        systems = profile.get("systems")
        if not isinstance(systems, dict) or set(systems) != set(SYSTEM_ORDER):
            raise ValueError(f"Demand profile {profile_id} must define the five expected systems.")
        for system in SYSTEM_ORDER:
            count = systems[system].get("terminal_count") if isinstance(systems[system], dict) else None
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(f"Demand profile {profile_id} has an invalid {system} terminal count.")
        loaded_profiles.append(profile)
    return loaded_profiles


def _load_scenarios(scenarios_directory: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    manifest = _load_json(scenarios_directory / "manifest.json")
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Scenario manifest must contain a non-empty scenarios list.")
    loaded_scenarios = []
    for entry in scenarios:
        if not isinstance(entry, dict):
            raise ValueError("Each scenario manifest entry must be an object.")
        scenario_id = entry.get("scenario_id")
        config_file = entry.get("config_file")
        intended_difficulty = entry.get("intended_difficulty")
        if not all(isinstance(value, str) and value for value in (scenario_id, config_file, intended_difficulty)):
            raise ValueError("Scenario manifest entries require id, file, and intended difficulty.")
        loaded_scenarios.append((entry, load_building_config(scenarios_directory / config_file)))
    return loaded_scenarios


def _routing_z(config: dict[str, Any]) -> float:
    routing_z_min = float(config["slab_thickness_m"]) + float(config["ceiling_height_m"])
    routing_z_max = float(config["floor_to_floor_m"])
    return routing_z_min + (routing_z_max - routing_z_min) / 2


def _point_is_valid_terminal_candidate(config: dict[str, Any], x: float, y: float) -> bool:
    clearance = float(config["routing"]["clearance_m"])
    wall_thickness = float(config["wall_thickness_m"])
    width = float(config["width_m"])
    length = float(config["length_m"])
    if not (wall_thickness + clearance < x < width - wall_thickness - clearance):
        return False
    if not (wall_thickness + clearance < y < length - wall_thickness - clearance):
        return False

    shaft = config["shaft"]
    outer_min_x, outer_max_x, outer_min_y, outer_max_y = shaft_outer_bounds(
        shaft_clear_bounds(width, length, float(shaft["width_m"]), float(shaft["length_m"])),
        wall_thickness,
    )
    if outer_min_x - clearance <= x <= outer_max_x + clearance and outer_min_y - clearance <= y <= outer_max_y + clearance:
        return False

    half_column = float(config["column_size_m"]) / 2 + clearance
    for _, center_x in interior_grid_positions(float(config["grid_spacing_x_m"]), width):
        for _, center_y in interior_grid_positions(float(config["grid_spacing_y_m"]), length):
            if center_x - half_column <= x <= center_x + half_column and center_y - half_column <= y <= center_y + half_column:
                return False
    return True


def _terminal_candidate_positions(config: dict[str, Any], required_count: int) -> list[tuple[float, float]]:
    """Select a stable farthest-spread subset of a normalized candidate lattice."""
    width = float(config["width_m"])
    length = float(config["length_m"])
    wall_thickness = float(config["wall_thickness_m"])
    clearance = float(config["routing"]["clearance_m"])
    minimum_x, maximum_x = wall_thickness + clearance, width - wall_thickness - clearance
    minimum_y, maximum_y = wall_thickness + clearance, length - wall_thickness - clearance
    candidates = []
    divisions = 15
    for x_index in range(1, divisions + 1):
        y_indices = range(1, divisions + 1) if x_index % 2 else range(divisions, 0, -1)
        for y_index in y_indices:
            x = minimum_x + (maximum_x - minimum_x) * x_index / (divisions + 1)
            y = minimum_y + (maximum_y - minimum_y) * y_index / (divisions + 1)
            if _point_is_valid_terminal_candidate(config, x, y):
                candidates.append((x, y))
    if len(candidates) < required_count:
        raise ValueError("Deterministic terminal candidate lattice cannot satisfy the demand profile.")

    selected = [candidates[0]]
    remaining = candidates[1:]
    while len(selected) < required_count:
        candidate = max(
            remaining,
            key=lambda point: min((point[0] - chosen[0]) ** 2 + (point[1] - chosen[1]) ** 2 for chosen in selected),
        )
        selected.append(candidate)
        remaining.remove(candidate)
    return selected


def _source_anchors(config: dict[str, Any]) -> list[dict[str, Any]]:
    width = float(config["width_m"])
    length = float(config["length_m"])
    shaft = config["shaft"]
    clear_min_x, clear_max_x, clear_min_y, clear_max_y = shaft_clear_bounds(
        width, length, float(shaft["width_m"]), float(shaft["length_m"])
    )
    center_x, center_y = (clear_min_x + clear_max_x) / 2, (clear_min_y + clear_max_y) / 2
    z = _routing_z(config)
    anchors = []
    for system in SYSTEM_ORDER:
        offset_x, offset_y = SOURCE_OFFSETS[system]
        anchors.append(
            {
                "anchor_id": f"source/{system}",
                "system": system,
                "x_m": center_x + offset_x * (clear_max_x - clear_min_x),
                "y_m": center_y + offset_y * (clear_max_y - clear_min_y),
                "z_m": z,
            }
        )
    return anchors


def _terminal_anchor_catalog(config: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    maximum_counts = {
        system: max(profile["systems"][system]["terminal_count"] for profile in profiles)
        for system in SYSTEM_ORDER
    }
    candidates = iter(_terminal_candidate_positions(config, sum(maximum_counts.values())))
    catalog = {system: [] for system in SYSTEM_ORDER}
    z = _routing_z(config)
    for terminal_index in range(max(maximum_counts.values())):
        for system in SYSTEM_ORDER:
            if terminal_index < maximum_counts[system]:
                x, y = next(candidates)
                catalog[system].append(
                    {
                        "anchor_id": f"terminal/{system}/{terminal_index}",
                        "system": system,
                        "terminal_index": terminal_index,
                        "x_m": x,
                        "y_m": y,
                        "z_m": z,
                    }
                )
    return catalog


def _nominal_cross_section(definition: dict[str, Any]) -> float:
    if definition["envelope_type"] == "circular":
        return pi * float(definition["diameter_m"]) ** 2 / 4
    return float(definition["width_m"]) * float(definition["height_m"])


def _validate_anchors(
    config: dict[str, Any], source_anchors: list[dict[str, Any]], terminal_anchors: list[dict[str, Any]]
) -> None:
    clear_bounds = shaft_clear_bounds(
        float(config["width_m"]),
        float(config["length_m"]),
        float(config["shaft"]["width_m"]),
        float(config["shaft"]["length_m"]),
    )
    routing_z_min = float(config["slab_thickness_m"]) + float(config["ceiling_height_m"])
    routing_z_max = float(config["floor_to_floor_m"])
    source_coordinates = set()
    for anchor in source_anchors:
        coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
        if not all(isfinite(value) for value in coordinates):
            raise ValueError("Source anchors must have finite coordinates.")
        if not (clear_bounds[0] < anchor["x_m"] < clear_bounds[1] and clear_bounds[2] < anchor["y_m"] < clear_bounds[3]):
            raise ValueError("Source anchors must remain inside the clear shaft.")
        if not routing_z_min < anchor["z_m"] < routing_z_max:
            raise ValueError("Source anchors must remain inside the routing plenum.")
        source_coordinates.add(coordinates)
    if len(source_coordinates) != len(source_anchors):
        raise ValueError("Source anchors must be unique.")

    terminal_coordinates = set()
    for anchor in terminal_anchors:
        coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
        if not all(isfinite(value) for value in coordinates):
            raise ValueError("Terminal anchors must have finite coordinates.")
        if not _point_is_valid_terminal_candidate(config, anchor["x_m"], anchor["y_m"]):
            raise ValueError("Terminal anchors must remain outside fixed obstacles and clearance zones.")
        if not routing_z_min < anchor["z_m"] < routing_z_max:
            raise ValueError("Terminal anchors must remain inside the routing plenum.")
        terminal_coordinates.add(coordinates)
    if len(terminal_coordinates) != len(terminal_anchors):
        raise ValueError("Terminal anchors must be unique within a benchmark case.")


def generate_benchmark_cases(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path
) -> list[dict[str, Any]]:
    """Generate all deterministic geometry-demand routing problem definitions."""
    systems = load_service_definitions(systems_path)
    profiles = load_demand_profiles(demands_directory)
    cases = []
    for scenario, config in _load_scenarios(scenarios_directory):
        building_metrics = calculate_metrics(config)
        source_anchors = _source_anchors(config)
        terminal_catalog = _terminal_anchor_catalog(config, profiles)
        for profile in profiles:
            terminal_anchors = [
                anchor
                for system in SYSTEM_ORDER
                for anchor in terminal_catalog[system][: profile["systems"][system]["terminal_count"]]
            ]
            anchor_ids = {anchor["anchor_id"] for anchor in [*source_anchors, *terminal_anchors]}
            connection_requests = []
            for anchor in terminal_anchors:
                system = anchor["system"]
                source_id = f"source/{system}"
                start_anchor, end_anchor = (
                    (anchor["anchor_id"], source_id)
                    if systems[system]["flow_direction"] == "terminal_to_source"
                    else (source_id, anchor["anchor_id"])
                )
                connection_requests.append(
                    {
                        "connection_id": f"{system}/{anchor['terminal_index']}",
                        "system": system,
                        "start_anchor": start_anchor,
                        "end_anchor": end_anchor,
                        "terminal_index": anchor["terminal_index"],
                    }
                )
            if any(
                request["start_anchor"] not in anchor_ids or request["end_anchor"] not in anchor_ids
                for request in connection_requests
            ):
                raise ValueError("Connection requests must reference anchors in the same case.")
            _validate_anchors(config, source_anchors, terminal_anchors)
            total_terminals = len(terminal_anchors)
            demand_metrics = {
                "total_terminal_count": total_terminals,
                "total_connection_request_count": len(connection_requests),
                "terminal_count_per_system": {
                    system: profile["systems"][system]["terminal_count"] for system in SYSTEM_ORDER
                },
                "system_mix": {system: profile["systems"][system]["terminal_count"] for system in SYSTEM_ORDER},
                "terminal_density_per_100m2": total_terminals / building_metrics["interior_floor_area_m2"] * 100,
                "connection_density_per_100m2": len(connection_requests) / building_metrics["interior_floor_area_m2"] * 100,
                "connection_density_per_1000m3": len(connection_requests)
                / building_metrics["routing_volume_proxy_m3"]
                * 1000,
                "aggregate_nominal_cross_section_m2": sum(
                    _nominal_cross_section(systems[request["system"]]) for request in connection_requests
                ),
            }
            cases.append(
                {
                    "case_id": f"{scenario['scenario_id']}-{profile['profile_id']}",
                    "scenario_id": scenario["scenario_id"],
                    "demand_profile_id": profile["profile_id"],
                    "intended_geometry_difficulty": scenario["intended_difficulty"],
                    "intended_demand_load": profile["intended_load"],
                    "building_metrics": building_metrics,
                    "demand_metrics": demand_metrics,
                    "source_anchors": source_anchors,
                    "terminal_anchors": terminal_anchors,
                    "connection_requests": connection_requests,
                }
            )
    return cases


def write_routing_demands(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path, output_path: Path
) -> list[dict[str, Any]]:
    """Write deterministic routing-demand cases and return them."""
    cases = generate_benchmark_cases(scenarios_directory, demands_directory, systems_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"cases": cases}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cases


def main() -> int:
    """Generate all deterministic geometry-demand benchmark cases."""
    parser = argparse.ArgumentParser(description="Generate deterministic MEP routing demand benchmarks.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cases = write_routing_demands(args.scenarios, args.demands, args.systems, args.output)
    print("case     geometry  demand  terminals  terminals/100m2  connections/1000m3  nominal-area(m2)")
    for case in cases:
        metrics = case["demand_metrics"]
        print(
            f"{case['case_id']:<8} {case['intended_geometry_difficulty']:<9} "
            f"{case['intended_demand_load']:<7} {metrics['total_terminal_count']:<10} "
            f"{metrics['terminal_density_per_100m2']:.4f}           "
            f"{metrics['connection_density_per_1000m3']:.4f}                "
            f"{metrics['aggregate_nominal_cross_section_m2']:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
