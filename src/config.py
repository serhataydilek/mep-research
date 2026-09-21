"""Configuration loading for the parametric building model."""

from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import Any


def _require_value(config: dict[str, Any], field: str, *, label: str | None = None) -> Any:
    field_label = label or field
    if field not in config:
        raise ValueError(f"Missing required field: {field_label}")
    return config[field]


def _require_number(
    config: dict[str, Any],
    field: str,
    *,
    minimum: float,
    label: str | None = None,
) -> float:
    field_label = label or field
    value = _require_value(config, field, label=field_label)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_label} must be numeric.")
    if not isfinite(value) or value <= minimum:
        raise ValueError(f"{field_label} must be greater than {minimum}.")
    return float(value)


def _require_object(config: dict[str, Any], field: str, *, label: str | None = None) -> dict[str, Any]:
    field_label = label or field
    value = _require_value(config, field, label=field_label)
    if not isinstance(value, dict):
        raise ValueError(f"{field_label} must be an object.")
    return value


def interior_grid_positions(spacing: float, extent: float) -> list[tuple[int, float]]:
    """Return interior grid indices and coordinates in deterministic order."""
    positions: list[tuple[int, float]] = []
    index = 0
    coordinate = spacing
    while coordinate < extent:
        positions.append((index, coordinate))
        index += 1
        coordinate = (index + 1) * spacing
    return positions


def load_building_config(path: Path) -> dict[str, Any]:
    """Load and validate the current parametric building configuration."""
    with path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)

    if not isinstance(config, dict):
        raise ValueError("Building configuration must be a JSON object.")

    floors = _require_value(config, "floors")
    if isinstance(floors, bool) or not isinstance(floors, int) or floors < 1:
        raise ValueError("floors must be an integer greater than or equal to 1.")

    width = _require_number(config, "width_m", minimum=0)
    length = _require_number(config, "length_m", minimum=0)
    floor_to_floor = _require_number(config, "floor_to_floor_m", minimum=0)
    ceiling_height = _require_number(config, "ceiling_height_m", minimum=0)
    slab_thickness = _require_number(config, "slab_thickness_m", minimum=0)
    wall_thickness = _require_number(config, "wall_thickness_m", minimum=0)
    column_size = _require_number(config, "column_size_m", minimum=0)
    grid_spacing_x = _require_number(config, "grid_spacing_x_m", minimum=0)
    grid_spacing_y = _require_number(config, "grid_spacing_y_m", minimum=0)

    if ceiling_height >= floor_to_floor:
        raise ValueError("ceiling_height_m must be less than floor_to_floor_m.")
    if slab_thickness >= floor_to_floor:
        raise ValueError("slab_thickness_m must be less than floor_to_floor_m.")
    if wall_thickness >= width or wall_thickness >= length:
        raise ValueError("wall_thickness_m must be less than both width_m and length_m.")
    if grid_spacing_x >= width:
        raise ValueError("grid_spacing_x_m must be less than width_m.")
    if grid_spacing_y >= length:
        raise ValueError("grid_spacing_y_m must be less than length_m.")
    if column_size >= grid_spacing_x:
        raise ValueError("column_size_m must be less than grid_spacing_x_m.")
    if column_size >= grid_spacing_y:
        raise ValueError("column_size_m must be less than grid_spacing_y_m.")

    x_positions = interior_grid_positions(grid_spacing_x, width)
    y_positions = interior_grid_positions(grid_spacing_y, length)
    if not x_positions or not y_positions:
        raise ValueError("Configured grid must produce at least one interior column position.")

    half_column = column_size / 2
    for axis, positions, extent in (
        ("X", x_positions, width),
        ("Y", y_positions, length),
    ):
        for _, coordinate in positions:
            minimum = coordinate - half_column
            maximum = coordinate + half_column
            if minimum < wall_thickness or maximum > extent - wall_thickness:
                raise ValueError(
                    f"Configured column footprint at grid {axis}={coordinate} "
                    "overlaps the perimeter wall volume."
                )

    shaft = _require_object(config, "shaft")
    shaft_width = _require_number(shaft, "width_m", minimum=0, label="shaft.width_m")
    shaft_length = _require_number(shaft, "length_m", minimum=0, label="shaft.length_m")
    shaft_position = _require_value(shaft, "position", label="shaft.position")
    if shaft_position != "center":
        raise ValueError('shaft.position must be "center".')
    if shaft_width > width or shaft_length > length:
        raise ValueError("shaft dimensions must fit inside the building footprint.")

    routing = _require_object(config, "routing")
    _require_number(routing, "voxel_size_m", minimum=0, label="routing.voxel_size_m")
    clearance = _require_value(routing, "clearance_m", label="routing.clearance_m")
    if isinstance(clearance, bool) or not isinstance(clearance, (int, float)):
        raise ValueError("routing.clearance_m must be numeric.")
    if not isfinite(clearance) or clearance < 0:
        raise ValueError("routing.clearance_m must be greater than or equal to 0.")

    return config
