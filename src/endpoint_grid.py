"""World-to-grid terminal mapping that does not clamp or flatten ports.

Containing cells use the same ``floor((world - origin) / voxel)`` rule as
``GridSpec.world_to_containing_cell``. An index outside the grid stays outside.
A blocked containing cell is an endpoint failure, not a nearby free cell.
"""

from __future__ import annotations

from math import floor, sqrt
from typing import Iterable, Sequence


ENDPOINT_OK = "ENDPOINT_OK"
ENDPOINT_OUTSIDE_GRID = "ENDPOINT_OUTSIDE_GRID"
ENDPOINT_CELL_BLOCKED = "ENDPOINT_CELL_BLOCKED"
TERMINAL_RELOCATED = "TERMINAL_RELOCATED"
AXIS_ORDER = ("X", "Y", "Z")


# Binary metres such as 0.3 are not exact multiples of 0.1. One nanometre keeps
# a decimal voxel boundary in the cell that starts on that boundary.
FLOOR_EPSILON = 1e-9


def containing_index(
    origin: Sequence[float], voxel_size_m: float, point: Sequence[float],
) -> tuple[int, int, int]:
    """Return the unclamped voxel that contains ``point``.

    The index is ``floor((point - origin) / voxel + 1e-9)``. The epsilon
    corrects binary representation of decimal metre boundaries. It is not a
    clearance tolerance and does not move a point by a meaningful fraction of
    a 0.1 m voxel.
    """
    size = float(voxel_size_m)
    if size <= 0:
        raise ValueError("Voxel size must be positive.")
    return tuple(
        floor((float(point[axis]) - float(origin[axis])) / size + FLOOR_EPSILON) for axis in range(3)
    )


def cell_center(
    origin: Sequence[float], voxel_size_m: float, index: Sequence[int],
) -> tuple[float, float, float]:
    size = float(voxel_size_m)
    return tuple(float(origin[axis]) + (int(index[axis]) + 0.5) * size for axis in range(3))


def locate_containing_cell(
    origin: Sequence[float],
    voxel_size_m: float,
    counts: Sequence[int],
    point: Sequence[float],
) -> dict:
    """Map a world point to its containing cell without clamping it into the grid."""
    index = containing_index(origin, voxel_size_m, point)
    size = float(voxel_size_m)
    normalized = [(float(point[axis]) - float(origin[axis])) / size for axis in range(3)]
    outside = [axis for axis in range(3) if index[axis] < 0 or index[axis] >= int(counts[axis])]
    return {
        "point_m": [float(value) for value in point],
        "origin_m": [float(value) for value in origin],
        "voxel_size_m": size,
        "floor_epsilon": FLOOR_EPSILON,
        "normalized_coordinates": normalized,
        "containing_index": list(index),
        "outside_axes": outside,
        "status": ENDPOINT_OUTSIDE_GRID if outside else ENDPOINT_OK,
        "clamped": False,
        "projected": False,
        "relocated": False,
    }


def relocation_detected(containing: Sequence[int], used: Sequence[int]) -> dict:
    """Flag a search node that is not the point's containing cell."""
    same = all(int(containing[axis]) == int(used[axis]) for axis in range(3))
    return {
        "status": "NOT_RELOCATED" if same else TERMINAL_RELOCATED,
        "containing_index": [int(value) for value in containing],
        "used_index": [int(value) for value in used],
        "z_index_changed": int(containing[2]) != int(used[2]),
        "index_delta": [int(used[axis]) - int(containing[axis]) for axis in range(3)],
    }


def orthogonal_connector(
    start: Sequence[float], end: Sequence[float], *, tolerance_m: float = 1e-9,
) -> list[list[float]]:
    """Connect two points with X, then Y, then Z segments. Both endpoints are kept."""
    points = [[float(value) for value in start]]
    current = [float(value) for value in start]
    for axis in range(3):
        if abs(float(end[axis]) - current[axis]) <= tolerance_m:
            continue
        current[axis] = float(end[axis])
        points.append(list(current))
    if any(abs(points[-1][axis] - float(end[axis])) > tolerance_m for axis in range(3)):
        points.append([float(value) for value in end])
    return points


