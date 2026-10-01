"""Immutable, provenanced endpoint and connection models for production routing.

This module deliberately does not alter the research-v1.0 benchmark data
contract.  ``benchmark_case_to_routing_problem`` is an adapter from that
contract to the normalized models; future IFC and manual adapters can target
the same models without coupling the benchmark generator to those sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isclose, isfinite, sqrt
from types import MappingProxyType
from typing import Any, Mapping


class System(str, Enum):
    """Current service identities; extend deliberately as product scope grows."""

    HVAC = "hvac"
    DRAINAGE = "drainage"
    WATER = "water"
    FIRE = "fire"
    ELECTRICAL = "electrical"


class EndpointProvenance(str, Enum):
    BENCHMARK_SYNTHETIC = "BENCHMARK_SYNTHETIC"
    IFC_DISTRIBUTION_PORT = "IFC_DISTRIBUTION_PORT"
    EXPORT_CONNECTOR = "EXPORT_CONNECTOR"
    MANUAL = "MANUAL"
    INFERRED = "INFERRED"


class FlowDirection(str, Enum):
    SOURCE = "SOURCE"
    SINK = "SINK"
    BIDIRECTIONAL = "BIDIRECTIONAL"
    UNKNOWN = "UNKNOWN"


class ConnectorShape(str, Enum):
    CIRCULAR = "CIRCULAR"
    RECTANGULAR = "RECTANGULAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Vector3:
    """A finite three-dimensional world-space vector in metres."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
               for value in (self.x, self.y, self.z)):
            raise ValueError("Vector3 coordinates must be finite numeric values.")


@dataclass(frozen=True, slots=True)
class ConnectorDimensions:
    """Dimensions in metres; shape-specific consistency is checked by Endpoint."""

    diameter: float | None = None
    width: float | None = None
    height: float | None = None

    def __post_init__(self) -> None:
        for name, value in (("diameter", self.diameter), ("width", self.width), ("height", self.height)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not isfinite(value) or value <= 0):
                raise ValueError(f"Connector {name} must be a finite positive value when supplied.")


