"""Deterministic architectural benchmark scenario metrics for future MEP research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import interior_grid_positions, load_building_config, shaft_clear_bounds, shaft_outer_bounds
from .ifc_model import create_ifc_model


def calculate_metrics(config: dict[str, Any]) -> dict[str, float | int]:
    """Calculate one-floor geometric metrics from an already validated config.

    The routing-volume proxy is geometric only; it is not available MEP routing
    volume because future discipline-specific envelopes are intentionally absent.
    """
    width = float(config["width_m"])
    length = float(config["length_m"])
    wall_thickness = float(config["wall_thickness_m"])
    column_size = float(config["column_size_m"])
    shaft = config["shaft"]
    gross_floor_area = width * length
    interior_floor_area = (width - 2 * wall_thickness) * (length - 2 * wall_thickness)
    column_count = len(interior_grid_positions(float(config["grid_spacing_x_m"]), width)) * len(
        interior_grid_positions(float(config["grid_spacing_y_m"]), length)
    )
    column_obstruction_area = column_count * column_size * column_size
    clear_bounds = shaft_clear_bounds(width, length, float(shaft["width_m"]), float(shaft["length_m"]))
    outer_min_x, outer_max_x, outer_min_y, outer_max_y = shaft_outer_bounds(
        clear_bounds, wall_thickness
    )
    shaft_outer_area = (outer_max_x - outer_min_x) * (outer_max_y - outer_min_y)
    fixed_obstruction_area = column_obstruction_area + shaft_outer_area
    plenum_height = float(config["floor_to_floor_m"]) - float(config["slab_thickness_m"]) - float(
        config["ceiling_height_m"]
    )
    if plenum_height <= 0:
        raise ValueError("Benchmark scenarios require a positive plenum_height_m.")
    return {
        "gross_floor_area_m2": gross_floor_area,
        "interior_floor_area_m2": interior_floor_area,
        "column_count_per_floor": column_count,
        "column_obstruction_area_m2": column_obstruction_area,
        "shaft_outer_area_m2": shaft_outer_area,
        "fixed_obstruction_area_m2": fixed_obstruction_area,
        "geometric_obstruction_ratio": fixed_obstruction_area / interior_floor_area,
        "plenum_height_m": plenum_height,
        "routing_volume_proxy_m3": interior_floor_area * plenum_height,
    }


def _verify_ifc_sanity(config: dict[str, Any]) -> None:
    """Ensure the unchanged Phase 1 generator supports a benchmark config."""
    model = create_ifc_model(config)
    repeat_model = create_ifc_model(config)
    floors = int(config["floors"])
    expected_columns = calculate_metrics(config)["column_count_per_floor"] * floors
    if model.schema != "IFC4":
        raise ValueError("Benchmark IFC model must use IFC4.")
    expected_counts = {
        "IfcSlab": floors,
        "IfcWall": 8 * floors,
        "IfcColumn": expected_columns,
        "IfcSpace": floors,
        "IfcOpeningElement": floors,
    }
    for entity_type, expected_count in expected_counts.items():
        elements = model.by_type(entity_type)
        if len(elements) != expected_count:
            raise ValueError(f"Benchmark IFC {entity_type} count does not match the scenario.")
        if not all(element.Representation for element in elements):
            raise ValueError(f"Benchmark IFC {entity_type} elements must have geometry.")
    first_ids = [entity.GlobalId for entity in model.by_type("IfcRoot")]
    repeat_ids = [entity.GlobalId for entity in repeat_model.by_type("IfcRoot")]
    if first_ids != repeat_ids:
        raise ValueError("Benchmark IFC semantic GlobalIds must be deterministic.")


def load_scenario_results(scenarios_directory: Path) -> list[dict[str, Any]]:
    """Load the manifest, validate each scenario, and calculate stable results."""
    manifest_path = scenarios_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Scenario manifest must contain a non-empty scenarios list.")

    results = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("Each scenario manifest entry must be an object.")
        scenario_id = scenario.get("scenario_id")
        config_file = scenario.get("config_file")
        intended_difficulty = scenario.get("intended_difficulty")
        description = scenario.get("description")
        if not all(
            isinstance(value, str) and value
            for value in (scenario_id, config_file, intended_difficulty, description)
        ):
            raise ValueError(
                "Scenario entries require scenario_id, config_file, intended_difficulty, and description."
            )
        config = load_building_config(scenarios_directory / config_file)
        _verify_ifc_sanity(config)
        results.append(
            {
                "scenario_id": scenario_id,
                "intended_difficulty": intended_difficulty,
                **calculate_metrics(config),
            }
        )

    ratios = [result["geometric_obstruction_ratio"] for result in results]
    if ratios != sorted(ratios):
        raise ValueError("Scenario geometric obstruction ratios must be non-decreasing.")
    return results


def write_scenario_metrics(scenarios_directory: Path, output_path: Path) -> list[dict[str, Any]]:
    """Write deterministic scenario metrics and return the calculated results."""
    results = load_scenario_results(scenarios_directory)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"scenarios": results}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return results


def main() -> int:
    """Generate deterministic benchmark metrics from a scenario directory."""
    parser = argparse.ArgumentParser(description="Generate deterministic building benchmark metrics.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    results = write_scenario_metrics(args.scenarios, args.output)
    print("ID   difficulty  footprint(m2)  columns  obstruction  plenum(m)  routing-volume(m3)")
    for result in results:
        print(
            f"{result['scenario_id']:<4} {result['intended_difficulty']:<11} "
            f"{result['gross_floor_area_m2']:.1f}       {result['column_count_per_floor']:<7} "
            f"{result['geometric_obstruction_ratio']:.6f}   {result['plenum_height_m']:.2f}       "
            f"{result['routing_volume_proxy_m3']:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
