"""Local endpoint qualification. Allowances never drop a whole host or discipline."""

from __future__ import annotations

from typing import Any, Sequence


SURFACE_INTERFACE_TOLERANCE_M = 0.05
SOLID_CONTACT_M = 1e-3

QUALIFIED_FREE = "QUALIFIED_FREE"
QUALIFIED_BOUNDARY_ALLOWANCE = "QUALIFIED_BOUNDARY_ALLOWANCE"
QUALIFIED_LOCAL_REFINEMENT = "QUALIFIED_LOCAL_MESH_REFINEMENT"
REJECTED_EMBEDDED = "EMBEDDED_IN_NON_EXEMPT_SOLID"
REJECTED_CELL_SOLID = "CONTAINING_CELL_OCCUPIED_BY_NON_EXEMPT_SOLID"
REJECTED_UNRESOLVED = "UNRESOLVED_ENDPOINT_GEOMETRY"
REJECTED_OUTSIDE = "ENDPOINT_OUTSIDE_GRID"


def decide_terminal(
    *,
    point_m: Sequence[float],
    cell_index: Sequence[int] | None,
    cell_center_m: Sequence[float] | None,
    cell_blocked: bool,
    outside_grid: bool,
    boundary_host_global_id: str,
    blockers: list[dict[str, Any]],
    mesh_facts_by_id: dict[str, dict[str, Any]],
    openings_by_host: dict[str, list[dict[str, Any]]] | None = None,
    surface_tolerance_m: float = SURFACE_INTERFACE_TOLERANCE_M,
    solid_contact_m: float = SOLID_CONTACT_M,
) -> dict[str, Any]:
    """Qualify one immutable port. The returned point is always the input point."""
    openings_by_host = openings_by_host or {}
    base = {
        "immutable_point_m": [float(value) for value in point_m],
        "cell_index": None if cell_index is None else [int(value) for value in cell_index],
        "boundary_host_global_id": boundary_host_global_id,
        "relocated": False,
        "allowance": None,
        "false_aabb_obstacle_ids": [],
        "opening_notes": [],
    }
    if outside_grid or cell_index is None:
        return {**base, "status": REJECTED_OUTSIDE, "qualified": False, "release_cell": False}
    if cell_blocked and not blockers:
        return {**base, "status": REJECTED_UNRESOLVED, "qualified": False, "release_cell": False, "unresolved_global_id": None}
    exempt: list[str] = []
    allowance = None
    for blocker in blockers:
        global_id = blocker["global_id"]
        facts = mesh_facts_by_id.get(global_id)
        opening_notes = _opening_notes(global_id, openings_by_host.get(global_id, []), facts)
        base["opening_notes"].extend(opening_notes)
        if facts is None or not facts.get("resolved", False):
            return {
                **base,
                "status": REJECTED_UNRESOLVED,
                "qualified": False,
                "release_cell": False,
                "unresolved_global_id": global_id,
            }
        if _is_boundary_surface(global_id, boundary_host_global_id, facts, surface_tolerance_m):
            exempt.append(global_id)
            allowance = _allowance(point_m, cell_index, cell_center_m, global_id, facts, surface_tolerance_m)
            continue
        if facts.get("port_enclosed"):
            return {
                **base,
                "status": REJECTED_EMBEDDED,
                "qualified": False,
                "release_cell": False,
                "obstruction_global_id": global_id,
                "obstruction_ifc_class": blocker.get("ifc_class"),
                "mesh_facts": facts,
            }
        center_occupied = bool(facts.get("other_enclosed") or facts.get("other_on_surface")) or (
            facts.get("other_surface_distance_m") is not None and facts["other_surface_distance_m"] <= solid_contact_m
        )
        if center_occupied:
            return {
                **base,
                "status": REJECTED_CELL_SOLID,
                "qualified": False,
                "release_cell": False,
                "obstruction_global_id": global_id,
                "obstruction_ifc_class": blocker.get("ifc_class"),
                "mesh_facts": facts,
            }
        exempt.append(global_id)
    unique_exempt = sorted(set(exempt))
    if not cell_blocked and not blockers:
        return {**base, "status": QUALIFIED_FREE, "qualified": True, "release_cell": False, "exempt_global_ids": []}
    if allowance is not None and unique_exempt:
        return {
            **base,
            "status": QUALIFIED_BOUNDARY_ALLOWANCE,
            "qualified": True,
            "release_cell": True,
            "allowance": allowance,
            "exempt_global_ids": unique_exempt,
            "false_aabb_obstacle_ids": [item for item in unique_exempt if item != boundary_host_global_id],
        }
    if unique_exempt and not any(item["global_id"] not in unique_exempt for item in blockers):
        return {
            **base,
            "status": QUALIFIED_LOCAL_REFINEMENT,
            "qualified": True,
            "release_cell": cell_blocked,
            "exempt_global_ids": unique_exempt,
            "false_aabb_obstacle_ids": unique_exempt,
        }
    if not cell_blocked:
        return {**base, "status": QUALIFIED_FREE, "qualified": True, "release_cell": False, "exempt_global_ids": unique_exempt}
    return {
        **base,
        "status": REJECTED_CELL_SOLID,
        "qualified": False,
        "release_cell": False,
        "exempt_global_ids": unique_exempt,
    }


def execution_status_flags(
    *,
    cli_completed: bool,
    astar_invoked: bool,
    path_found: bool,
    validation_label: str | None,
) -> dict[str, Any]:
    """Keep process launch, search invocation, path result, and validation distinct."""
    return {
        "cli_completed": cli_completed,
        "astar_invoked": astar_invoked,
        "path_found": path_found,
        "geometric_validation_status": validation_label,
        "a_star_executed": bool(astar_invoked),
    }


def _is_boundary_surface(global_id: str, boundary_id: str, facts: dict[str, Any], tolerance_m: float) -> bool:
    distance = facts.get("port_surface_distance_m")
    return global_id == boundary_id and distance is not None and distance <= tolerance_m


def _allowance(
    point: Sequence[float],
    cell_index: Sequence[int],
    cell_center: Sequence[float] | None,
    host_id: str,
    facts: dict[str, Any],
    tolerance_m: float,
) -> dict[str, Any]:
    return {
        "kind": "RETAINED_BOUNDARY_HOST_CONTAINING_CELL",
        "host_global_id": host_id,
        "reason": (
            "The immutable port lies on the retained boundary host surface. "
            "Only this host is ignored in the port's containing cell. The rest of the host stays occupied."
        ),
        "port_m": [float(value) for value in point],
        "cell_index": [int(value) for value in cell_index],
        "cell_center_m": None if cell_center is None else [float(value) for value in cell_center],
        "surface_distance_m": facts.get("port_surface_distance_m"),
        "tolerance_m": tolerance_m,
        "whole_host_excluded": False,
        "discipline_or_system_excluded": False,
    }


def _opening_notes(
    host_id: str,
    openings: list[dict[str, Any]],
    facts: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not openings:
        return []
    enclosed = bool(facts and facts.get("port_enclosed"))
    return [{
        "host_global_id": host_id,
        "opening_global_ids": [item.get("opening_global_id") for item in openings],
        "relation_global_ids": [item.get("relation_global_id") for item in openings],
        "host_mesh_encloses_port": enclosed,
        "effect": (
            "The exported host mesh still encloses the port, so these openings do not clear this point."
            if enclosed
            else "Openings are recorded. The exported host mesh does not enclose the port at this XY."
        ),
    }]
