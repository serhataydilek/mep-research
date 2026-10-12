"""Immutable controlled endpoint-entry authorization for the synthetic benchmark (Phase 9D.5).

This policy may authorize a FREE grid cell as a *benchmark grid-search entry* for
explicit research modes.  It never mutates authoritative endpoint coordinates and
never certifies physical connector engagement.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .clearance_policy import ClearancePolicy, legacy_obstacle_margin_m
from .endpoint_entry import (
    EntryClassification,
    EndpointEntryDiagnosis,
    EndpointEntryReport,
    ObstructionKind,
)
from .spatial_evidence import AabbFidelity, OccupancyRole, SpatialEvidence
from .voxel import FREE

POLICY_ID = "BENCHMARK_CONTROLLED_ENTRY_V1"
POLICY_VERSION = 1
_LEGACY_OBSTACLE_MARGIN_CLASS = "STRUCTURAL_COLUMN"
_SEGMENT_SAMPLES = 8


class TransitionAuthorization(str, Enum):
    """Whether a transition may be used for benchmark grid search only."""

    AUTHORIZED_BENCHMARK_GRID_ENTRY = "AUTHORIZED_BENCHMARK_GRID_ENTRY"
    REJECTED_RAW_SOLID_PROXY = "REJECTED_RAW_SOLID_PROXY"
    REJECTED_ENTRY_CELL_NOT_FREE = "REJECTED_ENTRY_CELL_NOT_FREE"
    UNRESOLVED_EXACT_GEOMETRY = "UNRESOLVED_EXACT_GEOMETRY"
    UNRESOLVED_UNSUPPORTED_INPUT = "UNRESOLVED_UNSUPPORTED_INPUT"
    NOT_REQUIRED_ENDPOINT_USABLE = "NOT_REQUIRED_ENDPOINT_USABLE"
    NO_CANDIDATE = "NO_CANDIDATE"


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


def _point_in_aabb(point: tuple[float, float, float], bounds) -> bool:
    return (
        bounds.min_x <= point[0] <= bounds.max_x
        and bounds.min_y <= point[1] <= bounds.max_y
        and bounds.min_z <= point[2] <= bounds.max_z
    )


@dataclass(frozen=True, slots=True)
class ControlledEntryPolicy:
    """Named, immutable authorization rules — not a physical connector standard."""

    policy_id: str = POLICY_ID
    version: int = POLICY_VERSION
    authorize_clearance_inflation_only: bool = True
    require_entry_cell_free: bool = True
    max_transition_length_m: float = 0.25
    segment_sample_count: int = _SEGMENT_SAMPLES
    physical_connector_engagement_certified: bool = False

    def __post_init__(self) -> None:
        if self.policy_id != POLICY_ID or self.version != POLICY_VERSION:
            raise ValueError("Only BENCHMARK_CONTROLLED_ENTRY_V1 is defined in this milestone.")
        if self.physical_connector_engagement_certified:
            raise ValueError("Controlled entry policy cannot certify physical connector engagement.")
        if self.max_transition_length_m <= 0 or self.segment_sample_count < 2:
            raise ValueError("Transition limits must be positive length and at least two samples.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "authorize_clearance_inflation_only": self.authorize_clearance_inflation_only,
            "max_transition_length_m": _round(self.max_transition_length_m),
            "physical_connector_engagement_certified": False,
            "policy_id": self.policy_id,
            "require_entry_cell_free": self.require_entry_cell_free,
            "segment_sample_count": self.segment_sample_count,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class TransitionEvidence:
    endpoint_id: str
    system: str
    authoritative_world_m: tuple[float, float, float]
    proposed_entry_cell: tuple[int, int, int] | None
    proposed_entry_world_m: tuple[float, float, float] | None
    displacement_m: tuple[float, float, float] | None
    transition_length_m: float | None
    raw_solid_proxy_hits: tuple[str, ...]
    clearance_inflation_basis: tuple[str, ...]
    aabb_uncertainty: bool
    exact_geometry_evaluated: bool
    authorization: TransitionAuthorization
    authorization_reason: str
    physical_connector_engagement_certified: bool = False
    authoritative_coordinate_preserved: bool = True

    def __post_init__(self) -> None:
        if self.physical_connector_engagement_certified:
            raise ValueError("Transition evidence cannot certify connector engagement.")
        if not self.authoritative_coordinate_preserved:
            raise ValueError("Transition evidence must preserve the authoritative coordinate.")
        if self.exact_geometry_evaluated:
            raise ValueError("Exact solid geometry is not evaluated in this milestone.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "aabb_uncertainty": self.aabb_uncertainty,
            "authoritative_coordinate_preserved": True,
            "authoritative_world_m": [_round(v) for v in self.authoritative_world_m],
            "authorization": self.authorization.value,
            "authorization_reason": self.authorization_reason,
            "clearance_inflation_basis": list(self.clearance_inflation_basis),
            "displacement_m": None if self.displacement_m is None else [_round(v) for v in self.displacement_m],
            "endpoint_id": self.endpoint_id,
            "exact_geometry_evaluated": False,
            "physical_connector_engagement_certified": False,
            "proposed_entry_cell": None if self.proposed_entry_cell is None else list(self.proposed_entry_cell),
            "proposed_entry_world_m": None if self.proposed_entry_world_m is None else [_round(v) for v in self.proposed_entry_world_m],
            "raw_solid_proxy_hits": list(self.raw_solid_proxy_hits),
            "system": self.system,
            "transition_length_m": None if self.transition_length_m is None else _round(self.transition_length_m),
        }


@dataclass(frozen=True, slots=True)
class ControlledEntryDecision:
    policy: ControlledEntryPolicy
    evidence_frame_id: str
    policy_digest_sha256: str
    transitions: tuple[TransitionEvidence, ...]
    authorized_entry_cells: tuple[tuple[str, tuple[int, int, int]], ...]

    def __post_init__(self) -> None:
        ids = [item.endpoint_id for item in self.transitions]
        if ids != sorted(set(ids)):
            raise ValueError("Controlled entry transitions must be unique and ordered by endpoint ID.")
        authorized = dict(self.authorized_entry_cells)
        for item in self.transitions:
            if item.authorization is TransitionAuthorization.AUTHORIZED_BENCHMARK_GRID_ENTRY:
                if authorized.get(item.endpoint_id) != item.proposed_entry_cell:
                    raise ValueError("Authorized entry cells must match transition evidence.")
            elif item.endpoint_id in authorized:
                raise ValueError("Unauthorized transitions cannot appear in authorized_entry_cells.")

    def entry_cells(self) -> dict[str, tuple[int, int, int]]:
        return {endpoint_id: cell for endpoint_id, cell in self.authorized_entry_cells}

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "authorized_entry_cells": [
                {"endpoint_id": endpoint_id, "cell": list(cell)} for endpoint_id, cell in self.authorized_entry_cells
            ],
            "clearance_policy_digest_sha256": self.policy_digest_sha256,
            "evidence_frame_id": self.evidence_frame_id,
            "policy": self.policy.to_canonical_dict(),
            "summary": {
                "authorized_benchmark_grid_entry_count": sum(
                    item.authorization is TransitionAuthorization.AUTHORIZED_BENCHMARK_GRID_ENTRY for item in self.transitions
                ),
                "endpoint_count": len(self.transitions),
                "physical_connector_engagement_certified_count": 0,
                "rejected_count": sum(
                    item.authorization.value.startswith("REJECTED_") for item in self.transitions
                ),
                "unresolved_count": sum(
                    item.authorization.value.startswith("UNRESOLVED_") for item in self.transitions
                ),
            },
            "transitions": [item.to_canonical_dict() for item in self.transitions],
        }


def benchmark_controlled_entry_v1() -> ControlledEntryPolicy:
    return ControlledEntryPolicy()


def _segment_raw_hits(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    evidence: SpatialEvidence,
    samples: int,
) -> tuple[str, ...]:
    hits: set[str] = set()
    for index in range(samples):
        t = index / (samples - 1)
        point = (
            start[0] + t * (end[0] - start[0]),
            start[1] + t * (end[1] - start[1]),
            start[2] + t * (end[2] - start[2]),
        )
        for element in evidence.elements:
            if element.occupancy_role is OccupancyRole.BLOCKED_FIXED and _point_in_aabb(point, element.world_bounds):
                hits.add(element.global_id)
    return tuple(sorted(hits))


def evaluate_endpoint_transition(
    diagnosis: EndpointEntryDiagnosis,
    evidence: SpatialEvidence,
    clearance_policy: ClearancePolicy,
    policy: ControlledEntryPolicy,
) -> TransitionEvidence:
    """Evaluate one diagnosed entry candidate under the controlled benchmark policy."""
    if not isinstance(policy, ControlledEntryPolicy):
        raise ValueError("Transition evaluation requires ControlledEntryPolicy.")
    base = dict(
        endpoint_id=diagnosis.endpoint_id,
        system=diagnosis.system,
        authoritative_world_m=diagnosis.authoritative_world_m,
        proposed_entry_cell=None,
        proposed_entry_world_m=None,
        displacement_m=None,
        transition_length_m=None,
        raw_solid_proxy_hits=(),
        clearance_inflation_basis=(),
        aabb_uncertainty=diagnosis.aabb_uncertainty,
        exact_geometry_evaluated=False,
        physical_connector_engagement_certified=False,
        authoritative_coordinate_preserved=True,
    )
    if diagnosis.classification is EntryClassification.ENDPOINT_ALREADY_USABLE:
        return TransitionEvidence(
            **base,
            authorization=TransitionAuthorization.NOT_REQUIRED_ENDPOINT_USABLE,
            authorization_reason="Authoritative containing cell is already FREE; no entry transition is required.",
        )
    diagnosis_raw = tuple(
        item.global_id for item in diagnosis.obstacle_contributions if item.obstruction_kind is ObstructionKind.RAW_AABB_INTERSECTION
    )
    if diagnosis.obstruction_kind is ObstructionKind.RAW_AABB_INTERSECTION or diagnosis_raw:
        # Raw solid-proxy evidence rejects authorization even when no FREE entry candidate exists.
        if diagnosis.entry_candidate is not None:
            base.update(
                proposed_entry_cell=diagnosis.entry_candidate.cell,
                proposed_entry_world_m=diagnosis.entry_candidate.world_center_m,
                displacement_m=diagnosis.entry_candidate.displacement_m,
                transition_length_m=diagnosis.entry_candidate.distance_m,
            )
        base.update(
            raw_solid_proxy_hits=diagnosis_raw,
            clearance_inflation_basis=tuple(
                item.global_id for item in diagnosis.obstacle_contributions if item.obstruction_kind is ObstructionKind.CLEARANCE_INFLATION
            ),
        )
        return TransitionEvidence(
            **base,
            authorization=TransitionAuthorization.REJECTED_RAW_SOLID_PROXY,
            authorization_reason=(
                "Authoritative point intersects a BLOCKED_FIXED element AABB proxy; "
                "benchmark entry is rejected and exact solid intersection remains unevaluated."
            ),
        )
    if diagnosis.entry_candidate is None or diagnosis.classification is not EntryClassification.CANDIDATE_ENTRY_CELL_FOUND:
        return TransitionEvidence(
            **base,
            authorization=TransitionAuthorization.NO_CANDIDATE,
            authorization_reason=f"No entry candidate is available ({diagnosis.classification.value}).",
        )
    candidate = diagnosis.entry_candidate
    grid = evidence.grid_for(diagnosis.system)
    if grid is None:
        return TransitionEvidence(
            **base,
            authorization=TransitionAuthorization.UNRESOLVED_UNSUPPORTED_INPUT,
            authorization_reason="No compatible occupancy grid for the endpoint system.",
        )
    if policy.require_entry_cell_free and grid.cells[grid.spec.linear_index(*candidate.cell)] != FREE:
        return TransitionEvidence(
            **base,
            proposed_entry_cell=candidate.cell,
            proposed_entry_world_m=candidate.world_center_m,
            displacement_m=candidate.displacement_m,
            transition_length_m=candidate.distance_m,
            authorization=TransitionAuthorization.REJECTED_ENTRY_CELL_NOT_FREE,
            authorization_reason="Proposed entry cell is not FREE on the system occupancy grid.",
        )
    if candidate.distance_m > policy.max_transition_length_m + 1e-12:
        return TransitionEvidence(
            **base,
            proposed_entry_cell=candidate.cell,
            proposed_entry_world_m=candidate.world_center_m,
            displacement_m=candidate.displacement_m,
            transition_length_m=candidate.distance_m,
            authorization=TransitionAuthorization.UNRESOLVED_UNSUPPORTED_INPUT,
            authorization_reason=(
                f"Transition length {candidate.distance_m:.6f} m exceeds policy maximum "
                f"{policy.max_transition_length_m:.6f} m."
            ),
        )

    raw_hits = _segment_raw_hits(
        diagnosis.authoritative_world_m, candidate.world_center_m, evidence, policy.segment_sample_count
    )
    clearance_basis = tuple(
        item.global_id for item in diagnosis.obstacle_contributions if item.obstruction_kind is ObstructionKind.CLEARANCE_INFLATION
    )
    aabb_uncertainty = diagnosis.aabb_uncertainty or any(
        element.aabb_fidelity is not AabbFidelity.EXACT
        for element in evidence.elements
        if element.global_id in raw_hits or element.global_id in clearance_basis
    )
    base.update(
        proposed_entry_cell=candidate.cell,
        proposed_entry_world_m=candidate.world_center_m,
        displacement_m=candidate.displacement_m,
        transition_length_m=candidate.distance_m,
        raw_solid_proxy_hits=raw_hits,
        clearance_inflation_basis=clearance_basis,
        aabb_uncertainty=aabb_uncertainty,
    )
    if raw_hits:
        return TransitionEvidence(
            **base,
            authorization=TransitionAuthorization.REJECTED_RAW_SOLID_PROXY,
            authorization_reason=(
                "Transition segment or authoritative point intersects a BLOCKED_FIXED element AABB proxy; "
                "exact solid intersection remains unevaluated."
            ),
        )
    if diagnosis.obstruction_kind is ObstructionKind.CLEARANCE_INFLATION and policy.authorize_clearance_inflation_only:
        # Confirm inflation margins are the legacy Phase 3A STRUCTURAL_COLUMN margins (no ad-hoc relaxation).
        planar, vertical = legacy_obstacle_margin_m(clearance_policy, diagnosis.system, _LEGACY_OBSTACLE_MARGIN_CLASS)
        return TransitionEvidence(
            **base,
            authorization=TransitionAuthorization.AUTHORIZED_BENCHMARK_GRID_ENTRY,
            authorization_reason=(
                f"Authoritative cell is blocked only by Phase 3A clearance inflation "
                f"(planar={planar:.6f} m, vertical={vertical:.6f} m under {_LEGACY_OBSTACLE_MARGIN_CLASS}); "
                "benchmark grid entry is authorized. Physical connector engagement is NOT certified; "
                "exact geometry is unevaluated."
            ),
        )
    return TransitionEvidence(
        **base,
        authorization=TransitionAuthorization.UNRESOLVED_EXACT_GEOMETRY,
        authorization_reason=(
            f"Obstruction kind {diagnosis.obstruction_kind.value} cannot be safely authorized without exact geometry."
        ),
    )


def authorize_controlled_entries(
    report: EndpointEntryReport,
    evidence: SpatialEvidence,
    clearance_policy: ClearancePolicy,
    policy: ControlledEntryPolicy | None = None,
) -> ControlledEntryDecision:
    """Authorize benchmark grid-entry cells from an endpoint-entry report without rewriting anchors."""
    policy = policy or benchmark_controlled_entry_v1()
    if report.evidence_frame_id != evidence.coordinate_reference.frame_id:
        raise ValueError("Controlled entry authorization requires matching evidence frames.")
    transitions = tuple(
        evaluate_endpoint_transition(item, evidence, clearance_policy, policy) for item in report.diagnoses
    )
    authorized = tuple(
        (item.endpoint_id, item.proposed_entry_cell)
        for item in transitions
        if item.authorization is TransitionAuthorization.AUTHORIZED_BENCHMARK_GRID_ENTRY and item.proposed_entry_cell is not None
    )
    return ControlledEntryDecision(
        policy, evidence.coordinate_reference.frame_id, clearance_policy.digest_sha256, transitions, authorized
    )
