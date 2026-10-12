"""Phase 9E.0 sequential multi-system coordination tests."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.clearance_policy import SameSystemClearanceStatus, legacy_reservation_margin_m
from src.multi_system_diagnostics import diagnose_multi_system_conflicts
from src.multi_system_routing import ConnectionRouteStatus, EndpointCellMode, route_multi_system
from src.sequential_coordination import (
    COORDINATION_METHOD_ID,
    ReservationStatus,
    clone_grid_cells,
    coordinate_and_diagnose,
    coordinate_systems_sequentially,
    coordination_system_order,
    evidence_with_derived_grids,
    reserve_physical_edge_on_target,
    system_routing_problem,
)
from src.visualization_export import (
    COORDINATED_FIXTURE,
    controlled_s01_inputs,
    export_multi_system_fixture,
    export_multi_system_fixtures,
    validate_multi_system_fixture,
)
from src.voxel import SYSTEM_ORDER

ROOT = Path(__file__).resolve().parents[1]


class SequentialCoordinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p, cls.e, cls.pol, cls.f = controlled_s01_inputs()
        cls.strict = route_multi_system(cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f)
        cls.bench = route_multi_system(
            cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f, endpoint_cell_mode=EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY
        )
        cls.coord, cls.diagnostics = coordinate_and_diagnose(
            cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f, endpoint_cell_mode=EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY
        )

    def test_deterministic_system_ordering(self):
        order = coordination_system_order(self.p)
        self.assertEqual(order, tuple(system for system in SYSTEM_ORDER if system in {c.system.value for c in self.p.connections}))
        self.assertEqual(order, ("hvac", "drainage", "water", "fire", "electrical"))
        self.assertEqual(self.coord.system_order, order)
        self.assertEqual([step.system for step in self.coord.steps], list(order))
        self.assertEqual([step.priority_rank for step in self.coord.steps], [1, 2, 3, 4, 5])

    def test_exact_demand_coverage_and_no_silent_removal(self):
        demand = [item.connection_id for item in self.p.connections]
        self.assertEqual(len(demand), 13)
        self.assertEqual(list(self.coord.routing.required_connection_ids), demand)
        self.assertEqual([item.connection_id for item in self.coord.routing.connections], demand)
        self.assertEqual(
            sum(self.coord.routing.count(status) for status in ConnectionRouteStatus),
            13,
        )
        unresolved = [item for item in self.coord.routing.connections if item.status is not ConnectionRouteStatus.ROUTED]
        self.assertTrue(unresolved)
        self.assertTrue(all(item.reason for item in unresolved))

    def test_source_and_derived_grids_remain_separate(self):
        before = {grid.system: grid.cells_sha256 for grid in self.e.grids}
        derived = clone_grid_cells(self.e)
        source = self.e.grid_for("hvac")
        target = self.e.grid_for("water")
        self.assertIsNotNone(source)
        self.assertIsNotNone(target)
        marked = reserve_physical_edge_on_target(
            target_cells=derived["water"],
            target_spec=target.spec,
            source_spec=source.spec,
            path_cells=((10, 10, 5), (11, 10, 5), (12, 10, 5)),
            prior_system="hvac",
            target_system="water",
            policy=self.pol,
        )
        self.assertGreater(marked, 0)
        self.assertEqual({grid.system: grid.cells_sha256 for grid in self.e.grids}, before)
        self.assertEqual(bytes(self.e.grid_for("water").cells), bytes(bytearray(self.e.grid_for("water").cells)))
        self.assertNotEqual(hashlib.sha256(bytes(derived["water"])).hexdigest(), before["water"])
        view = evidence_with_derived_grids(self.e, derived)
        self.assertEqual(self.e.grid_for("water").cells_sha256, before["water"])
        self.assertEqual(view.grid_for("water").cells_sha256, hashlib.sha256(bytes(derived["water"])).hexdigest())
        self.assertEqual(dict(self.coord.source_grid_digests), before)

    def test_world_space_reservations_and_service_specific_margins(self):
        hvac_water = legacy_reservation_margin_m(self.pol, "hvac", "water")
        hvac_electrical = legacy_reservation_margin_m(self.pol, "hvac", "electrical")
        self.assertNotEqual(hvac_water, hvac_electrical)
        events = [item for item in self.coord.reservation_events if item.prior_system == "hvac" and item.target_system == "water"]
        self.assertTrue(events)
        for event in events:
            self.assertEqual((event.planar_margin_m, event.vertical_margin_m), hvac_water)
            self.assertGreaterEqual(event.cells_marked, 0)
        # Shared MAIN reserved once per target: one event per owning physical edge / target pair.
        mains = [edge for edge in self.coord.routing.edges if edge.role == "MAIN" and edge.system == "hvac"]
        self.assertEqual(len(mains), 1)
        water_main_events = [item for item in events if item.physical_edge_id == mains[0].physical_edge_id]
        self.assertEqual(len(water_main_events), 1)

    def test_no_unauthorized_cross_system_sharing(self):
        by_id = {item.connection_id: item for item in self.coord.routing.connections}
        for edge in self.coord.routing.edges:
            self.assertEqual(edge.system, edge.owner_system)
            for connection_id in edge.connection_ids:
                self.assertEqual(by_id[connection_id].system, edge.system)

    def test_reservation_conflicts_and_lower_priority_effects(self):
        self.assertGreater(sum(item.cells_marked for item in self.coord.reservation_events), 0)
        water = next(item for item in self.coord.steps if item.system == "water")
        electrical = next(item for item in self.coord.steps if item.system == "electrical")
        self.assertEqual(water.routed_connection_ids, ())
        self.assertEqual(electrical.routed_connection_ids, ())
        self.assertIs(water.reservation_status, ReservationStatus.SKIPPED_NO_ROUTED_EDGES)
        self.assertTrue(water.reason and "ENDPOINT_CELL_NOT_FREE" in water.reason)
        self.assertTrue(electrical.reason and "ENDPOINT_CELL_NOT_FREE" in electrical.reason)
        self.assertEqual(self.coord.routing.count(ConnectionRouteStatus.ROUTED), 8)
        self.assertEqual(self.diagnostics["summary"]["inter_system_conflicting_pair_count"], 0)

    def test_bounded_termination_and_failure_preservation(self):
        limited = coordinate_systems_sequentially(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, max_expansions=1, max_proposal_attempts=1
        )
        self.assertEqual(len(limited.routing.connections), 13)
        self.assertEqual(limited.routing.count(ConnectionRouteStatus.FAILED), 13)
        self.assertTrue(all(item.reason and item.reason.startswith("SEARCH_LIMIT_REACHED@") for item in limited.routing.connections))
        self.assertLessEqual(limited.elapsed_s, 30.0)

    def test_explicit_endpoint_entry_mode_and_no_false_certification(self):
        self.assertIs(self.coord.endpoint_cell_mode, EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY)
        self.assertTrue(self.coord.routing.inter_system_reservation_applied)
        self.assertFalse(self.coord.routing.strict_mode)
        self.assertFalse(self.coord.routing.connector_engagement_validated)
        self.assertFalse(self.coord.collision_free_claimed)
        self.assertFalse(self.coord.to_canonical_dict()["collision_free_claimed"])
        self.assertIn("RESERVATION_IS_AABB_PROXY_NOT_CONTINUOUS_GEOMETRY", self.coord.to_canonical_dict()["unresolved_physical_requirements"])
        with self.assertRaisesRegex(ValueError, "collision freedom"):
            replace(self.coord, collision_free_claimed=True)
        with self.assertRaisesRegex(ValueError, "connector engagement"):
            replace(self.coord, connector_engagement_validated=True)

    def test_independent_diagnostics_agreement_and_same_system_undefined(self):
        again = diagnose_multi_system_conflicts(self.coord.routing, self.e, self.pol)
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.diagnostics, sort_keys=True))
        self.assertEqual(self.diagnostics["checks"]["clearance_policy_consistency"]["status"], "PASS")
        same = self.diagnostics["checks"]["same_system_independent_run_overlap"]
        self.assertEqual(same["status"], "EVALUATED_CLEARANCE_UNRESOLVED")
        self.assertTrue(all(status == SameSystemClearanceStatus.UNDEFINED.value for status in same["clearance_rule_status_by_system"].values()))
        self.assertFalse(self.diagnostics["geometric_clash_certified"])
        self.assertFalse(self.diagnostics["collision_free_claimed"])

    def test_determinism_and_canonical_serialization(self):
        again, diagnostics = coordinate_and_diagnose(
            self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY
        )
        # Wall-clock elapsed_s is not part of the algorithmic digest.
        left = again.to_canonical_dict()
        right = self.coord.to_canonical_dict()
        left["elapsed_s"] = right["elapsed_s"] = 0.0
        left["summary"]["elapsed_s"] = right["summary"]["elapsed_s"] = 0.0
        self.assertEqual(json.dumps(left, sort_keys=True), json.dumps(right, sort_keys=True))
        self.assertEqual(again.routing.to_canonical_json(), self.coord.routing.to_canonical_json())
        self.assertEqual(json.dumps(diagnostics, sort_keys=True), json.dumps(self.diagnostics, sort_keys=True))
        payload = json.loads(self.coord.to_canonical_json())
        self.assertEqual(payload["method_id"], COORDINATION_METHOD_ID)
        self.assertEqual(payload["routing"]["inter_system_reservation_applied"], True)

    def test_baseline_comparison_table_values(self):
        strict_d = diagnose_multi_system_conflicts(self.strict, self.e, self.pol)
        bench_d = diagnose_multi_system_conflicts(self.bench, self.e, self.pol)
        rows = (
            ("strict", self.strict.count(ConnectionRouteStatus.ROUTED), strict_d["summary"]["inter_system_conflicting_pair_count"], strict_d["summary"]["same_system_unexplained_overlap_pair_count"]),
            ("bench", self.bench.count(ConnectionRouteStatus.ROUTED), bench_d["summary"]["inter_system_conflicting_pair_count"], bench_d["summary"]["same_system_unexplained_overlap_pair_count"]),
            ("coord", self.coord.routing.count(ConnectionRouteStatus.ROUTED), self.diagnostics["summary"]["inter_system_conflicting_pair_count"], self.diagnostics["summary"]["same_system_unexplained_overlap_pair_count"]),
        )
        self.assertEqual(rows[0], ("strict", 5, 2, 0))
        self.assertEqual(rows[1], ("bench", 13, 28, 2))
        self.assertEqual(rows[2], ("coord", 8, 0, 2))
        # Completeness and conflicts measured together: coordination reduces conflicts but also routed count.
        self.assertLess(rows[2][1], rows[1][1])
        self.assertLess(rows[2][2], rows[1][2])

    def test_system_slice_helper_preserves_identities(self):
        water = system_routing_problem(self.p, "water")
        self.assertEqual([item.connection_id for item in water.connections], ["water/0", "water/1"])
        self.assertTrue(all(item.system.value == "water" for item in water.endpoints))

    def test_coordinated_viewer_fixture_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / COORDINATED_FIXTURE
            data = export_multi_system_fixture(self.coord.routing, self.diagnostics, self.e, self.p, path)
            validate_multi_system_fixture(data)
            self.assertTrue(data["inter_system_reservation_applied"])
            self.assertEqual(data["summary"]["routed_connection_count"], 8)
            self.assertEqual(len(data["connections"]), 13)
            self.assertFalse(data["geometric_clash_certified"])
            written = export_multi_system_fixtures(Path(directory), include_coordinated=True)
            self.assertIn("SEQUENTIAL_RESERVATION_V1", written)
            self.assertTrue((Path(directory) / COORDINATED_FIXTURE).exists())


if __name__ == "__main__":
    unittest.main()
