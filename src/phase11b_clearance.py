"""Clearance labels and request selection for the Phase 11B real-IFC benchmark.

The synthetic benchmark's 0.05 m value is an inter-system rule. It is not a
DigitalHub building-code clearance. Sensitivity cases stay labeled as such.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Sequence


SENSITIVITY_ADDITIONAL_CLEARANCE_M = (0.0, 0.025, 0.05, 0.1)
BENCHMARK_V1_INTER_SYSTEM_CLEARANCE_M = 0.05
VOXEL_SIZE_M = 0.1
FINER_VOXEL_SIZE_M = 0.05
COVERING_CONTACT_BASELINE_M = 1e-3
MIN_SHARED_ROI_AXIS_M = 0.5

BASELINE_MODE = "BASELINE_BODY_AABB_1MM_COVERING"
CLEARANCE_AWARE_MODE = "CLEARANCE_AWARE_BODY_PLUS_DECLARED_MARGIN"
FINER_GRID_MODE = "FINER_GRID_BODY_ENVELOPE_SENSITIVITY"

SEARCH_PATH_FOUND = "SEARCH_PATH_FOUND"
SEARCH_NO_PATH = "SEARCH_NO_PATH"
ENDPOINT_INVALID = "ENDPOINT_INVALID"
ENDPOINT_BLOCKED = "ENDPOINT_BLOCKED"
BROAD_PHASE_CANDIDATE = "BROAD_PHASE_CANDIDATE"
CONFIRMED_GEOMETRIC_COLLISION = "CONFIRMED_GEOMETRIC_COLLISION"
CLEARANCE_VIOLATION = "CLEARANCE_VIOLATION"
GEOMETRIC_VALIDATION_INCOMPLETE = "GEOMETRIC_VALIDATION_INCOMPLETE"
PHYSICALLY_ACCEPTABLE = "PHYSICALLY_ACCEPTABLE_UNDER_DECLARED_POLICY"
BOUNDARY_HOST_CONNECTION_CONTACT = "BOUNDARY_HOST_CONNECTION_CONTACT"
SEPARATED = "SEPARATED_UNDER_DECLARED_POLICY"

CLEARANCE_POLICY = {
    "project_building_clearance_defined": False,
    "building_code_compliance_claimed": False,
    "reason": (
        "BENCHMARK_V1 defines 0.05 m only between distinct synthetic systems. "
        "Same-system clearance is UNDEFINED. No DigitalHub duct-to-architecture clearance is declared."
    ),
    "benchmark_v1_inter_system_m": BENCHMARK_V1_INTER_SYSTEM_CLEARANCE_M,
    "same_system_status": "UNDEFINED",
    "sensitivity_additional_clearance_m": list(SENSITIVITY_ADDITIONAL_CLEARANCE_M),
    "sensitivity_label": "NOT_A_BUILDING_CODE_CLAIM",
    "baseline_occupancy": (
        "Obstacle AABBs are inflated by the measured route-body half-extent. "
        "Covering and proxy cells are blocked only when the cell centre is enclosed or within 1 mm of the mesh. "
        "Walls, slabs, and MEP keep their inflated AABBs."
    ),
    "clearance_aware_occupancy": (
        "AABB inflation and covering/proxy mesh contact both equal the body half-extent plus the declared "
        "additional clearance. The frozen A* implementation is unchanged."
    ),
    "boundary_host_exception": (
        "Only the containing cell of a retained boundary host may be released, and only for that host. "
        "Samples in that cell are connection contact. The rest of the route is still checked against that host."
    ),
    "validation": (
        "The full physical polyline, including connector stubs and bends, is tested. "
        "Swept AABBs select candidates. Mesh surface distance of centreline samples then decides enclosure "
        "or clearance. Required separation is the body half-extent plus the declared additional clearance. "
        "Absence of a confirmed intersection is not sufficient clearance."
    ),
    "zero_aabb_hits": "ZERO_BROAD_PHASE_CANDIDATES does not prove clearance. Those routes stay validation-incomplete.",
    "voxel_limitation_m": VOXEL_SIZE_M,
    "voxel_limitation": (
        "A 0.1 m grid can miss features narrower than one cell. Occupancy uses cell centres, so a free cell "
        "can still be closer to a solid than the route body. Mesh validation of the reconstructed polyline "
        "is the geometric check. A 0.05 m run is a localized sensitivity when the adapter cell limit allows it."
    ),
}


def phase11b_sort_key(preview: dict[str, Any]) -> tuple:
    """Prefer a 2–8 m separation inside real obstacles. Slab AABBs are only a tie-break."""
    distance = float(preview["straight_line_m"])
    band = 2.0 <= distance <= 8.0
    return (
        0 if band else 1,
        0 if preview.get("same_system") else 1,
        0 if preview.get("same_storey") else 1,
        -int(preview.get("approximate_obstacle_count") or 0),
        0 if not preview.get("port_inside_slab_aabb") else 1,
        abs(distance - 5.0),
        tuple(preview["replaced_host_global_ids"]),
        tuple(preview["boundary_host_global_ids"]),
    )


def phase11b_rank(
    previews: Sequence[dict[str, Any]],
    discipline: str,
    *,
    skip_replaced: Sequence[Sequence[str]] = (),
    prefer_replaced: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic per-discipline order. A skipped identity is never revived."""
    skipped = {frozenset(item) for item in skip_replaced}
    chosen = [
        preview for preview in previews
        if preview.get("discipline") == discipline and frozenset(preview["replaced_host_global_ids"]) not in skipped
    ]
    chosen.sort(key=phase11b_sort_key)
    if prefer_replaced is not None:
        preferred = frozenset(prefer_replaced)
        front = [preview for preview in chosen if frozenset(preview["replaced_host_global_ids"]) == preferred]
        rest = [preview for preview in chosen if frozenset(preview["replaced_host_global_ids"]) != preferred]
        chosen = front + rest
    return chosen


