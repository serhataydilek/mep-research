"""Focused regressions for the independent Phase 10R-B benchmark."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.multistorey_benchmark import load_fixture, run_fixture, write_fixture


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "experiments" / "multistorey"


class MultistoreyBenchmarkTests(unittest.TestCase):
    def test_geometry_has_two_storeys_open_shaft_and_required_architecture(self):
        data = load_fixture(FIXTURES / "fixture_a_known_feasible.json")
        self.assertEqual(len(data["storeys"]), 2)
        self.assertGreater(data["shaft"]["bounds"][5], data["shaft"]["bounds"][4])
        self.assertIn("SLAB_OPENING", {item["kind"] for item in data["openings"]})
        self.assertTrue({"SLAB", "WALL", "COLUMN", "BEAM"} <= {item["kind"] for item in data["elements"]})
        self.assertTrue({"ROOM", "HALLWAY", "CEILING_SERVICE_SPACE"} <= {item["kind"] for item in data["spaces"]})

    def test_known_feasible_fixture_is_globally_validated_by_declared_proxy_rules(self):
        result = run_fixture(FIXTURES / "fixture_a_known_feasible.json")
        self.assertEqual(result["validation"]["status"], "VALIDATED_GRID_AABB_PROXY")
        self.assertFalse(result["validation"]["not_physical_certification"] is False)
        self.assertEqual(result["validation"]["hard_conflict_count"], 0)
        self.assertTrue(all(edge["status"] == "ROUTED" for edge in result["edges"]))
        self.assertTrue(all(edge["vertical_travel_m"] > 0 for edge in result["edges"]))
        drainage = next(edge for edge in result["edges"] if edge["system"] == "drainage")
        self.assertTrue(drainage["gravity"]["drainage_gravity_pass"])

    def test_constrained_fixture_is_not_mislabeled_infeasible(self):
        result = run_fixture(FIXTURES / "fixture_b_constrained.json")
        self.assertEqual(result["validation"]["status"], "CONSTRAINED_CONFLICTS_DETECTED")
        self.assertGreater(result["validation"]["hard_conflict_count"], 0)
        self.assertTrue(all(edge["status"] == "ROUTED" for edge in result["edges"]))
        self.assertEqual(set(result["source_identity"]["fixture_sha256"]), {"fixture_a_known_feasible.json", "fixture_b_constrained.json"})

    def test_export_is_deterministic_and_coordinate_consistent(self):
        fixture = FIXTURES / "fixture_a_known_feasible.json"
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first, second = Path(one) / "fixture.json", Path(two) / "fixture.json"
            data = write_fixture(fixture, first); write_fixture(fixture, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
        size = data["coordinate_mapping"]["voxel_size_m"]
        for edge in data["edges"]:
            for cell, point in ((edge["grid_cells"][0], edge["world_polyline_m"][0]), (edge["grid_cells"][-1], edge["world_polyline_m"][-1])):
                self.assertEqual(point, [round((axis + .5) * size, 9) for axis in cell])

    def test_viewer_exports_are_present(self):
        viewer = (ROOT / "viewer" / "index.html").read_text(encoding="utf-8")
        self.assertIn("multistorey_feasible_fixture.json", viewer)
        self.assertIn("MULTISTOREY_MEP_DEBUG", viewer)
        self.assertIn("addGuideBox", viewer)


if __name__ == "__main__":
    unittest.main()
