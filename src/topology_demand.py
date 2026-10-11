"""Associate Phase 9A routing demand identities with Phase 9B topology graphs.

This integration layer keeps ``RoutingProblem`` / ``ConnectionRequest`` as the
authoritative required-connection contract.  Topology assignments reference
those identities; they never invent route geometry or certify physical
overlap.  Shared-edge ownership already validated by ``TopologyGraph`` is
preserved when assignments traverse share edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .endpoints import (
    ConnectionRequest,
    Endpoint,
    EndpointProvenance,
    FlowDirection,
    RoutingProblem,
    System,
    benchmark_case_to_routing_problem,
)
from .topology import (
    TopologyEdge,
    TopologyEdgeRole,
    TopologyGraph,
    TopologyNode,
    TopologyNodeKind,
    build_topology_graph,
)


def _freeze_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_metadata_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_metadata_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_metadata_value(item) for item in value)
    return value


def _freeze_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_metadata_value(value) for key, value in metadata.items()})


def _canonical_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical_metadata_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_canonical_metadata_value(item) for item in value]
    return value


def _canonical_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _canonical_metadata_value(value) for key, value in metadata.items()}


@dataclass(frozen=True, slots=True)
class ConnectionTopologyAssignment:
    """Maps one required connection identity to an ordered topology edge walk."""

    connection_id: str
    edge_ids: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, str) or not self.connection_id.strip():
            raise ValueError("Connection topology assignment ID must be a non-empty string.")
        if not isinstance(self.edge_ids, tuple) or not self.edge_ids:
            raise ValueError("Connection topology assignment edge_ids must be a non-empty tuple.")
        if any(not isinstance(edge_id, str) or not edge_id.strip() for edge_id in self.edge_ids):
            raise ValueError("Connection topology assignment edge IDs must be non-empty strings.")
        if len(set(self.edge_ids)) != len(self.edge_ids):
            raise ValueError(
                f"Connection topology assignment {self.connection_id} edge_ids must not contain duplicates."
            )
        if not isinstance(self.metadata, Mapping):
            raise ValueError("Connection topology assignment metadata must be a mapping.")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class TopologyDemandCoverage:
    """Read-only ID coverage report for proposed topology assignments.

    This report intentionally validates only the association IDs.  It can be
    used before strict topology association to explain incomplete proposals;
    it neither repairs assignments nor establishes endpoint connectivity.
    """

    required_connection_ids: tuple[str, ...]
    assigned_connection_ids: tuple[str, ...]
    missing_required_connection_ids: tuple[str, ...]
    unknown_connection_ids: tuple[str, ...]
    duplicate_assignment_ids: tuple[str, ...]

    @property
    def coverage_status(self) -> str:
        """Return COMPLETE only for exact, one-to-one authoritative coverage."""
        if (
            not self.missing_required_connection_ids
            and not self.unknown_connection_ids
            and not self.duplicate_assignment_ids
        ):
            return "COMPLETE"
        return "INCOMPLETE"


def inspect_topology_demand_coverage(
    problem: RoutingProblem,
    assignments: list[ConnectionTopologyAssignment] | tuple[ConnectionTopologyAssignment, ...],
) -> TopologyDemandCoverage:
    """Report authoritative connection-ID coverage without strict association validation.

    ``RoutingProblem.connections`` is the sole source of required IDs.  In
    particular, endpoint metadata such as benchmark source-to-egress breakouts
    is not interpreted as a routing obligation.
    """
    if not isinstance(problem, RoutingProblem):
        raise ValueError("Topology demand coverage requires a RoutingProblem.")
    if not isinstance(assignments, (list, tuple)) or not all(
        isinstance(assignment, ConnectionTopologyAssignment) for assignment in assignments
    ):
        raise ValueError("Topology demand coverage assignments must be ConnectionTopologyAssignment values.")
    required_ids = tuple(connection.connection_id for connection in problem.connections)
    assigned_ids = tuple(assignment.connection_id for assignment in assignments)
    assigned_set = set(assigned_ids)
    required_set = set(required_ids)
    duplicate_ids = tuple(sorted({item for item in assigned_ids if assigned_ids.count(item) > 1}))
    return TopologyDemandCoverage(
        required_connection_ids=required_ids,
        assigned_connection_ids=assigned_ids,
        missing_required_connection_ids=tuple(sorted(required_set - assigned_set)),
        unknown_connection_ids=tuple(sorted(assigned_set - required_set)),
        duplicate_assignment_ids=duplicate_ids,
    )


@dataclass(frozen=True, slots=True)
class TopologicalRoutingDemand:
    """Immutable pairing of authoritative connections with an explicit topology.

    Required connections remain those in ``problem.connections`` and are
    independent of any generated route result.  Every connection must have
    exactly one assignment; unknown or duplicate assignment IDs are rejected.
    """

    problem: RoutingProblem
    topology: TopologyGraph
    assignments: tuple[ConnectionTopologyAssignment, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.problem, RoutingProblem):
            raise ValueError("TopologicalRoutingDemand problem must be a RoutingProblem.")
        if not isinstance(self.topology, TopologyGraph):
            raise ValueError("TopologicalRoutingDemand topology must be a TopologyGraph.")
        if not isinstance(self.assignments, tuple) or not all(
            isinstance(assignment, ConnectionTopologyAssignment) for assignment in self.assignments
        ):
            raise ValueError(
                "TopologicalRoutingDemand assignments must be an immutable tuple of "
                "ConnectionTopologyAssignment values."
            )
        assignment_ids = tuple(assignment.connection_id for assignment in self.assignments)
        if len(set(assignment_ids)) != len(assignment_ids):
            raise ValueError("TopologicalRoutingDemand assignment connection IDs must be unique.")
        if assignment_ids != tuple(sorted(assignment_ids)):
            raise ValueError("TopologicalRoutingDemand assignments must have deterministic ID ordering.")
        coverage = inspect_topology_demand_coverage(self.problem, self.assignments)
        if coverage.missing_required_connection_ids:
            raise ValueError(
                "TopologicalRoutingDemand is missing assignments for connections: "
                f"{list(coverage.missing_required_connection_ids)}"
            )
        if coverage.unknown_connection_ids:
            raise ValueError(
                "TopologicalRoutingDemand has unknown connection assignments: "
                f"{list(coverage.unknown_connection_ids)}"
            )
        if coverage.duplicate_assignment_ids:
            raise ValueError("TopologicalRoutingDemand must assign exactly the required connections.")

        endpoint_nodes = _endpoint_node_index(self.topology)
        edge_by_id = {edge.edge_id: edge for edge in self.topology.edges}
        connection_by_id = {connection.connection_id: connection for connection in self.problem.connections}
        for assignment in self.assignments:
            connection = connection_by_id[assignment.connection_id]
            _validate_assignment(connection, assignment, edge_by_id, endpoint_nodes)
        if not isinstance(self.metadata, Mapping):
            raise ValueError("TopologicalRoutingDemand metadata must be a mapping.")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return a deterministic nested mapping suitable for JSON serialization."""
        return {
            "assignments": [
                {
                    "connection_id": assignment.connection_id,
                    "edge_ids": list(assignment.edge_ids),
                    "metadata": _canonical_metadata(assignment.metadata),
                }
                for assignment in self.assignments
            ],
            "metadata": _canonical_metadata(self.metadata),
            "problem": {
                "connection_ids": [connection.connection_id for connection in self.problem.connections],
                "endpoint_ids": [endpoint.endpoint_id for endpoint in self.problem.endpoints],
                "metadata": _canonical_metadata(self.problem.metadata),
            },
            "topology": self.topology.to_canonical_dict(),
        }


