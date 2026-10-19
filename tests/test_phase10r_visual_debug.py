"""Phase 10R read-only visual-feasibility debug export regressions."""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.visualization_export import PHASE10R_FIXTURE, export_phase10r_failing_fixture, validate_phase10r_fixture


ROOT = Path(__file__).resolve().parents[1]


class Phase10RVisualDebugTests(unittest.TestCase):
    def _export(self, directory: Path) -> tuple[Path, dict]:
        path = directory / PHASE10R_FIXTURE
        return path, export_phase10r_failing_fixture(path)

    def test_export_is_deterministic_and_versioned(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_path, first_data = self._export(Path(first))
            second_path, second_data = self._export(Path(second))
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        self.assertEqual(first_data, second_data)
        self.assertEqual(first_data["schema_version"], 3)
        self.assertEqual(first_data["debug_export_version"], "PHASE10R_DEBUG_EXPORT_V1")
        self.assertEqual(first_data["failure_case"]["grid_level_verdict"], "CONFLICTS_DETECTED")
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(first_data["source_identity"]["git_commit"], head)

    def test_route_identities_and_conflict_references_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            _, data = self._export(Path(directory))
        edge_ids = [edge["physical_edge_id"] for edge in data["edges"]]
        self.assertEqual(len(edge_ids), len(set(edge_ids)))
        self.assertTrue(all(edge_id.startswith("candidate/") for edge_id in edge_ids))
        self.assertTrue(data["conflicts"])
        self.assertTrue(all(set(conflict["physical_edge_ids"]) <= set(edge_ids) for conflict in data["conflicts"]))

    def test_coordinate_transform_and_geometry_are_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            _, data = self._export(Path(directory))
        self.assertIs(validate_phase10r_fixture(data), data)
        grids = {grid["system"]: grid for grid in data["coordinate_mapping"]["grids"]}
        for edge in (edge for edge in data["edges"] if edge["status"] == "ROUTED"):
            grid = grids[edge["system"]]
            self.assertEqual(len(edge["grid_cells"]), edge["grid_cell_count"])
            for cell, point in ((edge["grid_cells"][0], edge["world_polyline_m"][0]), (edge["grid_cells"][-1], edge["world_polyline_m"][-1])):
                expected = [round(grid["origin_m"][axis] + (cell[axis] + .5) * grid["voxel_size_m"], 9) for axis in range(3)]
                self.assertEqual(point, expected)
            self.assertIn("clearance_envelopes", edge)

    def test_source_configuration_hashes_match_exported_files(self):
        with tempfile.TemporaryDirectory() as directory:
            _, data = self._export(Path(directory))
        for relative_path, digest in data["source_identity"]["configuration_sha256"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest(), digest)
        self.assertEqual(data["failure_case"]["selection"], "s01/d01 BENCHMARK_CONTROLLED_ENTRY")

    def test_viewer_exposes_read_only_debug_layers_and_section_control(self):
        viewer = (ROOT / "viewer" / "index.html").read_text(encoding="utf-8")
        for required in (PHASE10R_FIXTURE, 'id="clearance"', 'id="clip-z"', "localClippingEnabled", "showConflict", "OrbitControls"):
            self.assertIn(required, viewer)


if __name__ == "__main__":
    unittest.main()