@dataclass(frozen=True, slots=True)
class Endpoint:
    endpoint_id: str
    system: System
    position: Vector3
    orientation: Vector3 | None
    flow_direction: FlowDirection
    connector_shape: ConnectorShape
    connector_dimensions: ConnectorDimensions
    provenance: EndpointProvenance
    source_element_id: str | None = None
    source_port_id: str | None = None
    confidence: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_id, str) or not self.endpoint_id.strip():
            raise ValueError("Endpoint ID must be a non-empty string.")
        if not isinstance(self.system, System):
            raise ValueError("Endpoint system must be a supported System value.")
        if not isinstance(self.position, Vector3):
            raise ValueError("Endpoint position must be a Vector3 in world coordinates.")
        if self.orientation is not None:
            if not isinstance(self.orientation, Vector3):
                raise ValueError("Endpoint orientation must be a Vector3 or None.")
            length = sqrt(self.orientation.x ** 2 + self.orientation.y ** 2 + self.orientation.z ** 2)
            if not isclose(length, 1.0, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("Endpoint orientation must be a normalized non-zero vector.")
        if not isinstance(self.flow_direction, FlowDirection):
            raise ValueError("Endpoint flow direction must be a FlowDirection value.")
        if not isinstance(self.provenance, EndpointProvenance):
            raise ValueError("Endpoint provenance must be an EndpointProvenance value.")
        if not isinstance(self.connector_shape, ConnectorShape):
            raise ValueError("Endpoint connector shape must be a ConnectorShape value.")
        if not isinstance(self.connector_dimensions, ConnectorDimensions):
            raise ValueError("Endpoint connector dimensions must be ConnectorDimensions.")
        dimensions = self.connector_dimensions
        if self.connector_shape is ConnectorShape.CIRCULAR:
            if dimensions.diameter is None or dimensions.width is not None or dimensions.height is not None:
                raise ValueError("Circular connectors require diameter only.")
        elif self.connector_shape is ConnectorShape.RECTANGULAR:
            if dimensions.diameter is not None or dimensions.width is None or dimensions.height is None:
                raise ValueError("Rectangular connectors require width and height only.")
        elif any(value is not None for value in (dimensions.diameter, dimensions.width, dimensions.height)):
            raise ValueError("Unknown connector shapes cannot declare shape-specific dimensions.")
        for name, value in (("source_element_id", self.source_element_id), ("source_port_id", self.source_port_id)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Endpoint {name} must be a non-empty string when supplied.")
        if self.confidence is not None and (isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
                                            or not isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError("Endpoint confidence must be a finite value from 0 through 1 when supplied.")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("Endpoint metadata must be a mapping.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ConnectionRequest:
    """An immutable demanded connection, independent from any route result."""

    connection_id: str
    system: System
    start_endpoint_id: str
    end_endpoint_id: str
    provenance: EndpointProvenance
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, str) or not self.connection_id.strip():
            raise ValueError("Connection ID must be a non-empty string.")
        if not isinstance(self.system, System):
            raise ValueError("Connection system must be a supported System value.")
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.start_endpoint_id, self.end_endpoint_id)):
            raise ValueError("Connection endpoint IDs must be non-empty strings.")
        if self.start_endpoint_id == self.end_endpoint_id:
            raise ValueError("A connection cannot reference the same endpoint twice.")
        if not isinstance(self.provenance, EndpointProvenance):
            raise ValueError("Connection provenance must be an EndpointProvenance value.")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("Connection metadata must be a mapping.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class RoutingProblem:
    """Composable authoritative demand input for later routing and validation phases."""

    endpoints: tuple[Endpoint, ...]
    connections: tuple[ConnectionRequest, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.endpoints, tuple) or not all(isinstance(endpoint, Endpoint) for endpoint in self.endpoints):
            raise ValueError("RoutingProblem endpoints must be an immutable tuple of Endpoint values.")
        if not isinstance(self.connections, tuple) or not all(isinstance(connection, ConnectionRequest) for connection in self.connections):
            raise ValueError("RoutingProblem connections must be an immutable tuple of ConnectionRequest values.")
        endpoint_ids = tuple(endpoint.endpoint_id for endpoint in self.endpoints)
        connection_ids = tuple(connection.connection_id for connection in self.connections)
        if len(set(endpoint_ids)) != len(endpoint_ids):
            raise ValueError("RoutingProblem endpoint IDs must be unique.")
        if len(set(connection_ids)) != len(connection_ids):
            raise ValueError("RoutingProblem connection IDs must be unique.")
        if endpoint_ids != tuple(sorted(endpoint_ids)) or connection_ids != tuple(sorted(connection_ids)):
            raise ValueError("RoutingProblem endpoints and connections must have deterministic ID ordering.")
        endpoint_by_id = {endpoint.endpoint_id: endpoint for endpoint in self.endpoints}
        for connection in self.connections:
            for endpoint_id in (connection.start_endpoint_id, connection.end_endpoint_id):
                if endpoint_id not in endpoint_by_id:
                    raise ValueError(f"Connection {connection.connection_id} references a missing endpoint: {endpoint_id}")
                if endpoint_by_id[endpoint_id].system is not connection.system:
                    raise ValueError(f"Connection {connection.connection_id} must use endpoints from its system.")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("RoutingProblem metadata must be a mapping.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _benchmark_dimensions(definition: Mapping[str, Any]) -> tuple[ConnectorShape, ConnectorDimensions]:
    if definition["envelope_type"] == "circular":
        return ConnectorShape.CIRCULAR, ConnectorDimensions(diameter=float(definition["diameter_m"]))
    if definition["envelope_type"] == "rectangular":
        return ConnectorShape.RECTANGULAR, ConnectorDimensions(
            width=float(definition["width_m"]), height=float(definition["height_m"])
        )
    raise ValueError("Benchmark system must define a circular or rectangular envelope.")


def benchmark_anchor_to_endpoint(anchor: Mapping[str, Any], definition: Mapping[str, Any]) -> Endpoint:
    """Adapt one legacy synthetic anchor without changing its identity or coordinates."""
    endpoint_id = anchor.get("anchor_id")
    if not isinstance(endpoint_id, str):
        raise ValueError("Benchmark anchor must provide a string anchor_id.")
    try:
        system = System(anchor["system"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Benchmark anchor must use a supported system.") from exc
    shape, dimensions = _benchmark_dimensions(definition)
    kind = endpoint_id.split("/", 1)[0]
    if kind == "source":
        flow_direction = FlowDirection.SOURCE
    elif kind == "terminal":
        flow_direction = FlowDirection.SINK
    elif kind == "egress":
        flow_direction = FlowDirection.UNKNOWN
    else:
        raise ValueError("Benchmark anchor ID must identify source, egress, or terminal.")
    metadata = {key: value for key, value in anchor.items() if key not in {"anchor_id", "system", "x_m", "y_m", "z_m"}}
    metadata["benchmark_anchor_kind"] = kind
    return Endpoint(
        endpoint_id=endpoint_id,
        system=system,
        position=Vector3(float(anchor["x_m"]), float(anchor["y_m"]), float(anchor["z_m"])),
        orientation=None,
        flow_direction=flow_direction,
        connector_shape=shape,
        connector_dimensions=dimensions,
        provenance=EndpointProvenance.BENCHMARK_SYNTHETIC,
        confidence=1.0,
        metadata=metadata,
    )


def benchmark_case_to_routing_problem(case: Mapping[str, Any], systems: Mapping[str, Mapping[str, Any]]) -> RoutingProblem:
    """Build normalized immutable demand from one legacy benchmark case.

    This adapter is read-only: legacy anchor and request records remain the
    source of regression data and their serialized representation is unchanged.
    """
    anchors = [*case["source_anchors"], *case["egress_anchors"], *case["terminal_anchors"]]
    endpoints = tuple(sorted(
        (benchmark_anchor_to_endpoint(anchor, systems[anchor["system"]]) for anchor in anchors),
        key=lambda endpoint: endpoint.endpoint_id,
    ))
    connections = tuple(sorted(
        (
            ConnectionRequest(
                connection_id=request["connection_id"],
                system=System(request["system"]),
                start_endpoint_id=request["start_anchor"],
                end_endpoint_id=request["end_anchor"],
                provenance=EndpointProvenance.BENCHMARK_SYNTHETIC,
                metadata={key: value for key, value in request.items()
                          if key not in {"connection_id", "system", "start_anchor", "end_anchor"}},
            )
            for request in case["connection_requests"]
        ),
        key=lambda connection: connection.connection_id,
    ))
    return RoutingProblem(
        endpoints=endpoints,
        connections=connections,
        metadata={"legacy_case_id": case.get("case_id"), "adapter": "benchmark"},
    )