def associate_routing_problem_with_topology(
    problem: RoutingProblem,
    topology: TopologyGraph,
    assignments: list[ConnectionTopologyAssignment] | tuple[ConnectionTopologyAssignment, ...],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> TopologicalRoutingDemand:
    """Build a validated demand/topology association with deterministic assignment order."""
    ordered = tuple(sorted(assignments, key=lambda assignment: assignment.connection_id))
    return TopologicalRoutingDemand(problem, topology, ordered, dict(metadata or {}))


def star_topology_for_routing_problem(problem: RoutingProblem) -> tuple[TopologyGraph, tuple[ConnectionTopologyAssignment, ...]]:
    """Derive a one-edge-per-connection star topology from existing connection identities.

    Each required connection becomes a dedicated BRANCH edge between its start and
    end endpoint nodes.  No shared trunks are invented, and source-to-egress
    breakout metadata is not added as a routed obligation.
    """
    endpoint_by_id = {endpoint.endpoint_id: endpoint for endpoint in problem.endpoints}
    used_endpoint_ids = {
        endpoint_id
        for connection in problem.connections
        for endpoint_id in (connection.start_endpoint_id, connection.end_endpoint_id)
    }
    nodes = [
        TopologyNode(
            node_id=_node_id_for_endpoint(endpoint_id),
            system=endpoint_by_id[endpoint_id].system,
            kind=_node_kind_for_endpoint(endpoint_by_id[endpoint_id]),
            endpoint_id=endpoint_id,
            metadata={"adapter": "star", "benchmark_anchor_kind": endpoint_by_id[endpoint_id].metadata.get("benchmark_anchor_kind")},
        )
        for endpoint_id in used_endpoint_ids
    ]
    edges = [
        TopologyEdge(
            edge_id=_edge_id_for_connection(connection.connection_id),
            system=connection.system,
            role=TopologyEdgeRole.BRANCH,
            start_node_id=_node_id_for_endpoint(connection.start_endpoint_id),
            end_node_id=_node_id_for_endpoint(connection.end_endpoint_id),
            metadata={"adapter": "star", "connection_id": connection.connection_id},
        )
        for connection in problem.connections
    ]
    topology = build_topology_graph(nodes, edges, metadata={"adapter": "star", "shared_trunks": False})
    assignments = tuple(
        ConnectionTopologyAssignment(
            connection_id=connection.connection_id,
            edge_ids=(_edge_id_for_connection(connection.connection_id),),
            metadata={"adapter": "star"},
        )
        for connection in sorted(problem.connections, key=lambda item: item.connection_id)
    )
    return topology, assignments


def benchmark_case_to_topological_demand(
    case: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
) -> TopologicalRoutingDemand:
    """Adapt one legacy benchmark case into demand + star topology associations.

    Preserves ``benchmark_case_to_routing_problem`` connection and endpoint
    identities.  Does not turn fixed breakouts into extra required connections.
    """
    problem = benchmark_case_to_routing_problem(case, systems)
    topology, assignments = star_topology_for_routing_problem(problem)
    return associate_routing_problem_with_topology(
        problem,
        topology,
        assignments,
        metadata={
            "adapter": "benchmark_star",
            "legacy_case_id": case.get("case_id"),
            "required_connection_count": len(problem.connections),
        },
    )


def _node_id_for_endpoint(endpoint_id: str) -> str:
    return f"node/{endpoint_id}"


def _edge_id_for_connection(connection_id: str) -> str:
    return f"edge/{connection_id}"


def _node_kind_for_endpoint(endpoint: Endpoint) -> TopologyNodeKind:
    if endpoint.flow_direction is FlowDirection.SOURCE:
        return TopologyNodeKind.SOURCE
    if endpoint.flow_direction is FlowDirection.SINK:
        return TopologyNodeKind.SINK
    return TopologyNodeKind.JUNCTION


def _endpoint_node_index(topology: TopologyGraph) -> dict[str, TopologyNode]:
    index: dict[str, TopologyNode] = {}
    for node in topology.nodes:
        if node.endpoint_id is None:
            continue
        if node.endpoint_id in index:
            raise ValueError(
                f"Topology binds endpoint {node.endpoint_id} to multiple nodes: "
                f"{index[node.endpoint_id].node_id} and {node.node_id}."
            )
        index[node.endpoint_id] = node
    return index


def _validate_assignment(
    connection: ConnectionRequest,
    assignment: ConnectionTopologyAssignment,
    edge_by_id: dict[str, TopologyEdge],
    endpoint_nodes: dict[str, TopologyNode],
) -> None:
    if connection.start_endpoint_id not in endpoint_nodes:
        raise ValueError(
            f"Assignment {assignment.connection_id} start endpoint "
            f"{connection.start_endpoint_id} has no topology node binding."
        )
    if connection.end_endpoint_id not in endpoint_nodes:
        raise ValueError(
            f"Assignment {assignment.connection_id} end endpoint "
            f"{connection.end_endpoint_id} has no topology node binding."
        )
    start_node = endpoint_nodes[connection.start_endpoint_id]
    end_node = endpoint_nodes[connection.end_endpoint_id]
    if start_node.system is not connection.system or end_node.system is not connection.system:
        raise ValueError(
            f"Assignment {assignment.connection_id} endpoint nodes must match connection system "
            f"{connection.system.value}."
        )
    for edge_id in assignment.edge_ids:
        if edge_id not in edge_by_id:
            raise ValueError(f"Assignment {assignment.connection_id} references a missing edge: {edge_id}")
        edge = edge_by_id[edge_id]
        if edge.system is not connection.system:
            raise ValueError(
                f"Assignment {assignment.connection_id} edge {edge_id} system "
                f"{edge.system.value} does not match connection system {connection.system.value}."
            )
    if not _edge_sequence_connects(assignment.edge_ids, edge_by_id, start_node.node_id, end_node.node_id):
        raise ValueError(
            f"Assignment {assignment.connection_id} edge sequence is not a contiguous walk "
            f"from {start_node.node_id} to {end_node.node_id}."
        )


def _edge_sequence_connects(
    edge_ids: tuple[str, ...],
    edge_by_id: dict[str, TopologyEdge],
    start_node_id: str,
    end_node_id: str,
) -> bool:
    """Return True when the ordered edges form an undirected contiguous walk."""
    current = start_node_id
    for edge_id in edge_ids:
        edge = edge_by_id[edge_id]
        if edge.start_node_id == current:
            current = edge.end_node_id
        elif edge.end_node_id == current:
            current = edge.start_node_id
        else:
            return False
    return current == end_node_id
