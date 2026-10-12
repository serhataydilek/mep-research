"""Deterministic authoritative endpoint-to-grid entry diagnostics (Phase 9D.4).

This module explains why an endpoint's containing cell is FREE or blocked and,
when blocked, may propose a nearby FREE entry cell.  A proposal never replaces
the authoritative endpoint coordinate and never claims that a physical connector
can reach the entry cell.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import hypot
from typing import Any

from .clearance_policy import ClearancePolicy, legacy_obstacle_margin_m
from .endpoints import Endpoint, RoutingProblem
from .spatial_evidence import (
    AUTHORITATIVE_POSITION_PROVENANCE,
    AabbFidelity,
    CELL_STATE_NAMES,
    OccupancyRole,
    SpatialEvidence,
)
from .voxel import FREE, SYSTEM_ORDER, aabb_index_ranges

# Phase 3A occupancy marks every obstacle with the STRUCTURAL_COLUMN service margin.
_LEGACY_OBSTACLE_MARGIN_CLASS = "STRUCTURAL_COLUMN"
_DEFAULT_MAX_RADIUS_CELLS = 2
_DEFAULT_MAX_CANDIDATES = 8


class EntryClassification(str, Enum):
    ENDPOINT_ALREADY_USABLE = "ENDPOINT_ALREADY_USABLE"
    BLOCKED_BY_CLEARANCE_INFLATION = "BLOCKED_BY_CLEARANCE_INFLATION"
    POSSIBLE_PHYSICAL_OBSTRUCTION = "POSSIBLE_PHYSICAL_OBSTRUCTION"
    UNSUPPORTED_OR_AMBIGUOUS_GEOMETRY = "UNSUPPORTED_OR_AMBIGUOUS_GEOMETRY"
    CANDIDATE_ENTRY_CELL_FOUND = "CANDIDATE_ENTRY_CELL_FOUND"
    NO_ENTRY_FOUND_WITHIN_LIMITS = "NO_ENTRY_FOUND_WITHIN_LIMITS"
    OUTSIDE_GRID_DOMAIN = "OUTSIDE_GRID_DOMAIN"
    NO_COMPATIBLE_GRID = "NO_COMPATIBLE_GRID"
    COORDINATE_FRAME_MISMATCH = "COORDINATE_FRAME_MISMATCH"
    ENDPOINT_POSITION_NOT_AUTHORITATIVE = "ENDPOINT_POSITION_NOT_AUTHORITATIVE"


class ObstructionKind(str, Enum):
    NONE = "NONE"
    CLEARANCE_INFLATION = "CLEARANCE_INFLATION"
    RAW_AABB_INTERSECTION = "RAW_AABB_INTERSECTION"
    UNCLASSIFIED_OCCUPANCY = "UNCLASSIFIED_OCCUPANCY"


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


def _axis_outside(value: float, low: float, high: float) -> float:
    if value < low:
        return low - value
    if value > high:
        return value - high
    return 0.0


def _point_in_aabb(point: tuple[float, float, float], bounds) -> bool:
    return (
        bounds.min_x <= point[0] <= bounds.max_x
        and bounds.min_y <= point[1] <= bounds.max_y
        and bounds.min_z <= point[2] <= bounds.max_z
    )


@dataclass(frozen=True, slots=True)
class ObstacleContribution:
    global_id: str
    ifc_class: str
    classification: str
    occupancy_role: str
    aabb_fidelity: str
    raw_aabb_contains_point: bool
    cell_marked_by_inflated_raster: bool
    planar_margin_m: float
    vertical_margin_m: float
    distance_point_to_raw_aabb_m: tuple[float, float, float]
    obstruction_kind: ObstructionKind

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "aabb_fidelity": self.aabb_fidelity,
            "cell_marked_by_inflated_raster": self.cell_marked_by_inflated_raster,
            "classification": self.classification,
            "distance_point_to_raw_aabb_m": [_round(v) for v in self.distance_point_to_raw_aabb_m],
            "global_id": self.global_id,
            "ifc_class": self.ifc_class,
            "obstruction_kind": self.obstruction_kind.value,
            "occupancy_role": self.occupancy_role,
            "planar_margin_m": _round(self.planar_margin_m),
            "raw_aabb_contains_point": self.raw_aabb_contains_point,
            "vertical_margin_m": _round(self.vertical_margin_m),
        }


@dataclass(frozen=True, slots=True)
class EntryCandidate:
    """A FREE grid cell near an authoritative endpoint; not a connector validation."""

    cell: tuple[int, int, int]
    world_center_m: tuple[float, float, float]
    displacement_m: tuple[float, float, float]
    distance_m: float
    manhattan_cells: int
    connector_transition_validated: bool = False
    physical_connector_engagement_claimed: bool = False

    def __post_init__(self) -> None:
        if self.connector_transition_validated or self.physical_connector_engagement_claimed:
            raise ValueError("Entry candidates cannot claim connector engagement or a validated transition.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "cell": list(self.cell),
            "connector_transition_validated": False,
            "displacement_m": [_round(v) for v in self.displacement_m],
            "distance_m": _round(self.distance_m),
            "manhattan_cells": self.manhattan_cells,
            "physical_connector_engagement_claimed": False,
            "world_center_m": [_round(v) for v in self.world_center_m],
        }


@dataclass(frozen=True, slots=True)
class EndpointEntryDiagnosis:
    endpoint_id: str
    system: str
    authoritative_world_m: tuple[float, float, float]
    authoritative_position_preserved: bool
    evidence_frame_id: str
    policy_id: str
    policy_digest_sha256: str
    grid_cells_sha256: str | None
    classification: EntryClassification
    containing_cell: tuple[int, int, int] | None
    containing_cell_state: str | None
    obstruction_kind: ObstructionKind
    obstacle_contributions: tuple[ObstacleContribution, ...]
    aabb_uncertainty: bool
    opening_global_ids: tuple[str, ...]
    shaft_contains_point: bool
    entry_candidate: EntryCandidate | None
    explored_cell_count: int
    unresolved_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.authoritative_position_preserved:
            raise ValueError("Endpoint-entry diagnostics must preserve the authoritative coordinate.")
        if self.entry_candidate is not None and (
            self.entry_candidate.connector_transition_validated
            or self.entry_candidate.physical_connector_engagement_claimed
        ):
            raise ValueError("Endpoint-entry diagnostics cannot claim connector validation.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "aabb_uncertainty": self.aabb_uncertainty,
            "authoritative_position_preserved": True,
            "authoritative_world_m": [_round(v) for v in self.authoritative_world_m],
            "classification": self.classification.value,
            "containing_cell": None if self.containing_cell is None else list(self.containing_cell),
            "containing_cell_state": self.containing_cell_state,
            "endpoint_id": self.endpoint_id,
            "entry_candidate": None if self.entry_candidate is None else self.entry_candidate.to_canonical_dict(),
            "evidence_frame_id": self.evidence_frame_id,
            "explored_cell_count": self.explored_cell_count,
            "grid_cells_sha256": self.grid_cells_sha256,
            "obstacle_contributions": [item.to_canonical_dict() for item in self.obstacle_contributions],
            "obstruction_kind": self.obstruction_kind.value,
            "opening_global_ids": list(self.opening_global_ids),
            "policy_digest_sha256": self.policy_digest_sha256,
            "policy_id": self.policy_id,
            "shaft_contains_point": self.shaft_contains_point,
            "system": self.system,
            "unresolved_requirements": list(self.unresolved_requirements),
        }


@dataclass(frozen=True, slots=True)
class EndpointEntryReport:
    scenario_id: str
    evidence_frame_id: str
    endpoint_frame_id: str
    policy_id: str
    policy_digest_sha256: str
    max_radius_cells: int
    max_candidates: int
    diagnoses: tuple[EndpointEntryDiagnosis, ...]
    connector_engagement_certified: bool = False
    collision_free_claimed: bool = False

    def __post_init__(self) -> None:
        if self.connector_engagement_certified or self.collision_free_claimed:
            raise ValueError("Endpoint-entry reports cannot claim connector engagement or collision freedom.")
        ids = [item.endpoint_id for item in self.diagnoses]
        if ids != sorted(set(ids)):
            raise ValueError("Endpoint-entry diagnoses must be unique and ordered by endpoint ID.")

    def diagnosis_for(self, endpoint_id: str) -> EndpointEntryDiagnosis:
        for item in self.diagnoses:
            if item.endpoint_id == endpoint_id:
                return item
        raise KeyError(endpoint_id)

    def entry_cells(self) -> dict[str, tuple[int, int, int]]:
        """Return proposed FREE entry cells only; never mutates authoritative endpoints."""
        return {
            item.endpoint_id: item.entry_candidate.cell
            for item in self.diagnoses
            if item.entry_candidate is not None
            and item.classification is EntryClassification.CANDIDATE_ENTRY_CELL_FOUND
        }

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "collision_free_claimed": False,
            "connector_engagement_certified": False,
            "diagnoses": [item.to_canonical_dict() for item in self.diagnoses],
            "endpoint_frame_id": self.endpoint_frame_id,
            "evidence_frame_id": self.evidence_frame_id,
            "max_candidates": self.max_candidates,
            "max_radius_cells": self.max_radius_cells,
            "policy_digest_sha256": self.policy_digest_sha256,
            "policy_id": self.policy_id,
            "scenario_id": self.scenario_id,
            "summary": {
                "endpoint_count": len(self.diagnoses),
                **{
                    status.value.lower() + "_count": sum(item.classification is status for item in self.diagnoses)
                    for status in EntryClassification
                },
            },
        }


def _obstacle_contributions(
    endpoint: Endpoint,
    point: tuple[float, float, float],
    cell: tuple[int, int, int],
    evidence: SpatialEvidence,
    policy: ClearancePolicy,
) -> tuple[ObstacleContribution, ...]:
    planar, vertical = legacy_obstacle_margin_m(policy, endpoint.system.value, _LEGACY_OBSTACLE_MARGIN_CLASS)
    contributions: list[ObstacleContribution] = []
    for element in evidence.elements:
        if element.occupancy_role is not OccupancyRole.BLOCKED_FIXED:
            continue
        bounds = element.world_bounds
        raw = _point_in_aabb(point, bounds)
        inflated = (
            bounds.min_x - planar,
            bounds.max_x + planar,
            bounds.min_y - planar,
            bounds.max_y + planar,
            bounds.min_z - vertical,
            bounds.max_z + vertical,
        )
        grid = evidence.grid_for(endpoint.system.value)
        xs, ys, zs = aabb_index_ranges(grid.spec, inflated)
        marked = cell[0] in xs and cell[1] in ys and cell[2] in zs
        if not raw and not marked:
            continue
        if raw:
            kind = ObstructionKind.RAW_AABB_INTERSECTION
        elif marked:
            kind = ObstructionKind.CLEARANCE_INFLATION
        else:
            kind = ObstructionKind.UNCLASSIFIED_OCCUPANCY
        contributions.append(
            ObstacleContribution(
                element.global_id,
                element.ifc_class,
                element.classification.value,
                element.occupancy_role.value,
                element.aabb_fidelity.value,
                raw,
                marked,
                planar,
                vertical,
                (
                    _axis_outside(point[0], bounds.min_x, bounds.max_x),
                    _axis_outside(point[1], bounds.min_y, bounds.max_y),
                    _axis_outside(point[2], bounds.min_z, bounds.max_z),
                ),
                kind,
            )
        )
    return tuple(sorted(contributions, key=lambda item: (item.global_id, item.obstruction_kind.value)))


def _search_entry_candidate(
    grid,
    point: tuple[float, float, float],
    containing: tuple[int, int, int],
    *,
    max_radius_cells: int,
    max_candidates: int,
) -> tuple[EntryCandidate | None, int]:
    explored = 0
    found: list[EntryCandidate] = []
    for radius in range(0, max_radius_cells + 1):
        shell: list[EntryCandidate] = []
        for dz in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if abs(dx) + abs(dy) + abs(dz) != radius:
                        continue
                    cell = (containing[0] + dx, containing[1] + dy, containing[2] + dz)
                    explored += 1
                    try:
                        if grid.cells[grid.spec.linear_index(*cell)] != FREE:
                            continue
                    except (ValueError, IndexError):
                        continue
                    center = grid.spec.cell_center(*cell)
                    displacement = (center[0] - point[0], center[1] - point[1], center[2] - point[2])
                    shell.append(EntryCandidate(cell, center, displacement, hypot(*displacement), abs(dx) + abs(dy) + abs(dz)))
        shell.sort(key=lambda item: (item.distance_m, item.cell))
        for candidate in shell:
            found.append(candidate)
            if len(found) >= max_candidates:
                return found[0], explored
        if found:
            return min(found, key=lambda item: (item.distance_m, item.cell)), explored
    return None, explored


def diagnose_endpoint_entry(
    endpoint: Endpoint,
    evidence: SpatialEvidence,
    policy: ClearancePolicy,
    *,
    endpoint_frame_id: str,
    max_radius_cells: int = _DEFAULT_MAX_RADIUS_CELLS,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
) -> EndpointEntryDiagnosis:
    """Diagnose one authoritative endpoint against its service occupancy grid."""
    if max_radius_cells < 0 or max_candidates < 1:
        raise ValueError("Endpoint-entry search limits must be non-negative radius and positive candidate count.")
    point = (endpoint.position.x, endpoint.position.y, endpoint.position.z)
    base = dict(
        endpoint_id=endpoint.endpoint_id,
        system=endpoint.system.value,
        authoritative_world_m=point,
        authoritative_position_preserved=True,
        evidence_frame_id=evidence.coordinate_reference.frame_id,
        policy_id=policy.policy_id,
        policy_digest_sha256=policy.digest_sha256,
        grid_cells_sha256=None,
        containing_cell=None,
        containing_cell_state=None,
        obstruction_kind=ObstructionKind.NONE,
        obstacle_contributions=(),
        aabb_uncertainty=False,
        opening_global_ids=(),
        shaft_contains_point=any(shaft.world_bounds.contains_point(*point, tolerance=0.0) for shaft in evidence.shafts),
        entry_candidate=None,
        explored_cell_count=0,
    )
    if endpoint.provenance not in AUTHORITATIVE_POSITION_PROVENANCE:
        return EndpointEntryDiagnosis(
            **base,
            classification=EntryClassification.ENDPOINT_POSITION_NOT_AUTHORITATIVE,
            unresolved_requirements=("ENDPOINT_POSITION_NOT_AUTHORITATIVE",),
        )
    if endpoint_frame_id != evidence.coordinate_reference.frame_id:
        return EndpointEntryDiagnosis(
            **base,
            classification=EntryClassification.COORDINATE_FRAME_MISMATCH,
            unresolved_requirements=("COORDINATE_FRAME_MISMATCH",),
        )
    grid = evidence.grid_for(endpoint.system.value)
    if grid is None:
        return EndpointEntryDiagnosis(
            **base,
            classification=EntryClassification.NO_COMPATIBLE_GRID,
            unresolved_requirements=("NO_COMPATIBLE_GRID",),
        )
    base["grid_cells_sha256"] = grid.cells_sha256
    openings = tuple(
        sorted(
            opening.global_id
            for opening in evidence.openings
            if _point_in_aabb(point, opening.world_bounds)
        )
    )
    base["opening_global_ids"] = openings
    try:
        cell = grid.spec.world_to_containing_cell(*point)
    except ValueError:
        return EndpointEntryDiagnosis(
            **base,
            classification=EntryClassification.OUTSIDE_GRID_DOMAIN,
            unresolved_requirements=("ENDPOINT_OUTSIDE_GRID",),
        )  # containing cell remains None
    state = grid.cells[grid.spec.linear_index(*cell)]
    state_name = CELL_STATE_NAMES.get(state, str(state))
    contributions = _obstacle_contributions(endpoint, point, cell, evidence, policy)
    aabb_uncertainty = any(item.aabb_fidelity != AabbFidelity.EXACT.value for item in contributions)
    base.update(
        containing_cell=cell,
        containing_cell_state=state_name,
        obstacle_contributions=contributions,
        aabb_uncertainty=aabb_uncertainty,
    )
    raw_hits = [item for item in contributions if item.raw_aabb_contains_point]
    clearance_hits = [item for item in contributions if item.obstruction_kind is ObstructionKind.CLEARANCE_INFLATION]
    # A FREE containing cell does not cancel a raw AABB hit: the point may sit inside a thin
    # solid while the voxel centre used by Phase 3A occupancy remains free.
    if raw_hits:
        obstruction = ObstructionKind.RAW_AABB_INTERSECTION
        if any(item.aabb_fidelity != AabbFidelity.EXACT.value for item in raw_hits):
            prior = EntryClassification.UNSUPPORTED_OR_AMBIGUOUS_GEOMETRY
            unresolved = ("RAW_AABB_INTERSECTION_UNCERTAIN_FIDELITY", "EXACT_SOLID_INTERSECTION_UNEVALUATED")
        else:
            prior = EntryClassification.POSSIBLE_PHYSICAL_OBSTRUCTION
            unresolved = ("POSSIBLE_PHYSICAL_OBSTRUCTION", "EXACT_SOLID_INTERSECTION_UNEVALUATED")
    elif state == FREE:
        base["obstruction_kind"] = ObstructionKind.NONE
        return EndpointEntryDiagnosis(**base, classification=EntryClassification.ENDPOINT_ALREADY_USABLE, unresolved_requirements=())
    elif clearance_hits:
        obstruction = ObstructionKind.CLEARANCE_INFLATION
        prior = EntryClassification.BLOCKED_BY_CLEARANCE_INFLATION
        unresolved = ("BLOCKED_BY_CLEARANCE_INFLATION",)
        if aabb_uncertainty:
            unresolved = unresolved + ("AABB_FIDELITY_UNCERTAIN",)
    else:
        obstruction = ObstructionKind.UNCLASSIFIED_OCCUPANCY
        prior = EntryClassification.UNSUPPORTED_OR_AMBIGUOUS_GEOMETRY
        unresolved = ("UNCLASSIFIED_NON_FREE_CELL",)

    candidate, explored = _search_entry_candidate(
        grid, point, cell, max_radius_cells=max_radius_cells, max_candidates=max_candidates
    )
    base["explored_cell_count"] = explored
    base["obstruction_kind"] = obstruction
    base["entry_candidate"] = candidate
    if candidate is None:
        return EndpointEntryDiagnosis(
            **base,
            classification=EntryClassification.NO_ENTRY_FOUND_WITHIN_LIMITS,
            unresolved_requirements=unresolved + ("NO_ENTRY_FOUND_WITHIN_LIMITS", f"PRIOR_{prior.value}"),
        )
    return EndpointEntryDiagnosis(
        **base,
        classification=EntryClassification.CANDIDATE_ENTRY_CELL_FOUND,
        unresolved_requirements=unresolved
        + (
            "CONNECTOR_TRANSITION_TO_ENTRY_CELL_UNEVALUATED",
            "PHYSICAL_CONNECTOR_ENGAGEMENT_UNVALIDATED",
            "AUTHORITATIVE_ENDPOINT_COORDINATE_UNCHANGED",
            f"PRIOR_{prior.value}",
        ),
    )


def diagnose_routing_problem_endpoints(
    problem: RoutingProblem,
    evidence: SpatialEvidence,
    policy: ClearancePolicy,
    *,
    endpoint_frame_id: str,
    max_radius_cells: int = _DEFAULT_MAX_RADIUS_CELLS,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
    endpoint_ids: tuple[str, ...] | None = None,
) -> EndpointEntryReport:
    """Diagnose every (or selected) authoritative endpoint used by a routing problem."""
    if not isinstance(problem, RoutingProblem) or not isinstance(evidence, SpatialEvidence) or not isinstance(policy, ClearancePolicy):
        raise ValueError("Endpoint-entry diagnosis requires RoutingProblem, SpatialEvidence and ClearancePolicy.")
    selected = set(endpoint_ids) if endpoint_ids is not None else {
        endpoint_id
        for connection in problem.connections
        for endpoint_id in (connection.start_endpoint_id, connection.end_endpoint_id)
    }
    by_id = {endpoint.endpoint_id: endpoint for endpoint in problem.endpoints}
    missing = sorted(selected - set(by_id))
    if missing:
        raise ValueError(f"Unknown endpoint IDs in entry diagnosis request: {missing}.")
    diagnoses = tuple(
        diagnose_endpoint_entry(
            by_id[endpoint_id],
            evidence,
            policy,
            endpoint_frame_id=endpoint_frame_id,
            max_radius_cells=max_radius_cells,
            max_candidates=max_candidates,
        )
        for endpoint_id in sorted(selected)
    )
    return EndpointEntryReport(
        evidence.provenance.scenario_id,
        evidence.coordinate_reference.frame_id,
        endpoint_frame_id,
        policy.policy_id,
        policy.digest_sha256,
        max_radius_cells,
        max_candidates,
        diagnoses,
    )