def assemble_physical_polyline(
    start_port: Sequence[float],
    voxel_centers: Sequence[Sequence[float]],
    end_port: Sequence[float],
) -> dict:
    """Build port → voxel-centre path → port, with explicit orthogonal stubs."""
    if not voxel_centers:
        raise ValueError("A physical route needs at least one voxel centre.")
    start_stub = orthogonal_connector(start_port, voxel_centers[0])
    end_stub = orthogonal_connector(voxel_centers[-1], end_port)
    middle = [[float(value) for value in point] for point in voxel_centers]
    polyline = start_stub[:-1] + middle + end_stub[1:]
    return {
        "polyline_m": polyline,
        "start_stub_m": start_stub,
        "end_stub_m": end_stub,
        "start_port_m": [float(value) for value in start_port],
        "end_port_m": [float(value) for value in end_port],
        "exact_port_to_port": _same_point(polyline[0], start_port) and _same_point(polyline[-1], end_port),
    }


def polyline_metrics(polyline: Sequence[Sequence[float]], *, tolerance_m: float = 1e-9) -> dict:
    """Length, bends, and cumulative absolute Z travel of a world polyline."""
    segments = []
    for index in range(len(polyline) - 1):
        delta = [float(polyline[index + 1][axis]) - float(polyline[index][axis]) for axis in range(3)]
        length = sqrt(sum(value * value for value in delta))
        if length <= tolerance_m:
            continue
        direction = tuple(0 if abs(value) <= tolerance_m else (1 if value > 0 else -1) for value in delta)
        segments.append({"delta_m": delta, "length_m": length, "direction": direction})
    bends = sum(segments[index]["direction"] != segments[index - 1]["direction"] for index in range(1, len(segments)))
    vertical = sum(abs(segment["delta_m"][2]) for segment in segments)
    net = sum(segment["delta_m"][2] for segment in segments)
    return {
        "segment_count": len(segments),
        "length_m": sum(segment["length_m"] for segment in segments),
        "bend_count": bends,
        "vertical_travel_m": vertical,
        "net_vertical_m": net,
        "orthogonal": all(sum(axis != 0 for axis in segment["direction"]) == 1 for segment in segments),
    }


def trace_relocated_terminals(
    origin: Sequence[float],
    voxel_size_m: float,
    start_port: Sequence[float],
    end_port: Sequence[float],
    start_proxy: Sequence[float],
    end_proxy: Sequence[float],
    used_start: Sequence[int],
    used_end: Sequence[int],
) -> dict:
    """Compare immutable ports, pre-grid proxies, and the nodes A* actually used."""
    start_cell = containing_index(origin, voxel_size_m, start_port)
    end_cell = containing_index(origin, voxel_size_m, end_port)
    start_proxy_cell = containing_index(origin, voxel_size_m, start_proxy)
    end_proxy_cell = containing_index(origin, voxel_size_m, end_proxy)
    return {
        "origin_m": [float(value) for value in origin],
        "voxel_size_m": float(voxel_size_m),
        "start_port_m": [float(value) for value in start_port],
        "end_port_m": [float(value) for value in end_port],
        "port_z_delta_m": float(end_port[2]) - float(start_port[2]),
        "start_containing_index": list(start_cell),
        "end_containing_index": list(end_cell),
        "start_containing_center_m": list(cell_center(origin, voxel_size_m, start_cell)),
        "end_containing_center_m": list(cell_center(origin, voxel_size_m, end_cell)),
        "start_proxy_m": [float(value) for value in start_proxy],
        "end_proxy_m": [float(value) for value in end_proxy],
        "start_proxy_containing_index": list(start_proxy_cell),
        "end_proxy_containing_index": list(end_proxy_cell),
        "used_start_index": [int(value) for value in used_start],
        "used_end_index": [int(value) for value in used_end],
        "start_relocation": relocation_detected(start_cell, used_start),
        "end_relocation": relocation_detected(end_cell, used_end),
        "proxy_start_relocation": relocation_detected(start_proxy_cell, used_start),
        "proxy_end_relocation": relocation_detected(end_proxy_cell, used_end),
        "containing_z_indices_differ": start_cell[2] != end_cell[2],
        "used_z_indices_equal": int(used_start[2]) == int(used_end[2]),
    }


def _same_point(first: Iterable[float], second: Sequence[float], tolerance_m: float = 1e-9) -> bool:
    left = list(first)
    return all(abs(left[axis] - float(second[axis])) <= tolerance_m for axis in range(3))
