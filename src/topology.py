"""Immutable topology graph models for production MEP routing demand.

Phase 9B foundation: declare mains, branches, headers, risers, and explicit
same-system shared-segment ownership.  This module does not plan topology,
route geometry, or detect physical clashes between independently generated
paths.  Declaring shared ownership authorizes future route sharing; it does
not certify that two geometric realizations occupy the same cells.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping

from .endpoints import ConnectorDimensions, ConnectorShape, System


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


class TopologyNodeKind(str, Enum):
    """Logical roles for topology vertices in the demand graph."""

    SOURCE = "SOURCE"
    SINK = "SINK"
    JUNCTION = "JUNCTION"
    HEADER = "HEADER"
    RISER = "RISER"


class TopologyEdgeRole(str, Enum):
    """Structural roles for topology edges."""

    MAIN = "MAIN"
    BRANCH = "BRANCH"
    HEADER = "HEADER"
    RISER = "RISER"


@dataclass(frozen=True, slots=True)
class EdgeProfile:
    """Optional capacity/profile metadata attached to a topology edge."""

    connector_shape: ConnectorShape | None = None
    dimensions: ConnectorDimensions | None = None
    nominal_capacity: float | None = None

    def __post_init__(self) -> None:
        if self.connector_shape is not None and not isinstance(self.connector_shape, ConnectorShape):
            raise ValueError("Edge profile connector_shape must be a ConnectorShape or None.")
        if self.dimensions is not None and not isinstance(self.dimensions, ConnectorDimensions):
            raise ValueError("Edge profile dimensions must be ConnectorDimensions or None.")
        if self.nominal_capacity is not None and (
            isinstance(self.nominal_capacity, bool)
            or not isinstance(self.nominal_capacity, (int, float))
            or not isfinite(self.nominal_capacity)
            or self.nominal_capacity <= 0
        ):
            raise ValueError("Edge profile nominal_capacity must be a finite positive value when supplied.")
        if self.connector_shape is ConnectorShape.CIRCULAR:
            if self.dimensions is None or self.dimensions.diameter is None:
                raise ValueError("Circular edge profiles require diameter dimensions.")
            if self.dimensions.width is not None or self.dimensions.height is not None:
                raise ValueError("Circular edge profiles require diameter only.")
        elif self.connector_shape is ConnectorShape.RECTANGULAR:
            if self.dimensions is None or self.dimensions.width is None or self.dimensions.height is None:
                raise ValueError("Rectangular edge profiles require width and height dimensions.")
            if self.dimensions.diameter is not None:
                raise ValueError("Rectangular edge profiles require width and height only.")
        elif self.connector_shape is ConnectorShape.UNKNOWN and self.dimensions is not None:
            if any(value is not None for value in (self.dimensions.diameter, self.dimensions.width, self.dimensions.height)):
                raise ValueError("Unknown edge profiles cannot declare shape-specific dimensions.")
        elif self.connector_shape is None and self.dimensions is not None:
            raise ValueError("Edge profile dimensions require a connector_shape.")


@dataclass(frozen=True, slots=True)
class TopologyNode:
    """A system-owned vertex that may optionally reference a Phase 9A endpoint."""

    node_id: str
    system: System
    kind: TopologyNodeKind
    endpoint_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ValueError("Topology node ID must be a non-empty string.")
        if not isinstance(self.system, System):
            raise ValueError("Topology node system must be a supported System value.")
        if not isinstance(self.kind, TopologyNodeKind):
            raise ValueError("Topology node kind must be a TopologyNodeKind value.")
        if self.endpoint_id is not None and (not isinstance(self.endpoint_id, str) or not self.endpoint_id.strip()):
            raise ValueError("Topology node endpoint_id must be a non-empty string when supplied.")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("Topology node metadata must be a mapping.")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    """A directed topology segment with explicit role and optional share ownership.

    ``owner_edge_id`` identifies the authoritative edge that owns a shared
    physical segment.  A share edge must point at a primary owner (an edge with
    no owner of its own).  Two primary edges that declare the same undirected
    node pair without ownership are treated as unauthorized duplicate segment
    declarations and rejected by ``TopologyGraph``.
    """

    edge_id: str
    system: System
    role: TopologyEdgeRole
    start_node_id: str
    end_node_id: str
    owner_edge_id: str | None = None
    profile: EdgeProfile | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.edge_id, str) or not self.edge_id.strip():
            raise ValueError("Topology edge ID must be a non-empty string.")
        if not isinstance(self.system, System):
            raise ValueError("Topology edge system must be a supported System value.")
        if not isinstance(self.role, TopologyEdgeRole):
            raise ValueError("Topology edge role must be a TopologyEdgeRole value.")
        if any(not isinstance(value, str) or not value.strip() for value in (self.start_node_id, self.end_node_id)):
            raise ValueError("Topology edge node IDs must be non-empty strings.")
        if self.start_node_id == self.end_node_id:
            raise ValueError("A topology edge cannot reference the same node twice.")
        if self.owner_edge_id is not None:
            if not isinstance(self.owner_edge_id, str) or not self.owner_edge_id.strip():
                raise ValueError("Topology edge owner_edge_id must be a non-empty string when supplied.")
            if self.owner_edge_id == self.edge_id:
                raise ValueError("A topology edge cannot own itself.")
        if self.profile is not None and not isinstance(self.profile, EdgeProfile):
            raise ValueError("Topology edge profile must be an EdgeProfile or None.")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("Topology edge metadata must be a mapping.")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    @property
    def is_primary(self) -> bool:
        """Return True when this edge owns its segment rather than sharing another."""
        return self.owner_edge_id is None

    @property
    def undirected_node_pair(self) -> tuple[str, str]:
        """Stable undirected endpoint key used for duplicate-segment detection."""
        return tuple(sorted((self.start_node_id, self.end_node_id)))  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class TopologyGraph:
    """Authoritative immutable topology demand for later routing phases.

    Compatibility note: future ``RoutingProblem`` integration, constrained A*,
    conflict coordination, IFC connector mapping, and continuous geometry may
    consume this graph.  Those systems are intentionally not implemented here.
    """

    nodes: tuple[TopologyNode, ...]
    edges: tuple[TopologyEdge, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple) or not all(isinstance(node, TopologyNode) for node in self.nodes):
            raise ValueError("TopologyGraph nodes must be an immutable tuple of TopologyNode values.")
        if not isinstance(self.edges, tuple) or not all(isinstance(edge, TopologyEdge) for edge in self.edges):
            raise ValueError("TopologyGraph edges must be an immutable tuple of TopologyEdge values.")
        node_ids = tuple(node.node_id for node in self.nodes)
        edge_ids = tuple(edge.edge_id for edge in self.edges)
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("TopologyGraph node IDs must be unique.")
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("TopologyGraph edge IDs must be unique.")
        if node_ids != tuple(sorted(node_ids)) or edge_ids != tuple(sorted(edge_ids)):
            raise ValueError("TopologyGraph nodes and edges must have deterministic ID ordering.")
        node_by_id = {node.node_id: node for node in self.nodes}
        edge_by_id = {edge.edge_id: edge for edge in self.edges}
        for edge in self.edges:
            for node_id in (edge.start_node_id, edge.end_node_id):
                if node_id not in node_by_id:
                    raise ValueError(f"Topology edge {edge.edge_id} references a missing node: {node_id}")
                if node_by_id[node_id].system is not edge.system:
                    raise ValueError(f"Topology edge {edge.edge_id} must use nodes from its system.")
            if edge.owner_edge_id is not None:
                if edge.owner_edge_id not in edge_by_id:
                    raise ValueError(
                        f"Topology edge {edge.edge_id} references a missing owner edge: {edge.owner_edge_id}"
                    )
                owner = edge_by_id[edge.owner_edge_id]
                if owner.system is not edge.system:
                    raise ValueError(
                        f"Topology edge {edge.edge_id} cannot share across systems with owner {owner.edge_id}."
                    )
                if not owner.is_primary:
                    raise ValueError(
                        f"Topology edge {edge.edge_id} owner {owner.edge_id} must itself be a primary owner."
                    )
                if edge.undirected_node_pair != owner.undirected_node_pair:
                    raise ValueError(
                        f"Topology edge {edge.edge_id} must share the same undirected node pair as owner "
                        f"{owner.edge_id}."
                    )
        primary_by_pair: dict[tuple[System, tuple[str, str]], str] = {}
        for edge in self.edges:
            if not edge.is_primary:
                continue
            key = (edge.system, edge.undirected_node_pair)
            if key in primary_by_pair:
                raise ValueError(
                    "Unauthorized duplicate segment declaration for undirected pair "
                    f"{edge.undirected_node_pair} on system {edge.system.value}: "
                    f"{primary_by_pair[key]} and {edge.edge_id}."
                )
            primary_by_pair[key] = edge.edge_id
        if not isinstance(self.metadata, Mapping):
            raise ValueError("TopologyGraph metadata must be a mapping.")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return a deterministic nested mapping suitable for JSON serialization."""
        return {
            "edges": [_edge_to_dict(edge) for edge in self.edges],
            "metadata": _canonical_metadata(self.metadata),
            "nodes": [_node_to_dict(node) for node in self.nodes],
        }


