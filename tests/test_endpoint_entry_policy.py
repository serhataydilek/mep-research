"""Focused Phase 9D.5 controlled endpoint-entry policy tests."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace

from src.endpoint_entry import (
    EntryClassification,
    ObstructionKind,
    diagnose_endpoint_entry,
    diagnose_routing_problem_endpoints,
)
from src.endpoint_entry_policy import (
    ControlledEntryPolicy,
    TransitionAuthorization,
    authorize_controlled_entries,
    benchmark_controlled_entry_v1,
    evaluate_endpoint_transition,
)
from src.endpoints import Vector3
from src.multi_system_diagnostics import diagnose_multi_system_conflicts
from src.multi_system_routing import ConnectionRouteStatus, EndpointCellMode, route_multi_system
from src.spatial_evidence import AabbFidelity
from src.visualization_export import controlled_s01_inputs


class ControlledEntryPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p, cls.e, cls.pol, cls.f = controlled_s01_inputs()
        cls.report = diagnose_routing_problem_endpoints(cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f)
        cls.decision = authorize_controlled_entries(cls.report, cls.e, cls.pol, benchmark_controlled_entry_v1())
        cls.by_id = {item.endpoint_id: item for item in cls.decision.transitions}
        cls.endpoints = {item.endpoint_id: item for item in cls.p.endpoints}
        cls.strict = route_multi_system(cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f)
        cls.bench = route_multi_system(
            cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f, endpoint_cell_mode=EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY
        )
        cls.alias = route_multi_system(
            cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f, endpoint_cell_mode=EndpointCellMode.EXPLORATORY_ENTRY_CANDIDATE
        )
        cls.snap = route_multi_system(
            cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f, endpoint_cell_mode=EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE
        )

    def test_blocked_s01_egresses_authorize_clearance_inflation_only(self):
        for endpoint_id in ("egress/hvac", "egress/fire", "egress/water"):
            diagnosis = self.report.diagnosis_for(endpoint_id)
            transition = self.by_id[endpoint_id]
            self.assertIs(diagnosis.obstruction_kind, ObstructionKind.CLEARANCE_INFLATION)
            self.assertIs(diagnosis.classification, EntryClassification.CANDIDATE_ENTRY_CELL_FOUND)
            self.assertIs(transition.authorization, TransitionAuthorization.AUTHORIZED_BENCHMARK_GRID_ENTRY)
            self.assertEqual(transition.proposed_entry_cell, diagnosis.entry_candidate.cell)
            self.assertEqual(transition.authoritative_world_m, diagnosis.authoritative_world_m)
            self.assertEqual(transition.displacement_m, diagnosis.entry_candidate.displacement_m)
            self.assertEqual(transition.raw_solid_proxy_hits, ())
            self.assertTrue(transition.clearance_inflation_basis)
            self.assertTrue(transition.aabb_uncertainty)
            self.assertFalse(transition.exact_geometry_evaluated)
            self.assertFalse(transition.physical_connector_engagement_certified)
            self.assertTrue(transition.authoritative_coordinate_preserved)
        self.assertEqual(
            [endpoint_id for endpoint_id, _ in self.decision.authorized_entry_cells],
            ["egress/fire", "egress/hvac", "egress/water"],
        )

    def test_raw_obstruction_rejected_inflated_authorized(self):
        endpoint = self.endpoints["egress/fire"]
        wall = next(item for item in self.e.elements if item.global_id.endswith("Jvo_Uq"))
        inside = replace(
            endpoint,
            position=Vector3(
                (wall.world_bounds.min_x + wall.world_bounds.max_x) / 2,
                (wall.world_bounds.min_y + wall.world_bounds.max_y) / 2,
                endpoint.position.z,
            ),
        )
        diagnosis = diagnose_endpoint_entry(inside, self.e, self.pol, endpoint_frame_id=self.f)
        transition = evaluate_endpoint_transition(diagnosis, self.e, self.pol, benchmark_controlled_entry_v1())
        self.assertIs(diagnosis.obstruction_kind, ObstructionKind.RAW_AABB_INTERSECTION)
        self.assertIs(transition.authorization, TransitionAuthorization.REJECTED_RAW_SOLID_PROXY)
        self.assertTrue(transition.raw_solid_proxy_hits)
        self.assertFalse(transition.physical_connector_engagement_certified)

        authorized = self.by_id["egress/fire"]
        self.assertIs(authorized.authorization, TransitionAuthorization.AUTHORIZED_BENCHMARK_GRID_ENTRY)
        self.assertEqual(authorized.raw_solid_proxy_hits, ())

    def test_unsupported_geometry_and_policy_guards(self):
        with self.assertRaisesRegex(ValueError, "connector engagement"):
            ControlledEntryPolicy(physical_connector_engagement_certified=True)
        with self.assertRaisesRegex(ValueError, "BENCHMARK_CONTROLLED_ENTRY_V1"):
            ControlledEntryPolicy(policy_id="OTHER")
        mismatch = diagnose_endpoint_entry(self.endpoints["egress/hvac"], self.e, self.pol, endpoint_frame_id="wrong-frame")
        unresolved = evaluate_endpoint_transition(mismatch, self.e, self.pol, benchmark_controlled_entry_v1())
        self.assertIs(unresolved.authorization, TransitionAuthorization.NO_CANDIDATE)
        usable = evaluate_endpoint_transition(
            self.report.diagnosis_for("egress/electrical"), self.e, self.pol, benchmark_controlled_entry_v1()
        )
        self.assertIs(usable.authorization, TransitionAuthorization.NOT_REQUIRED_ENDPOINT_USABLE)

    def test_modes_report_separately_without_false_certification(self):
        demand = [item.connection_id for item in self.p.connections]
        self.assertEqual(len(demand), 13)
        cases = (
            (self.strict, 5, EndpointCellMode.CONTAINING_CELL_ONLY, True),
            (self.bench, 13, EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY, False),
            (self.alias, 13, EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY, False),
            (self.snap, 13, EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE, False),
        )
        for result, routed, mode, strict in cases:
            self.assertEqual(list(result.required_connection_ids), demand)
            self.assertEqual([item.connection_id for item in result.connections], demand)
            self.assertEqual(result.count(ConnectionRouteStatus.ROUTED), routed)
            self.assertIs(result.endpoint_cell_mode, mode)
            self.assertEqual(result.strict_mode, strict)
            self.assertEqual(result.grid_paths_complete, routed == 13)
            self.assertFalse(result.connector_engagement_validated)
            self.assertFalse(result.collision_free_claimed)
            payload = result.to_canonical_dict()
            self.assertFalse(payload["connector_engagement_validated"])
            self.assertEqual(payload["summary"]["strict_mode"], strict)
            self.assertEqual(payload["summary"]["benchmark_controlled_entry_mode"], mode is EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY)
            self.assertEqual(payload["summary"]["legacy_evidence_snap_mode"], mode is EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE)
        self.assertEqual(self.alias.to_canonical_json(), self.bench.to_canonical_json())
        self.assertIsNotNone(self.bench.controlled_entry)
        self.assertIsNone(self.strict.controlled_entry)
        for record in self.bench.connections:
            if record.system in ("hvac", "fire", "water"):
                self.assertIn("BENCHMARK_CONTROLLED_ENTRY_AUTHORIZED", record.unvalidated_requirements)
                self.assertIn("PHYSICAL_CONNECTOR_ENGAGEMENT_UNVALIDATED", record.unvalidated_requirements)
                self.assertIn("NOT_STRICT_AUTHORITATIVE_ENDPOINT_ROUTING", record.unvalidated_requirements)
                self.assertNotIn("EXPLORATORY_ENTRY_CELL_USED", record.unvalidated_requirements)
        with self.assertRaisesRegex(ValueError, "connector engagement validation"):
            replace(self.bench, connector_engagement_validated=True)
        with self.assertRaisesRegex(ValueError, "strict_mode is reserved"):
            replace(self.bench, strict_mode=True)

    def test_no_dropped_demand_and_zero_length_main_prevention(self):
        for result in (self.strict, self.bench, self.snap):
            self.assertEqual(len(result.connections), 13)
            self.assertEqual(set(result.required_connection_ids), {item.connection_id for item in self.p.connections})
            self.assertTrue(all(len(edge.route.grid_cells) >= 2 for edge in result.edges if edge.role == "MAIN" and edge.route.status.value == "ROUTED"))
            degenerate = [item for item in result.attempts if item.reason == "DEGENERATE_ZERO_LENGTH_MAIN"]
            self.assertTrue(all(not item.selected for item in degenerate))
        water = [item for item in self.bench.attempts if item.system == "water" and item.selected]
        self.assertEqual(len(water), 1)
        self.assertEqual(water[0].strategy.value, "SHARED_MAIN")
        self.assertIsNone(water[0].reason)

    def test_determinism_and_bounded_termination(self):
        again = authorize_controlled_entries(self.report, self.e, self.pol, benchmark_controlled_entry_v1())
        self.assertEqual(again.to_canonical_dict(), self.decision.to_canonical_dict())
        self.assertEqual(
            json.dumps(again.to_canonical_dict(), sort_keys=True),
            json.dumps(self.decision.to_canonical_dict(), sort_keys=True),
        )
        route_again = route_multi_system(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY
        )
        self.assertEqual(route_again.to_canonical_json(), self.bench.to_canonical_json())
        limited = diagnose_routing_problem_endpoints(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, max_radius_cells=0, max_candidates=1
        )
        limited_decision = authorize_controlled_entries(limited, self.e, self.pol)
        for endpoint_id in ("egress/hvac", "egress/fire", "egress/water"):
            self.assertIs(limited_decision.entry_cells().get(endpoint_id), None)
            self.assertIs(self.by_id[endpoint_id].authorization, TransitionAuthorization.AUTHORIZED_BENCHMARK_GRID_ENTRY)

    def test_identity_preservation_across_modes(self):
        demand = [item.connection_id for item in self.p.connections]
        for result in (self.strict, self.bench, self.snap):
            self.assertEqual(list(result.required_connection_ids), demand)
            for endpoint in self.p.endpoints:
                if endpoint.endpoint_id.startswith("egress/"):
                    self.assertEqual(
                        (endpoint.position.x, endpoint.position.y, endpoint.position.z),
                        self.report.diagnosis_for(endpoint.endpoint_id).authoritative_world_m,
                    )
        for endpoint_id, cell in self.decision.entry_cells().items():
            diagnosis = self.report.diagnosis_for(endpoint_id)
            self.assertNotEqual(diagnosis.containing_cell, cell)
            self.assertEqual(diagnosis.authoritative_world_m, self.by_id[endpoint_id].authoritative_world_m)

    def test_conflict_counts_remain_honest(self):
        diagnostics = diagnose_multi_system_conflicts(self.bench, self.e, self.pol)
        self.assertEqual(self.bench.count(ConnectionRouteStatus.ROUTED), 13)
        self.assertEqual(diagnostics["summary"]["inter_system_conflicting_pair_count"], 28)
        self.assertFalse(self.bench.connector_engagement_validated)
        self.assertTrue(self.bench.grid_paths_complete)
        self.assertFalse(self.bench.strict_mode)


if __name__ == "__main__":
    unittest.main()