def same_point(left: Sequence[float], right: Sequence[float], tolerance_m: float = 1e-9) -> bool:
    return all(abs(float(left[axis]) - float(right[axis])) <= tolerance_m for axis in range(3))


def point_in_allowance_cell(point: Sequence[float], allowance: dict[str, Any], voxel_size_m: float = VOXEL_SIZE_M) -> bool:
    center = allowance.get("cell_center_m")
    if center is None:
        return False
    half = voxel_size_m / 2.0
    return all(abs(float(point[axis]) - float(center[axis])) <= half + 1e-9 for axis in range(3))


def classify_proximity(
    *,
    distance_m: float | None,
    required_separation_m: float,
    enclosed: bool,
    obstacle_is_boundary_host: bool,
    sample_inside_boundary_allowance_cell: bool,
    measurement: str,
) -> str:
    """Separate a legitimate interface sample from the rest of the route.

    ``measurement`` records whether the distance came from a mesh or an AABB.
    A sample that is not enclosed is not automatically clear of the body.
    """
    if measurement not in {"MESH_SURFACE_DISTANCE", "AABB_BROAD_PHASE"}:
        raise ValueError(f"Unknown proximity measurement: {measurement}")
    if sample_inside_boundary_allowance_cell and obstacle_is_boundary_host:
        return BOUNDARY_HOST_CONNECTION_CONTACT
    if enclosed and measurement == "MESH_SURFACE_DISTANCE":
        return CONFIRMED_GEOMETRIC_COLLISION
    if distance_m is not None and distance_m + 1e-6 < required_separation_m:
        return CLEARANCE_VIOLATION
    if measurement == "AABB_BROAD_PHASE":
        return BROAD_PHASE_CANDIDATE
    return SEPARATED


def search_state(*, astar_invoked: bool, path_found: bool, endpoint_statuses: Sequence[str]) -> str:
    if not astar_invoked:
        if any(status == "ENDPOINT_OUTSIDE_GRID" for status in endpoint_statuses):
            return ENDPOINT_INVALID
        return ENDPOINT_BLOCKED
    if path_found:
        return SEARCH_PATH_FOUND
    return SEARCH_NO_PATH


def geometric_state(validation_label: str | None, path_found: bool) -> str | None:
    """A found voxel path is acceptable only when the mesh check supports it."""
    if not path_found:
        return None
    if validation_label == "CONFIRMED_MESH_INTERSECTION":
        return CONFIRMED_GEOMETRIC_COLLISION
    if validation_label == "CLEARANCE_VIOLATION":
        return CLEARANCE_VIOLATION
    if validation_label == "MESH_SEPARATED_BROAD_PHASE_CANDIDATES":
        return PHYSICALLY_ACCEPTABLE
    return GEOMETRIC_VALIDATION_INCOMPLETE


def box_intersection(boxes: Sequence[Sequence[float]]) -> tuple[float, float, float, float, float, float] | None:
    if not boxes:
        return None
    acc = tuple(float(value) for value in boxes[0])
    for box in boxes[1:]:
        acc = (
            max(acc[0], float(box[0])), min(acc[1], float(box[1])),
            max(acc[2], float(box[2])), min(acc[3], float(box[3])),
            max(acc[4], float(box[4])), min(acc[5], float(box[5])),
        )
        if any(acc[axis * 2] >= acc[axis * 2 + 1] for axis in range(3)):
            return None
    return acc


def shared_roi_group(items: Sequence[dict[str, Any]], minimum_axis_m: float = MIN_SHARED_ROI_AXIS_M) -> list[str] | None:
    """Return the first three request ids whose ROIs share a common box, if any."""
    usable = [item for item in items if item.get("roi_bounds_m")]
    for group in combinations(usable, 3):
        intersection = box_intersection([item["roi_bounds_m"] for item in group])
        if intersection is None:
            continue
        spans = [intersection[axis * 2 + 1] - intersection[axis * 2] for axis in range(3)]
        if min(spans) >= minimum_axis_m:
            return [item["request_id"] for item in group]
    return None
