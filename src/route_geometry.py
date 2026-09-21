"""Shared deterministic raw-route geometry primitives for benchmark baselines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .voxel import GridSpec


@dataclass(frozen=True)
class Segment:
    """An axis-aligned world-space voxel-centerline segment."""

    start: tuple[float, float, float]
    end: tuple[float, float, float]


def compress_path_cells(path_cells: list[list[int]], spec: GridSpec) -> tuple[Segment, ...]:
    """Compress raw voxel cells into deterministic maximal collinear segments."""
    centers = [spec.cell_center(*cell) for cell in path_cells]
    if not centers:
        return ()
    if len(centers) == 1:
        return (Segment(centers[0], centers[0]),)
    segments = []
    start = centers[0]
    previous = centers[0]
    direction = tuple(centers[1][axis] - centers[0][axis] for axis in range(3))
    for current in centers[1:]:
        current_direction = tuple(current[axis] - previous[axis] for axis in range(3))
        if current_direction != direction:
            segments.append(Segment(start, previous))
            start = previous
            direction = current_direction
        previous = current
    segments.append(Segment(start, previous))
    return tuple(segments)


def nominal_half_extents(definition: dict[str, Any]) -> tuple[float, float]:
    """Return the existing Phase 2C planar and vertical service half extents."""
    if definition["envelope_type"] == "circular":
        half = float(definition["diameter_m"]) / 2
        return half, half
    return (
        max(float(definition["width_m"]), float(definition["height_m"])) / 2,
        float(definition["height_m"]) / 2,
    )


def swept_segment_aabb(
    segment: Segment, planar_half_extent: float, vertical_half_extent: float, extra: float = 0.0
) -> tuple[float, float, float, float, float, float]:
    """Create a nominal (or uniformly expanded) swept centerline AABB."""
    planar = planar_half_extent + extra
    vertical = vertical_half_extent + extra
    return (
        min(segment.start[0], segment.end[0]) - planar,
        max(segment.start[0], segment.end[0]) + planar,
        min(segment.start[1], segment.end[1]) - planar,
        max(segment.start[1], segment.end[1]) + planar,
        min(segment.start[2], segment.end[2]) - vertical,
        max(segment.start[2], segment.end[2]) + vertical,
    )
