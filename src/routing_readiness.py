"""Read-only Phase 9C.3 preflight for the first physical-edge routing attempt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .clearance_policy import ClearancePolicy
from .junction_placement import CandidateJunctionPlacement, JunctionPlacementStatus
from .spatial_evidence import AUTHORITATIVE_POSITION_PROVENANCE, SpatialEvidence, SpatialEvidenceStatus
from .topology import TopologyNodeKind
from .topology_planner import TopologyCandidate


@dataclass(frozen=True, slots=True)
class ReadinessFinding:
    code: str
    subject_id: str | None
    blocking: bool

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"blocking": self.blocking, "code": self.code, "subject_id": self.subject_id}


@dataclass(frozen=True, slots=True)
class RoutingReadinessAssessment:
    candidate_id: str
    evidence_frame_id: str
    policy_id: str
    policy_digest_sha256: str
    authoritative_connection_ids: tuple[str, ...]
    endpoint_coordinates_available: bool
    coordinate_frame_valid: bool
    relevant_grids_available: bool
    junction_placements_available: bool
    shared_trunk_geometry_unresolved: bool
    bounded_edge_routing_can_start: bool
    physical_feasibility_claimed: bool = False
    collision_free_claimed: bool = False
    findings: tuple[ReadinessFinding, ...] = ()

    def __post_init__(self) -> None:
        if self.physical_feasibility_claimed or self.collision_free_claimed:
            raise ValueError("Routing readiness cannot claim physical feasibility or collision freedom.")
        if self.findings != tuple(sorted(self.findings, key=lambda item: (item.code, item.subject_id or "", item.blocking))):
            raise ValueError("Routing readiness findings must be deterministically ordered.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "authoritative_connection_ids": list(self.authoritative_connection_ids),
            "bounded_edge_routing_can_start": self.bounded_edge_routing_can_start,
            "candidate_id": self.candidate_id,
            "collision_free_claimed": False,
            "coordinate_frame_valid": self.coordinate_frame_valid,
            "endpoint_coordinates_available": self.endpoint_coordinates_available,
            "evidence_frame_id": self.evidence_frame_id,
            "findings": [item.to_canonical_dict() for item in self.findings],
            "junction_placements_available": self.junction_placements_available,
            "physical_feasibility_claimed": False,
            "policy_digest_sha256": self.policy_digest_sha256,
            "policy_id": self.policy_id,
            "relevant_grids_available": self.relevant_grids_available,
            "shared_trunk_geometry_unresolved": self.shared_trunk_geometry_unresolved,
        }


def assess_routing_readiness(
    candidate: TopologyCandidate,
    evidence: SpatialEvidence,
    policy: ClearancePolicy,
    *,
    endpoint_frame_id: str,
    junction_placement: CandidateJunctionPlacement | None = None,
) -> RoutingReadinessAssessment:
    """State whether inputs exist for a bounded edge search, never whether it will succeed."""
    if not isinstance(candidate, TopologyCandidate) or not isinstance(evidence, SpatialEvidence) or not isinstance(policy, ClearancePolicy):
        raise ValueError("Readiness requires a topology candidate, spatial evidence, and clearance policy.")
    findings: list[ReadinessFinding] = []
    frame_valid = endpoint_frame_id == evidence.coordinate_reference.frame_id
    if not frame_valid:
        findings.append(ReadinessFinding("COORDINATE_FRAME_MISMATCH", None, True))
    evidence_available = evidence.status is SpatialEvidenceStatus.EVIDENCE_AVAILABLE
    if not evidence_available:
        findings.append(ReadinessFinding("SPATIAL_EVIDENCE_NOT_USABLE", None, True))

    endpoint_coordinates = True
    grids_available = True
    for endpoint in candidate.demand.problem.endpoints:
        if endpoint.provenance not in AUTHORITATIVE_POSITION_PROVENANCE:
            endpoint_coordinates = False
            findings.append(ReadinessFinding("ENDPOINT_POSITION_NOT_AUTHORITATIVE", endpoint.endpoint_id, True))
        grid = evidence.grid_for(endpoint.system.value)
        if grid is None:
            grids_available = False
            findings.append(ReadinessFinding("NO_COMPATIBLE_GRID", endpoint.endpoint_id, True))
        else:
            try:
                grid.spec.world_to_containing_cell(endpoint.position.x, endpoint.position.y, endpoint.position.z)
            except ValueError:
                endpoint_coordinates = False
                findings.append(ReadinessFinding("ENDPOINT_OUTSIDE_GRID", endpoint.endpoint_id, True))
            else:
                if grid.clearance_policy_digest_sha256 != policy.digest_sha256:
                    findings.append(ReadinessFinding("CLEARANCE_POLICY_DIGEST_MISMATCH", endpoint.endpoint_id, True))

    virtual_nodes = [node for node in candidate.demand.topology.nodes if node.kind is TopologyNodeKind.JUNCTION and node.endpoint_id is None]
    junctions_available = not virtual_nodes
    if virtual_nodes:
        if junction_placement is None:
            findings.extend(ReadinessFinding("JUNCTION_PLACEMENT_MISSING", node.node_id, True) for node in virtual_nodes)
        elif junction_placement.candidate_id != candidate.candidate_id:
            findings.append(ReadinessFinding("JUNCTION_PLACEMENT_CANDIDATE_MISMATCH", junction_placement.candidate_id, True))
        else:
            by_node = {item.junction_node_id: item for item in junction_placement.junctions}
            junctions_available = all(
                node.node_id in by_node and by_node[node.node_id].status is JunctionPlacementStatus.PROPOSALS_GENERATED
                for node in virtual_nodes
            )
            if not junctions_available:
                findings.extend(ReadinessFinding("JUNCTION_PLACEMENT_UNAVAILABLE", node.node_id, True) for node in virtual_nodes if node.node_id not in by_node or by_node[node.node_id].status is not JunctionPlacementStatus.PROPOSALS_GENERATED)

    shared_trunk_unresolved = bool(virtual_nodes)
    if shared_trunk_unresolved:
        findings.append(ReadinessFinding("SHARED_TRUNK_GEOMETRY_UNRESOLVED", None, False))
    blocking = any(item.blocking for item in findings)
    return RoutingReadinessAssessment(
        candidate.candidate_id, evidence.coordinate_reference.frame_id, policy.policy_id, policy.digest_sha256,
        tuple(item.connection_id for item in candidate.demand.problem.connections), endpoint_coordinates, frame_valid,
        grids_available, junctions_available, shared_trunk_unresolved, not blocking, findings=tuple(sorted(findings, key=lambda item: (item.code, item.subject_id or "", item.blocking))),
    )
