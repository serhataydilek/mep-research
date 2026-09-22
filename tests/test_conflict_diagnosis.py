"""Synthetic graph coverage for Phase 6A; greedy cover minimality is not asserted."""

from __future__ import annotations

import copy
from pathlib import Path
import unittest

from src.conflict_diagnosis import diagnose_b0_conflicts, diagnose_route_conflicts


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"


def route(system: str, connection_id: str) -> dict:
    return {"system": system, "connection_id": connection_id, "path_found": True,
            "grid_route_length_m": 1.0, "bend_count": 0, "vertical_travel_m": 0.0}


def record(first: tuple[str, str], second: tuple[str, str], *, hard=True, clearance=True, hard_segments=1, clearance_segments=1, shared=0) -> dict:
    ordered = sorted((first[0], second[0]), key=("hvac", "drainage", "water", "fire", "electrical").index)
    return {"route_a_system": first[0], "route_a_connection_id": first[1],
            "route_b_system": second[0], "route_b_connection_id": second[1],
            "system_pair": "|".join(ordered), "hard_envelope_conflict": hard,
            "clearance_violation": clearance, "hard_segment_intersection_count": hard_segments,
            "clearance_segment_intersection_count": clearance_segments, "shared_centerline_cell_count": shared}


def diagnose(routes: list[dict], records: list[dict]) -> dict:
    routing = {"cases": [{"case_id": "s01-d01", "scenario_id": "s01", "demand_profile_id": "d01", "routes": routes}]}
    conflicts = {"cases": [{"case_id": "s01-d01", "conflict_details": records}]}
    return diagnose_route_conflicts(routing, conflicts)["cases"][0]


class ConflictDiagnosisTests(unittest.TestCase):
    def test_no_edge_graph_has_no_components_or_candidates(self):
        result = diagnose([route("hvac", "a"), route("water", "b")], [])
        self.assertEqual(0, result["selected_repair_candidate_count"])
        self.assertEqual(0, result["conflict_component_count"])
        self.assertTrue(result["current_violation_edge_cover_complete"])

    def test_one_hard_edge_selects_one_stable_candidate(self):
        result = diagnose([route("hvac", "a"), route("water", "b")], [record(("hvac", "a"), ("water", "b"))])
        self.assertEqual("HARD", result["component_diagnostics"][0]["edges"][0]["severity"])
        self.assertEqual(["hvac"], [item["system"] for item in result["selected_repair_candidates"]])
        self.assertTrue(result["current_violation_edge_cover_complete"])

    def test_clearance_only_edge_and_burdens(self):
        result = diagnose([route("hvac", "a"), route("water", "b")], [record(("hvac", "a"), ("water", "b"), hard=False, clearance=True, hard_segments=0, clearance_segments=3, shared=2)])
        edge = result["component_diagnostics"][0]["edges"][0]
        self.assertEqual("CLEARANCE_ONLY", edge["severity"])
        first = result["route_diagnostics"][0]
        self.assertEqual(1, first["clearance_only_partner_count"])
        self.assertEqual(0, first["hard_conflict_partner_count"])
        self.assertEqual(3, first["clearance_segment_intersection_burden"])
        self.assertEqual(1, first["shared_centerline_conflict_partner_count"])

    def test_same_system_record_is_not_a_primary_edge(self):
        result = diagnose([route("hvac", "a"), route("hvac", "b")], [record(("hvac", "a"), ("hvac", "b"))])
        self.assertEqual(0, result["violation_edge_count"])

    def test_disconnected_groups_and_component_order_are_stable(self):
        routes = [route("water", "w"), route("hvac", "h"), route("fire", "f"), route("drainage", "d")]
        records = [record(("water", "w"), ("fire", "f")), record(("hvac", "h"), ("drainage", "d"))]
        first, second = diagnose(routes, records), diagnose(routes, list(reversed(records)))
        self.assertEqual(["component_001", "component_002"], [component["component_id"] for component in first["component_diagnostics"]])
        self.assertEqual(first, second)

    def test_chain_is_one_component_and_selects_cover(self):
        routes = [route("hvac", "a"), route("drainage", "b"), route("water", "c")]
        result = diagnose(routes, [record(("hvac", "a"), ("drainage", "b")), record(("drainage", "b"), ("water", "c"))])
        self.assertEqual(1, result["conflict_component_count"])
        self.assertEqual("drainage", result["selected_repair_candidates"][0]["system"])
        self.assertEqual(1, result["selected_repair_candidate_count"])

    def test_star_selects_center_first(self):
        routes = [route("drainage", "center"), route("hvac", "a"), route("water", "b"), route("fire", "c")]
        records = [record(("drainage", "center"), (system, name)) for system, name in [("hvac", "a"), ("water", "b"), ("fire", "c")]]
        result = diagnose(routes, records)
        self.assertEqual("center", result["selected_repair_candidates"][0]["connection_id"])

    def test_hard_coverage_then_total_coverage_then_segment_burdens_rank(self):
        routes = [route("hvac", "a"), route("drainage", "b"), route("water", "c"), route("fire", "d")]
        records = [record(("hvac", "a"), ("drainage", "b"), hard=False, clearance=True, hard_segments=0, clearance_segments=99),
                   record(("water", "c"), ("fire", "d"), hard=True, clearance=True, hard_segments=1)]
        result = diagnose(routes, records)
        # Separate components: the hard edge's candidate ranks above clearance-only within its component.
        self.assertEqual(2, result["selected_repair_candidate_count"])
        self.assertTrue(result["current_violation_edge_cover_complete"])

    def test_only_incident_conflicting_routes_are_selected(self):
        routes = [route("hvac", "a"), route("water", "b"), route("fire", "free")]
        result = diagnose(routes, [record(("hvac", "a"), ("water", "b"))])
        self.assertNotIn("free", [item["connection_id"] for item in result["selected_repair_candidates"]])

    def test_inputs_are_not_mutated(self):
        routing = {"cases": [{"case_id": "s01-d01", "scenario_id": "s01", "demand_profile_id": "d01", "routes": [route("hvac", "a"), route("water", "b")]}]}
        conflicts = {"cases": [{"case_id": "s01-d01", "conflict_details": [record(("hvac", "a"), ("water", "b"))]}]}
        original_routing, original_conflicts = copy.deepcopy(routing), copy.deepcopy(conflicts)
        diagnose_route_conflicts(routing, conflicts)
        self.assertEqual(original_routing, routing)
        self.assertEqual(original_conflicts, conflicts)

    def test_greedy_cover_is_a_heuristic_not_minimum_assertion(self):
        # Required invariant is full current-edge coverage, not minimum vertex-cover cardinality.
        routes = [route("hvac", "a"), route("drainage", "b"), route("water", "c")]
        result = diagnose(routes, [record(("hvac", "a"), ("drainage", "b")), record(("drainage", "b"), ("water", "c"))])
        self.assertTrue(result["current_violation_edge_cover_complete"])

    def test_b0_integration_preserves_frozen_conflict_geometry(self):
        result = diagnose_b0_conflicts(SCENARIOS, DEMANDS, SYSTEMS)
        metrics = result["global_metrics"]
        self.assertEqual(12, len(result["cases"]))
        self.assertEqual(292, metrics["total_route_instances"])
        self.assertEqual(1256, metrics["total_hard_edges"])
        self.assertEqual(1, metrics["total_clearance_only_edges"])
        self.assertEqual(1257, metrics["total_violation_edges"])
        self.assertTrue(all(case["current_violation_edge_cover_complete"] for case in result["cases"]))
