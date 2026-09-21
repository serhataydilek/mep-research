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
EGRESS_PREFERENCES = {
    "hvac": (("north", -0.30), ("north", 0.0), ("north", 0.30), ("east", -0.30), ("west", 0.30)),
    "drainage": (("east", 0.30), ("east", 0.0), ("east", -0.30), ("north", 0.30), ("south", -0.30)),
    "water": (("west", -0.30), ("west", 0.0), ("west", 0.30), ("south", -0.30), ("north", 0.30)),
    "fire": (("south", 0.30), ("south", 0.0), ("south", -0.30), ("west", 0.30), ("east", -0.30)),
    "electrical": (("north", 0.30), ("north", 0.0), ("north", -0.30), ("east", 0.30), ("west", -0.30)),
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


def service_routing_margins(definition: dict[str, Any], clearance_m: float) -> dict[str, float]:
    """Return conservative benchmark centerline margins for one nominal service.

    This baseline uses nominal service envelopes plus routing clearance. It is
    not a full fabrication-geometry model.
    """
    if definition["envelope_type"] == "circular":
        planar_half_extent = float(definition["diameter_m"]) / 2
        vertical_half_extent = planar_half_extent
    else:
        planar_half_extent = max(float(definition["width_m"]), float(definition["height_m"])) / 2
        vertical_half_extent = float(definition["height_m"]) / 2
    return {
        "planar_half_extent_m": planar_half_extent,
        "vertical_half_extent_m": vertical_half_extent,
        "required_planar_margin_m": planar_half_extent + clearance_m,
        "required_vertical_margin_m": vertical_half_extent + clearance_m,
    }


def preflight_service_feasibility(
    config: dict[str, Any], systems: dict[str, dict[str, Any]]
) -> dict[str, float | bool]:
    """Reject a geometry-demand combination whose plenum cannot fit a service."""
    plenum_height = float(calculate_metrics(config)["plenum_height_m"])
    clearance = float(config["routing"]["clearance_m"])
    required_envelopes = []
    for system in SYSTEM_ORDER:
        margins = service_routing_margins(systems[system], clearance)
        required_envelope = 2 * margins["required_vertical_margin_m"]
        if required_envelope > plenum_height:
            raise ValueError(
                f"Scenario plenum cannot fit the nominal {system} envelope with routing clearance."
            )
        required_envelopes.append(required_envelope)
    return {
        "all_service_envelopes_fit_plenum": True,
        "plenum_height_m": plenum_height,
        "maximum_required_service_vertical_envelope_m": max(required_envelopes),
    }


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


def _point_is_valid_service_candidate(
    config: dict[str, Any], definition: dict[str, Any], x: float, y: float
) -> bool:
    margin = service_routing_margins(definition, float(config["routing"]["clearance_m"]))[
        "required_planar_margin_m"
    ]
    wall_thickness = float(config["wall_thickness_m"])
    width = float(config["width_m"])
    length = float(config["length_m"])
    if not (wall_thickness + margin < x < width - wall_thickness - margin):
        return False
    if not (wall_thickness + margin < y < length - wall_thickness - margin):
        return False

    shaft = config["shaft"]
    outer_min_x, outer_max_x, outer_min_y, outer_max_y = shaft_outer_bounds(
        shaft_clear_bounds(width, length, float(shaft["width_m"]), float(shaft["length_m"])),
        wall_thickness,
    )
    if outer_min_x - margin <= x <= outer_max_x + margin and outer_min_y - margin <= y <= outer_max_y + margin:
        return False

    half_column = float(config["column_size_m"]) / 2 + margin
    for _, center_x in interior_grid_positions(float(config["grid_spacing_x_m"]), width):
        for _, center_y in interior_grid_positions(float(config["grid_spacing_y_m"]), length):
            if center_x - half_column <= x <= center_x + half_column and center_y - half_column <= y <= center_y + half_column:
                return False
    return True


def _terminal_candidate_lattice(config: dict[str, Any]) -> list[tuple[float, float]]:
    """Return a stable normalized candidate lattice inside the wall inner boundary."""
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
            candidates.append((x, y))
    return candidates


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


def _select_farthest_candidate(
    candidates: list[tuple[float, float]], selected: list[tuple[float, float]]
) -> tuple[float, float]:
    if not selected:
        return candidates[0]
    return max(
        candidates,
        key=lambda point: min(
            (point[0] - chosen[0]) ** 2 + (point[1] - chosen[1]) ** 2
            for chosen in selected
        ),
    )


def _terminal_anchor_catalog(
    config: dict[str, Any], profiles: list[dict[str, Any]], systems: dict[str, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Allocate service-aware terminal anchors in stable round-robin order."""
    maximum_counts = {
        system: max(profile["systems"][system]["terminal_count"] for profile in profiles)
        for system in SYSTEM_ORDER
    }
    lattice = _terminal_candidate_lattice(config)
    selected_coordinates: list[tuple[float, float]] = []
    catalog = {system: [] for system in SYSTEM_ORDER}
    z = _routing_z(config)
    for terminal_index in range(max(maximum_counts.values())):
        for system in SYSTEM_ORDER:
            if terminal_index < maximum_counts[system]:
                available = [
                    point
                    for point in lattice
                    if point not in selected_coordinates
                    and _point_is_valid_service_candidate(config, systems[system], point[0], point[1])
                ]
                if not available:
                    raise ValueError(
                        f"No valid deterministic terminal candidate exists for nominal {system} routing demand."
                    )
                x, y = _select_farthest_candidate(available, selected_coordinates)
                selected_coordinates.append((x, y))
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


def _egress_candidate(
    config: dict[str, Any], definition: dict[str, Any], side: str, offset: float
) -> tuple[float, float]:
    width = float(config["width_m"])
    length = float(config["length_m"])
    wall_thickness = float(config["wall_thickness_m"])
    shaft = config["shaft"]
    clear_min_x, clear_max_x, clear_min_y, clear_max_y = shaft_clear_bounds(
        width, length, float(shaft["width_m"]), float(shaft["length_m"])
    )
    outer_min_x, outer_max_x, outer_min_y, outer_max_y = shaft_outer_bounds(
        (clear_min_x, clear_max_x, clear_min_y, clear_max_y), wall_thickness
    )
    margin = service_routing_margins(definition, float(config["routing"]["clearance_m"]))[
        "required_planar_margin_m"
    ]
    epsilon = 0.000001
    center_x, center_y = (clear_min_x + clear_max_x) / 2, (clear_min_y + clear_max_y) / 2
    if side == "north":
        return center_x + offset * (clear_max_x - clear_min_x), outer_max_y + margin + epsilon
    if side == "east":
        return outer_max_x + margin + epsilon, center_y + offset * (clear_max_y - clear_min_y)
    if side == "south":
        return center_x + offset * (clear_max_x - clear_min_x), outer_min_y - margin - epsilon
    if side == "west":
        return outer_min_x - margin - epsilon, center_y + offset * (clear_max_y - clear_min_y)
    raise ValueError(f"Unsupported shaft egress side: {side}")


def _egress_anchors(config: dict[str, Any], systems: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose one deterministic room-side egress for each shaft source."""
    anchors = []
    used_coordinates = set()
    z = _routing_z(config)
    for system in SYSTEM_ORDER:
        for side, offset in EGRESS_PREFERENCES[system]:
            x, y = _egress_candidate(config, systems[system], side, offset)
            if _point_is_valid_service_candidate(config, systems[system], x, y) and (x, y, z) not in used_coordinates:
                anchors.append(
                    {
                        "anchor_id": f"egress/{system}",
                        "system": system,
                        "wall_side": side,
                        "x_m": x,
                        "y_m": y,
                        "z_m": z,
                    }
                )
                used_coordinates.add((x, y, z))
                break
        else:
            raise ValueError(f"No valid deterministic shaft egress exists for nominal {system} routing demand.")
    return anchors


def _nominal_cross_section(definition: dict[str, Any]) -> float:
    if definition["envelope_type"] == "circular":
        return pi * float(definition["diameter_m"]) ** 2 / 4
    return float(definition["width_m"]) * float(definition["height_m"])


def _anchor_has_valid_vertical_clearance(
    config: dict[str, Any], definition: dict[str, Any], z: float
) -> bool:
    margin = service_routing_margins(definition, float(config["routing"]["clearance_m"]))[
        "required_vertical_margin_m"
    ]
    routing_z_min = float(config["slab_thickness_m"]) + float(config["ceiling_height_m"])
    routing_z_max = float(config["floor_to_floor_m"])
    return routing_z_min <= z - margin and z + margin <= routing_z_max


def _validate_anchors(
    config: dict[str, Any],
    systems: dict[str, dict[str, Any]],
    source_anchors: list[dict[str, Any]],
    egress_anchors: list[dict[str, Any]],
    terminal_anchors: list[dict[str, Any]],
) -> None:
    clear_bounds = shaft_clear_bounds(
        float(config["width_m"]),
        float(config["length_m"]),
        float(config["shaft"]["width_m"]),
        float(config["shaft"]["length_m"]),
    )
    source_coordinates = set()
    for anchor in source_anchors:
        coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
        if not all(isfinite(value) for value in coordinates):
            raise ValueError("Source anchors must have finite coordinates.")
        if not (clear_bounds[0] < anchor["x_m"] < clear_bounds[1] and clear_bounds[2] < anchor["y_m"] < clear_bounds[3]):
            raise ValueError("Source anchors must remain inside the clear shaft.")
        planar_margin = service_routing_margins(
            systems[anchor["system"]], float(config["routing"]["clearance_m"])
        )["required_planar_margin_m"]
        if not (
            clear_bounds[0] + planar_margin <= anchor["x_m"] <= clear_bounds[1] - planar_margin
            and clear_bounds[2] + planar_margin <= anchor["y_m"] <= clear_bounds[3] - planar_margin
        ):
            raise ValueError("Source anchors must keep their nominal service envelope inside the clear shaft.")
        if not _anchor_has_valid_vertical_clearance(config, systems[anchor["system"]], anchor["z_m"]):
            raise ValueError("Source anchors must remain inside the routing plenum.")
        source_coordinates.add(coordinates)
    if len(source_coordinates) != len(source_anchors):
        raise ValueError("Source anchors must be unique.")

    egress_coordinates = set()
    for anchor in egress_anchors:
        coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
        if not all(isfinite(value) for value in coordinates):
            raise ValueError("Egress anchors must have finite coordinates.")
        if not _point_is_valid_service_candidate(config, systems[anchor["system"]], anchor["x_m"], anchor["y_m"]):
            raise ValueError("Egress anchors must respect service-aware fixed-obstacle margins.")
        if not _anchor_has_valid_vertical_clearance(config, systems[anchor["system"]], anchor["z_m"]):
            raise ValueError("Egress anchors must remain inside the routing plenum.")
        egress_coordinates.add(coordinates)
    if len(egress_coordinates) != len(egress_anchors):
        raise ValueError("Egress anchors must be unique.")

    terminal_coordinates = set()
    for anchor in terminal_anchors:
        coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
        if not all(isfinite(value) for value in coordinates):
            raise ValueError("Terminal anchors must have finite coordinates.")
        if not _point_is_valid_service_candidate(config, systems[anchor["system"]], anchor["x_m"], anchor["y_m"]):
            raise ValueError("Terminal anchors must remain outside fixed obstacles and clearance zones.")
        if not _anchor_has_valid_vertical_clearance(config, systems[anchor["system"]], anchor["z_m"]):
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
        preflight = preflight_service_feasibility(config, systems)
        source_anchors = _source_anchors(config)
        egress_anchors = _egress_anchors(config, systems)
        terminal_catalog = _terminal_anchor_catalog(config, profiles, systems)
        egress_ids = {anchor["system"]: anchor["anchor_id"] for anchor in egress_anchors}
        fixed_breakouts = [
            {
                "breakout_id": f"breakout/{system}",
                "system": system,
                "source_anchor": f"source/{system}",
                "egress_anchor": egress_ids[system],
                "wall_side": next(anchor["wall_side"] for anchor in egress_anchors if anchor["system"] == system),
            }
            for system in SYSTEM_ORDER
        ]
        for profile in profiles:
            terminal_anchors = [
                anchor
                for system in SYSTEM_ORDER
                for anchor in terminal_catalog[system][: profile["systems"][system]["terminal_count"]]
            ]
            anchor_ids = {
                anchor["anchor_id"]
                for anchor in [*source_anchors, *egress_anchors, *terminal_anchors]
            }
            connection_requests = []
            for anchor in terminal_anchors:
                system = anchor["system"]
                egress_id = egress_ids[system]
                start_anchor, end_anchor = (
                    (anchor["anchor_id"], egress_id)
                    if systems[system]["flow_direction"] == "terminal_to_source"
                    else (egress_id, anchor["anchor_id"])
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
            _validate_anchors(config, systems, source_anchors, egress_anchors, terminal_anchors)
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
                    "preflight": {
                        **preflight,
                        "all_egress_anchors_valid": True,
                        "all_terminal_anchors_valid": True,
                    },
                    "source_anchors": source_anchors,
                    "egress_anchors": egress_anchors,
                    "terminal_anchors": terminal_anchors,
                    "fixed_breakouts": fixed_breakouts,
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
    print("preflight case  plenum(m)  max-service-envelope(m)  egress  terminals  result")
    for case in cases:
        preflight = case["preflight"]
        passed = (
            preflight["all_service_envelopes_fit_plenum"]
            and preflight["all_egress_anchors_valid"]
            and preflight["all_terminal_anchors_valid"]
        )
        print(
            f"preflight {case['case_id']:<8} {preflight['plenum_height_m']:.2f}       "
            f"{preflight['maximum_required_service_vertical_envelope_m']:.3f}                    "
            f"{str(preflight['all_egress_anchors_valid']):<6}  "
            f"{str(preflight['all_terminal_anchors_valid']):<9}  "
            f"{'PASS' if passed else 'FAIL'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
