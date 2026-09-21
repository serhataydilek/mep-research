"""IFC-derived, service-aware voxel occupancy for pre-routing benchmarks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from math import ceil, floor, sqrt
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom

from .benchmark import calculate_metrics
from .demand import generate_benchmark_cases, load_service_definitions, service_routing_margins
from .ifc_model import create_ifc_model


FREE = 0
BLOCKED_FIXED = 1
RESERVED_SHAFT = 2
BLOCKED_VERTICAL_BOUNDARY = 3
SYSTEM_ORDER = ("hvac", "drainage", "water", "fire", "electrical")


@dataclass(frozen=True)
class GridSpec:
    voxel_size_m: float
    origin_x: float
    origin_y: float
    origin_z: float
    nx: int
    ny: int
    nz: int

    @property
    def total_cell_count(self) -> int:
        return self.nx * self.ny * self.nz

    def linear_index(self, i: int, j: int, k: int) -> int:
        if not (0 <= i < self.nx and 0 <= j < self.ny and 0 <= k < self.nz):
            raise ValueError("Grid index is outside the routing domain.")
        return ((k * self.ny) + j) * self.nx + i

    def index_from_linear(self, index: int) -> tuple[int, int, int]:
        if not 0 <= index < self.total_cell_count:
            raise ValueError("Linear index is outside the routing domain.")
        i = index % self.nx
        remainder = index // self.nx
        return i, remainder % self.ny, remainder // self.ny

    def cell_center(self, i: int, j: int, k: int) -> tuple[float, float, float]:
        self.linear_index(i, j, k)
        size = self.voxel_size_m
        return (
            self.origin_x + (i + 0.5) * size,
            self.origin_y + (j + 0.5) * size,
            self.origin_z + (k + 0.5) * size,
        )

    def world_to_containing_cell(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        i = floor((x - self.origin_x) / self.voxel_size_m)
        j = floor((y - self.origin_y) / self.voxel_size_m)
        k = floor((z - self.origin_z) / self.voxel_size_m)
        self.linear_index(i, j, k)
        return i, j, k


@dataclass
class OccupancyGrid:
    scenario_id: str
    system: str
    spec: GridSpec
    cells: bytearray
    shaft_bounds: tuple[float, float, float, float, float, float]
    obstacle_counts: dict[str, int]

    def state_at(self, i: int, j: int, k: int) -> int:
        return self.cells[self.spec.linear_index(i, j, k)]


def _multiple(value: float, voxel_size: float, label: str) -> int:
    count = round(value / voxel_size)
    if count < 1 or abs(value - count * voxel_size) > 0.0000001:
        raise ValueError(f"{label} must be an integer multiple of routing.voxel_size_m.")
    return count


def grid_spec_from_config(config: dict[str, Any]) -> GridSpec:
    size = float(config["routing"]["voxel_size_m"])
    plenum = float(calculate_metrics(config)["plenum_height_m"])
    return GridSpec(
        voxel_size_m=size,
        origin_x=0.0,
        origin_y=0.0,
        origin_z=float(config["slab_thickness_m"]) + float(config["ceiling_height_m"]),
        nx=_multiple(float(config["width_m"]), size, "width_m"),
        ny=_multiple(float(config["length_m"]), size, "length_m"),
        nz=_multiple(plenum, size, "plenum_height_m"),
    )


def _bounds(model: ifcopenshell.file, element: ifcopenshell.entity_instance) -> tuple[float, float, float, float, float, float]:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, element)
    vertices = shape.geometry.verts
    if not vertices:
        raise ValueError(f"IFC element {element.Name} has no tessellated geometry.")
    return (
        min(vertices[::3]), max(vertices[::3]), min(vertices[1::3]), max(vertices[1::3]), min(vertices[2::3]), max(vertices[2::3]),
    )


def extract_ifc_obstacles(config: dict[str, Any]) -> tuple[list[tuple[str, tuple[float, float, float, float, float, float]]], tuple[float, float, float, float, float, float]]:
    """Extract axis-aligned bounds from actual tessellated Phase 1 IFC geometry.

    This AABB approximation is exact for the current orthogonal benchmark, not
    arbitrary rotated or curved IFC geometry.
    """
    model = create_ifc_model(config)
    obstacles = []
    for wall in model.by_type("IfcWall"):
        obstacles.append(("shaft_wall" if wall.Name.startswith("Shaft") else "perimeter_wall", _bounds(model, wall)))
    for column in model.by_type("IfcColumn"):
        obstacles.append(("structural_column", _bounds(model, column)))
    spaces = [space for space in model.by_type("IfcSpace") if space.Name == "Service Shaft 0"]
    if len(spaces) != 1:
        raise ValueError("IFC model must contain exactly one Service Shaft 0 space.")
    return obstacles, _bounds(model, spaces[0])


def _index_range(spec: GridSpec, low: float, high: float, axis: str) -> range:
    origin, count = {"x": (spec.origin_x, spec.nx), "y": (spec.origin_y, spec.ny), "z": (spec.origin_z, spec.nz)}[axis]
    size = spec.voxel_size_m
    start = max(0, ceil((low - origin) / size - 0.5 - 0.000000001))
    end = min(count - 1, floor((high - origin) / size - 0.5 + 0.000000001))
    return range(start, end + 1) if start <= end else range(0)


def _mark_aabb(grid: OccupancyGrid, bounds: tuple[float, float, float, float, float, float], state: int) -> None:
    xs = _index_range(grid.spec, bounds[0], bounds[1], "x")
    ys = _index_range(grid.spec, bounds[2], bounds[3], "y")
    zs = _index_range(grid.spec, bounds[4], bounds[5], "z")
    for k in zs:
        for j in ys:
            base = grid.spec.linear_index(0, j, k)
            for i in xs:
                index = base + i
                if state == BLOCKED_FIXED or grid.cells[index] != BLOCKED_FIXED:
                    grid.cells[index] = state


def build_occupancy_grid(
    scenario_id: str,
    config: dict[str, Any],
    system: str,
    definition: dict[str, Any],
    obstacles: list[tuple[str, tuple[float, float, float, float, float, float]]] | None = None,
    shaft_bounds: tuple[float, float, float, float, float, float] | None = None,
) -> OccupancyGrid:
    spec = grid_spec_from_config(config)
    if obstacles is None or shaft_bounds is None:
        obstacles, shaft_bounds = extract_ifc_obstacles(config)
    grid = OccupancyGrid(scenario_id, system, spec, bytearray(spec.total_cell_count), shaft_bounds, {
        "perimeter_wall": sum(kind == "perimeter_wall" for kind, _ in obstacles),
        "shaft_wall": sum(kind == "shaft_wall" for kind, _ in obstacles),
        "structural_column": sum(kind == "structural_column" for kind, _ in obstacles),
    })
    margins = service_routing_margins(definition, float(config["routing"]["clearance_m"]))
    vertical_margin = margins["required_vertical_margin_m"]
    for k in range(spec.nz):
        _, _, z = spec.cell_center(0, 0, k)
        if z - vertical_margin < spec.origin_z - 0.000000001 or z + vertical_margin > spec.origin_z + spec.nz * spec.voxel_size_m + 0.000000001:
            for j in range(spec.ny):
                start = spec.linear_index(0, j, k)
                grid.cells[start : start + spec.nx] = bytes([BLOCKED_VERTICAL_BOUNDARY]) * spec.nx
    planar_margin = margins["required_planar_margin_m"]
    for _, bounds in obstacles:
        inflated = (
            bounds[0] - planar_margin, bounds[1] + planar_margin,
            bounds[2] - planar_margin, bounds[3] + planar_margin,
            bounds[4] - vertical_margin, bounds[5] + vertical_margin,
        )
        _mark_aabb(grid, inflated, BLOCKED_FIXED)
    _mark_aabb(grid, shaft_bounds, RESERVED_SHAFT)
    return grid


def grid_summary(grid: OccupancyGrid) -> dict[str, Any]:
    counts = {state: grid.cells.count(state) for state in (FREE, BLOCKED_FIXED, RESERVED_SHAFT, BLOCKED_VERTICAL_BOUNDARY)}
    usable_layers = sum(any(grid.state_at(i, j, k) == FREE for j in range(grid.spec.ny) for i in range(grid.spec.nx)) for k in range(grid.spec.nz))
    return {
        "scenario_id": grid.scenario_id, "system": grid.system, "voxel_size_m": grid.spec.voxel_size_m,
        "nx": grid.spec.nx, "ny": grid.spec.ny, "nz": grid.spec.nz, "total_cell_count": grid.spec.total_cell_count,
        "free_cell_count": counts[FREE], "blocked_cell_count": counts[BLOCKED_FIXED],
        "reserved_shaft_cell_count": counts[RESERVED_SHAFT], "vertical_boundary_cell_count": counts[BLOCKED_VERTICAL_BOUNDARY],
        "free_cell_fraction": counts[FREE] / grid.spec.total_cell_count, "usable_z_layer_count": usable_layers,
    }


def snap_anchor_to_free_cell(grid: OccupancyGrid, anchor: dict[str, Any]) -> dict[str, Any]:
    original = (float(anchor["x_m"]), float(anchor["y_m"]), float(anchor["z_m"]))
    containing = grid.spec.world_to_containing_cell(*original)
    for radius in range(max(grid.spec.nx, grid.spec.ny, grid.spec.nz)):
        candidates = []
        for dz in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if abs(dx) + abs(dy) + abs(dz) != radius:
                        continue
                    index = (containing[0] + dx, containing[1] + dy, containing[2] + dz)
                    try:
                        if grid.state_at(*index) == FREE:
                            center = grid.spec.cell_center(*index)
                            candidates.append((sum((center[n] - original[n]) ** 2 for n in range(3)), index, center))
                    except ValueError:
                        continue
        if candidates:
            _, index, center = min(candidates, key=lambda item: (item[0], item[1]))
            distance = sqrt(sum((center[n] - original[n]) ** 2 for n in range(3)))
            if distance > 2 * grid.spec.voxel_size_m + 0.000000001:
                raise ValueError("Anchor snap exceeds the allowed two-voxel tolerance.")
            return {"anchor_id": anchor["anchor_id"], "original_world": list(original), "grid_index": list(index), "voxel_center": list(center), "snap_distance_m": distance}
    raise ValueError("No free voxel exists for routing-anchor snapping.")


def _require_egress_on_room_side(
    grid: OccupancyGrid, anchor: dict[str, Any], endpoint: dict[str, Any]
) -> None:
    """Reject an egress snap that crosses through or into the shaft."""
    x, y, _ = endpoint["voxel_center"]
    min_x, max_x, min_y, max_y, _, _ = grid.shaft_bounds
    side = anchor["wall_side"]
    room_side = {
        "north": y > max_y,
        "south": y < min_y,
        "east": x > max_x,
        "west": x < min_x,
    }.get(side)
    if not room_side:
        raise ValueError(
            f"Egress {anchor['anchor_id']} snapped off its {side} room side: "
            f"{endpoint['voxel_center']}"
        )


def _load_scenarios(directory: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    from .config import load_building_config
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    return [(entry, load_building_config(directory / entry["config_file"])) for entry in manifest["scenarios"]]


def build_occupancy_grids(
    scenarios_directory: Path, systems_path: Path
) -> dict[tuple[str, str], OccupancyGrid]:
    """Build the twenty immutable Phase 3A scenario/service occupancy grids."""
    systems = load_service_definitions(systems_path)
    grids = {}
    scenario_configs = _load_scenarios(scenarios_directory)
    geometry_by_scenario = {
        scenario["scenario_id"]: extract_ifc_obstacles(config)
        for scenario, config in scenario_configs
    }
    for scenario, config in scenario_configs:
        obstacles, shaft_bounds = geometry_by_scenario[scenario["scenario_id"]]
        for system in SYSTEM_ORDER:
            grids[(scenario["scenario_id"], system)] = build_occupancy_grid(
                scenario["scenario_id"], config, system, systems[system], obstacles, shaft_bounds
            )
    return grids


def build_voxel_summary(scenarios_directory: Path, demands_directory: Path, systems_path: Path) -> dict[str, Any]:
    grids = build_occupancy_grids(scenarios_directory, systems_path)
    scenario_configs = _load_scenarios(scenarios_directory)
    cases = generate_benchmark_cases(scenarios_directory, demands_directory, systems_path)
    mappings = []
    for case in cases:
        anchors = {anchor["anchor_id"]: anchor for anchor in [*case["egress_anchors"], *case["terminal_anchors"]]}
        for request in case["connection_requests"]:
            grid = grids[(case["scenario_id"], request["system"])]
            start = snap_anchor_to_free_cell(grid, anchors[request["start_anchor"]])
            end = snap_anchor_to_free_cell(grid, anchors[request["end_anchor"]])
            for endpoint in (start, end):
                anchor = anchors[endpoint["anchor_id"]]
                if endpoint["anchor_id"].startswith("egress/"):
                    _require_egress_on_room_side(grid, anchor, endpoint)
            mappings.append({"case_id": case["case_id"], "connection_id": request["connection_id"], "system": request["system"], "start": start, "end": end})
    return {
        "grid_summaries": [
            grid_summary(grids[(scenario["scenario_id"], system)])
            for scenario, _ in scenario_configs
            for system in SYSTEM_ORDER
        ],
        "endpoint_mappings": sorted(
            mappings, key=lambda mapping: (mapping["case_id"], mapping["connection_id"])
        ),
    }


def write_voxel_summary(scenarios_directory: Path, demands_directory: Path, systems_path: Path, output_path: Path) -> dict[str, Any]:
    summary = build_voxel_summary(scenarios_directory, demands_directory, systems_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate IFC-derived service-aware voxel summaries.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary = write_voxel_summary(args.scenarios, args.demands, args.systems, args.output)
    print("scenario system dimensions total free free% usable-z")
    for item in summary["grid_summaries"]:
        print(f"{item['scenario_id']} {item['system']:<10} {item['nx']}x{item['ny']}x{item['nz']} {item['total_cell_count']} {item['free_cell_count']} {item['free_cell_fraction']:.4f} {item['usable_z_layer_count']}")
    print("case                  connections  max-snap  endpoints-free")
    for case_id in sorted({item["case_id"] for item in summary["endpoint_mappings"]}):
        mappings = [item for item in summary["endpoint_mappings"] if item["case_id"] == case_id]
        max_snap = max(
            endpoint["snap_distance_m"]
            for mapping in mappings
            for endpoint in (mapping["start"], mapping["end"])
        )
        print(f"{case_id:21} {len(mappings):11}  {max_snap:8.3f}  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
