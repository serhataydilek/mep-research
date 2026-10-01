"""Focused Phase 9A tests for normalized endpoints and benchmark adaptation."""

from __future__ import annotations

from pathlib import Path
import unittest

from src.demand import generate_benchmark_cases, load_service_definitions
from src.endpoints import (
    ConnectorDimensions, ConnectorShape, ConnectionRequest, Endpoint,
    EndpointProvenance, FlowDirection, RoutingProblem, System, Vector3,
    benchmark_anchor_to_endpoint, benchmark_case_to_routing_problem,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"


def circular_endpoint(**overrides: object) -> Endpoint:
    values: dict[str, object] = {
        "endpoint_id": "endpoint/a", "system": System.WATER, "position": Vector3(1.0, 2.0, 3.0),
        "orientation": None, "flow_direction": FlowDirection.SOURCE,
        "connector_shape": ConnectorShape.CIRCULAR,
        "connector_dimensions": ConnectorDimensions(diameter=0.05),
        "provenance": EndpointProvenance.MANUAL,
    }
    values.update(overrides)
    return Endpoint(**values)  # type: ignore[arg-type]


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.systems = load_service_definitions(SYSTEMS)
        cls.case = generate_benchmark_cases(SCENARIOS, DEMANDS, SYSTEMS)[0]

    def test_valid_circular_and_rectangular_endpoints(self) -> None:
        circular = circular_endpoint()
        rectangular = Endpoint(
            endpoint_id="endpoint/b", system=System.HVAC, position=Vector3(0.0, 0.0, 0.0),
            orientation=Vector3(1.0, 0.0, 0.0), flow_direction=FlowDirection.SINK,
            connector_shape=ConnectorShape.RECTANGULAR,
            connector_dimensions=ConnectorDimensions(width=0.4, height=0.25),
            provenance=EndpointProvenance.IFC_DISTRIBUTION_PORT,
        )
        self.assertEqual(circular.connector_dimensions.diameter, 0.05)
        self.assertEqual(rectangular.connector_dimensions.width, 0.4)
        self.assertIsNone(circular.orientation)

    def test_invalid_position_orientation_and_dimensions_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            Vector3(float("nan"), 0.0, 0.0)
        with self.assertRaisesRegex(ValueError, "normalized"):
            circular_endpoint(orientation=Vector3(2.0, 0.0, 0.0))
        with self.assertRaisesRegex(ValueError, "positive"):
            ConnectorDimensions(diameter=0.0)
        with self.assertRaisesRegex(ValueError, "Circular"):
            circular_endpoint(connector_dimensions=ConnectorDimensions(width=0.1, height=0.1))

    def test_provenance_and_metadata_are_preserved_immutably(self) -> None:
        endpoint = circular_endpoint(provenance=EndpointProvenance.INFERRED, metadata={"evidence": "scan"})
        self.assertIs(endpoint.provenance, EndpointProvenance.INFERRED)
        self.assertEqual(endpoint.metadata["evidence"], "scan")
        with self.assertRaises(TypeError):
            endpoint.metadata["evidence"] = "changed"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "provenance"):
            circular_endpoint(provenance="inferred")  # type: ignore[arg-type]

    def test_benchmark_source_egress_and_terminal_conversion(self) -> None:
        source = benchmark_anchor_to_endpoint(self.case["source_anchors"][0], self.systems["hvac"])
        egress = benchmark_anchor_to_endpoint(self.case["egress_anchors"][0], self.systems["hvac"])
        terminal = benchmark_anchor_to_endpoint(next(anchor for anchor in self.case["terminal_anchors"] if anchor["system"] == "hvac"), self.systems["hvac"])
        self.assertEqual((source.endpoint_id, source.position), ("source/hvac", Vector3(self.case["source_anchors"][0]["x_m"], self.case["source_anchors"][0]["y_m"], self.case["source_anchors"][0]["z_m"])))
        self.assertIs(source.flow_direction, FlowDirection.SOURCE)
        self.assertIs(egress.flow_direction, FlowDirection.UNKNOWN)
        self.assertIs(terminal.flow_direction, FlowDirection.SINK)
        self.assertTrue(all(endpoint.provenance is EndpointProvenance.BENCHMARK_SYNTHETIC for endpoint in (source, egress, terminal)))

    def test_benchmark_conversion_is_deterministic_and_preserves_requests(self) -> None:
        first = benchmark_case_to_routing_problem(self.case, self.systems)
        second = benchmark_case_to_routing_problem(self.case, self.systems)
        self.assertEqual(first, second)
        self.assertEqual(len(first.connections), len(self.case["connection_requests"]))
        self.assertEqual({connection.connection_id for connection in first.connections}, {request["connection_id"] for request in self.case["connection_requests"]})
        self.assertEqual([endpoint.endpoint_id for endpoint in first.endpoints], sorted(endpoint.endpoint_id for endpoint in first.endpoints))

    def test_connection_validation_rejects_missing_references_and_mixed_systems(self) -> None:
        endpoint_a = circular_endpoint(endpoint_id="endpoint/a")
        endpoint_b = circular_endpoint(endpoint_id="endpoint/b")
        request = ConnectionRequest("connection/a", System.WATER, "endpoint/a", "missing", EndpointProvenance.MANUAL)
        with self.assertRaisesRegex(ValueError, "missing endpoint"):
            RoutingProblem((endpoint_a, endpoint_b), (request,))
        mismatched = circular_endpoint(endpoint_id="endpoint/c", system=System.FIRE)
        request = ConnectionRequest("connection/a", System.WATER, "endpoint/a", "endpoint/c", EndpointProvenance.MANUAL)
        with self.assertRaisesRegex(ValueError, "system"):
            RoutingProblem((endpoint_a, endpoint_b, mismatched), (request,))
        with self.assertRaisesRegex(ValueError, "same endpoint"):
            ConnectionRequest("connection/self", System.WATER, "endpoint/a", "endpoint/a", EndpointProvenance.MANUAL)

    def test_connection_and_endpoint_ids_must_be_unique_and_ordered(self) -> None:
        endpoint_a = circular_endpoint(endpoint_id="endpoint/a")
        endpoint_b = circular_endpoint(endpoint_id="endpoint/b")
        request = ConnectionRequest("connection/a", System.WATER, "endpoint/a", "endpoint/b", EndpointProvenance.MANUAL)
        with self.assertRaisesRegex(ValueError, "unique"):
            RoutingProblem((endpoint_a, endpoint_a), (request,))
        with self.assertRaisesRegex(ValueError, "deterministic"):
            RoutingProblem((endpoint_b, endpoint_a), (request,))


if __name__ == "__main__":
    unittest.main()
