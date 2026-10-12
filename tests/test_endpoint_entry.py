import json
import unittest
from dataclasses import replace

from src.clearance_policy import benchmark_v1_policy
from src.demand import load_service_definitions
from src.endpoint_entry import (
    EntryCandidate,
    EntryClassification,
    ObstructionKind,
    diagnose_endpoint_entry,
    diagnose_routing_problem_endpoints,
)
from src.endpoints import EndpointProvenance, Vector3
from src.multi_system_routing import EndpointCellMode, route_multi_system
from src.spatial_evidence import AabbFidelity, Bounds3, ElementEvidence, OccupancyRole
from src.visualization_export import controlled_s01_inputs
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EndpointEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p, cls.e, cls.pol, cls.f = controlled_s01_inputs()
        cls.report = diagnose_routing_problem_endpoints(cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f)
        cls.by_id = {item.endpoint_id: item for item in cls.report.diagnoses}
        cls.endpoints = {item.endpoint_id: item for item in cls.p.endpoints}

    def test_strict_blocked_egress_classification(self):
        for endpoint_id, wall_suffix, axis in (
            ("egress/hvac", "7Y119z", 1),
            ("egress/fire", "Jvo_Uq", 1),
            ("egress/water", "Xk4ZsY", 0),
        ):
            diagnosis = self.by_id[endpoint_id]
            self.assertEqual(diagnosis.containing_cell_state, "BLOCKED_FIXED")
            self.assertIs(diagnosis.obstruction_kind, ObstructionKind.CLEARANCE_INFLATION)
            self.assertIs(diagnosis.classification, EntryClassification.CANDIDATE_ENTRY_CELL_FOUND)
            self.assertTrue(diagnosis.aabb_uncertainty)
            self.assertEqual(len(diagnosis.obstacle_contributions), 1)
            hit = diagnosis.obstacle_contributions[0]
            self.assertTrue(hit.global_id.endswith(wall_suffix))
            self.assertEqual(hit.classification, "SHAFT_WALL")
            self.assertFalse(hit.raw_aabb_contains_point)
            self.assertTrue(hit.cell_marked_by_inflated_raster)
            self.assertIs(hit.obstruction_kind, ObstructionKind.CLEARANCE_INFLATION)
            self.assertEqual(hit.aabb_fidelity, AabbFidelity.VOLUME_MATCH_UNCERTAIN.value)
            self.assertGreater(hit.distance_point_to_raw_aabb_m[axis], 0.0)
            self.assertAlmostEqual(sum(hit.distance_point_to_raw_aabb_m), hit.distance_point_to_raw_aabb_m[axis], places=6)

    def test_usable_egresses_remain_free(self):
        for endpoint_id in ("egress/electrical", "egress/drainage"):
            diagnosis = self.by_id[endpoint_id]
            self.assertIs(diagnosis.classification, EntryClassification.ENDPOINT_ALREADY_USABLE)
            self.assertEqual(diagnosis.containing_cell_state, "FREE")
            self.assertIs(diagnosis.obstruction_kind, ObstructionKind.NONE)
            self.assertIsNone(diagnosis.entry_candidate)
            self.assertEqual(diagnosis.obstacle_contributions, ())

    def test_physical_versus_clearance_distinction_and_aabb_uncertainty(self):
        endpoint = self.endpoints["egress/fire"]
        wall = next(item for item in self.e.elements if item.global_id.endswith("Jvo_Uq"))
        # Keep Z inside the plenum routing domain; XY at the shaft-wall AABB centre.
        inside = replace(
            endpoint,
            position=Vector3(
                (wall.world_bounds.min_x + wall.world_bounds.max_x) / 2,
                (wall.world_bounds.min_y + wall.world_bounds.max_y) / 2,
                endpoint.position.z,
            ),
        )
        ambiguous = diagnose_endpoint_entry(inside, self.e, self.pol, endpoint_frame_id=self.f)
        self.assertIs(ambiguous.obstruction_kind, ObstructionKind.RAW_AABB_INTERSECTION)
        self.assertTrue(ambiguous.aabb_uncertainty)
        self.assertIn("RAW_AABB_INTERSECTION_UNCERTAIN_FIDELITY", ambiguous.unresolved_requirements)
        self.assertIn("EXACT_SOLID_INTERSECTION_UNEVALUATED", ambiguous.unresolved_requirements)
        self.assertIn("PRIOR_UNSUPPORTED_OR_AMBIGUOUS_GEOMETRY", ambiguous.unresolved_requirements)

        exact_wall = replace(wall, aabb_fidelity=AabbFidelity.EXACT)
        exact_evidence = replace(
            self.e,
            elements=tuple(exact_wall if item.global_id == wall.global_id else item for item in self.e.elements),
        )
        physical = diagnose_endpoint_entry(inside, exact_evidence, self.pol, endpoint_frame_id=self.f)
        self.assertIs(physical.obstruction_kind, ObstructionKind.RAW_AABB_INTERSECTION)
        self.assertIn("POSSIBLE_PHYSICAL_OBSTRUCTION", physical.unresolved_requirements)
        self.assertIn("PRIOR_POSSIBLE_PHYSICAL_OBSTRUCTION", physical.unresolved_requirements)
        self.assertFalse(any(item.aabb_fidelity != AabbFidelity.EXACT.value for item in physical.obstacle_contributions if item.raw_aabb_contains_point))
        self.assertTrue(physical.obstacle_contributions[0].raw_aabb_contains_point)

    def test_entry_proposal_bounds_displacement_and_no_silent_mutation(self):
        for endpoint_id in ("egress/hvac", "egress/fire", "egress/water"):
            diagnosis = self.by_id[endpoint_id]
            endpoint = self.endpoints[endpoint_id]
            candidate = diagnosis.entry_candidate
            self.assertIsNotNone(candidate)
            self.assertEqual(
                diagnosis.authoritative_world_m,
                (endpoint.position.x, endpoint.position.y, endpoint.position.z),
            )
            self.assertTrue(diagnosis.authoritative_position_preserved)
            expected = tuple(candidate.world_center_m[i] - diagnosis.authoritative_world_m[i] for i in range(3))
            self.assertEqual(candidate.displacement_m, expected)
            self.assertAlmostEqual(
                candidate.distance_m,
                (sum(value * value for value in expected)) ** 0.5,
                places=9,
            )
            self.assertLessEqual(candidate.manhattan_cells, self.report.max_radius_cells)
            self.assertFalse(candidate.connector_transition_validated)
            self.assertFalse(candidate.physical_connector_engagement_claimed)
            self.assertIn("AUTHORITATIVE_ENDPOINT_COORDINATE_UNCHANGED", diagnosis.unresolved_requirements)
            self.assertIn("PHYSICAL_CONNECTOR_ENGAGEMENT_UNVALIDATED", diagnosis.unresolved_requirements)

    def test_entry_candidate_cannot_claim_connector_validation(self):
        with self.assertRaisesRegex(ValueError, "connector engagement"):
            EntryCandidate((0, 0, 0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0, 0, connector_transition_validated=True)

    def test_unsupported_geometry_and_frame_mismatch(self):
        diagnosis = diagnose_endpoint_entry(
            self.endpoints["egress/hvac"], self.e, self.pol, endpoint_frame_id="wrong-frame"
        )
        self.assertIs(diagnosis.classification, EntryClassification.COORDINATE_FRAME_MISMATCH)
        no_grid = replace(self.e, grids=tuple(grid for grid in self.e.grids if grid.system != "hvac"))
        missing = diagnose_endpoint_entry(self.endpoints["egress/hvac"], no_grid, self.pol, endpoint_frame_id=self.f)
        self.assertIs(missing.classification, EntryClassification.NO_COMPATIBLE_GRID)
        non_auth = replace(self.endpoints["egress/hvac"], provenance=EndpointProvenance.INFERRED)
        inferred = diagnose_endpoint_entry(non_auth, self.e, self.pol, endpoint_frame_id=self.f)
        self.assertIs(inferred.classification, EntryClassification.ENDPOINT_POSITION_NOT_AUTHORITATIVE)

    def test_bounded_termination_and_determinism(self):
        limited = diagnose_routing_problem_endpoints(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, max_radius_cells=0, max_candidates=1
        )
        for endpoint_id in ("egress/hvac", "egress/fire", "egress/water"):
            diagnosis = limited.diagnosis_for(endpoint_id)
            self.assertIs(diagnosis.classification, EntryClassification.NO_ENTRY_FOUND_WITHIN_LIMITS)
            self.assertIsNone(diagnosis.entry_candidate)
            self.assertLessEqual(diagnosis.explored_cell_count, 1)
        again = diagnose_routing_problem_endpoints(self.p, self.e, self.pol, endpoint_frame_id=self.f)
        self.assertEqual(again.to_canonical_dict(), self.report.to_canonical_dict())
        self.assertEqual(json.dumps(again.to_canonical_dict(), sort_keys=True), json.dumps(self.report.to_canonical_dict(), sort_keys=True))

    def test_authoritative_demands_preserved_in_both_modes(self):
        demand = [item.connection_id for item in self.p.connections]
        self.assertEqual(len(demand), 13)
        strict = route_multi_system(self.p, self.e, self.pol, endpoint_frame_id=self.f)
        exploratory = route_multi_system(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=EndpointCellMode.EXPLORATORY_ENTRY_CANDIDATE
        )
        for result, routed in ((strict, 5), (exploratory, 13)):
            self.assertEqual(list(result.required_connection_ids), demand)
            self.assertEqual([item.connection_id for item in result.connections], demand)
            self.assertEqual(result.count(result.connections[0].status.__class__.ROUTED), routed)
            self.assertFalse(result.connector_engagement_validated)
            self.assertEqual(result.grid_paths_complete, routed == 13)
            self.assertIsNotNone(result.endpoint_entry)
            self.assertEqual(len(result.endpoint_entry.diagnoses), len({eid for c in self.p.connections for eid in (c.start_endpoint_id, c.end_endpoint_id)}))
        for record in exploratory.connections:
            if record.system in ("hvac", "fire", "water"):
                self.assertIn("EXPLORATORY_ENTRY_CELL_USED", record.unvalidated_requirements)
                self.assertIn("PHYSICAL_CONNECTOR_ENGAGEMENT_UNVALIDATED", record.unvalidated_requirements)
                self.assertIn("GRID_PATH_COMPLETE_DOES_NOT_IMPLY_CONNECTOR_ENGAGEMENT", record.unvalidated_requirements)
        with self.assertRaisesRegex(ValueError, "connector engagement validation"):
            replace(exploratory, connector_engagement_validated=True)

    def test_no_false_complete_physical_network_claim(self):
        exploratory = route_multi_system(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=EndpointCellMode.EXPLORATORY_ENTRY_CANDIDATE
        )
        payload = exploratory.to_canonical_dict()
        self.assertTrue(payload["grid_paths_complete"])
        self.assertFalse(payload["connector_engagement_validated"])
        self.assertFalse(payload["collision_free_claimed"])
        self.assertIn("CONNECTOR_ENGAGEMENT_UNEVALUATED", payload["unresolved_physical_requirements"])
        self.assertTrue(payload["summary"]["exploratory_endpoint_mode"])
        self.assertFalse(payload["summary"]["connector_engagement_validated"])

    def test_s01_routing_reproducibility_unchanged_for_strict_and_snap(self):
        first = route_multi_system(self.p, self.e, self.pol, endpoint_frame_id=self.f)
        second = route_multi_system(self.p, self.e, self.pol, endpoint_frame_id=self.f)
        self.assertEqual(first.to_canonical_json(), second.to_canonical_json())
        snap_a = route_multi_system(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE
        )
        snap_b = route_multi_system(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE
        )
        self.assertEqual(snap_a.to_canonical_json(), snap_b.to_canonical_json())
        self.assertEqual(first.count(first.connections[0].status.__class__.ROUTED), 5)
        self.assertEqual(snap_a.count(snap_a.connections[0].status.__class__.ROUTED), 13)


if __name__ == "__main__":
    unittest.main()