def build_topology_graph(
    nodes: list[TopologyNode] | tuple[TopologyNode, ...],
    edges: list[TopologyEdge] | tuple[TopologyEdge, ...],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> TopologyGraph:
    """Sort nodes and edges by ID, then construct a validated TopologyGraph."""
    ordered_nodes = tuple(sorted(nodes, key=lambda node: node.node_id))
    ordered_edges = tuple(sorted(edges, key=lambda edge: edge.edge_id))
    return TopologyGraph(ordered_nodes, ordered_edges, dict(metadata or {}))


def _node_to_dict(node: TopologyNode) -> dict[str, Any]:
    return {
        "endpoint_id": node.endpoint_id,
        "kind": node.kind.value,
        "metadata": _canonical_metadata(node.metadata),
        "node_id": node.node_id,
        "system": node.system.value,
    }


def _profile_to_dict(profile: EdgeProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    dimensions = None
    if profile.dimensions is not None:
        dimensions = {
            "diameter": profile.dimensions.diameter,
            "height": profile.dimensions.height,
            "width": profile.dimensions.width,
        }
    return {
        "connector_shape": None if profile.connector_shape is None else profile.connector_shape.value,
        "dimensions": dimensions,
        "nominal_capacity": profile.nominal_capacity,
    }


def _edge_to_dict(edge: TopologyEdge) -> dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "end_node_id": edge.end_node_id,
        "metadata": _canonical_metadata(edge.metadata),
        "owner_edge_id": edge.owner_edge_id,
        "profile": _profile_to_dict(edge.profile),
        "role": edge.role.value,
        "start_node_id": edge.start_node_id,
        "system": edge.system.value,
    }
