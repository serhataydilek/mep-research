"""Focused tests for the post-research IFC sanity-validation harness."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import ifcopenshell

from src.config import load_building_config
from src.ifc_model import create_ifc_model
from src.ifc_validation import (
    ValidationOptions,
    build_validation_report,
    inspect_ifc,
    markdown_report,
    write_json,
    write_markdown,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "building.json"
IMMUTABLE_INPUTS = (
    *sorted((ROOT / "config").glob("*.json")),
    *sorted((ROOT / "experiments" / "scenarios").glob("*.json")),
    *sorted((ROOT / "experiments" / "demands").glob("*.json")),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IfcValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.config = load_building_config(CONFIG)
        self.path = self.directory / "control.ifc"
        create_ifc_model(self.config).write(str(self.path))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_unit_detection_reports_metre_factor(self) -> None:
        report = inspect_ifc(self.path)

        self.assertEqual(report["units"]["length_unit"], "METRE")
        self.assertEqual(report["units"]["conversion_factor_to_meters"], 1.0)
        self.assertEqual(report["pipeline_stages"]["unit_interpretation"]["status"], "PASS")

    def test_unit_detection_reads_millimetre_conversion(self) -> None:
        model = ifcopenshell.open(str(self.path))
        length_unit = next(unit for unit in model.by_type("IfcSIUnit") if unit.UnitType == "LENGTHUNIT")
        length_unit.Prefix = "MILLI"
        millimetre_path = self.directory / "millimetres.ifc"
        model.write(str(millimetre_path))

        units = inspect_ifc(millimetre_path)["units"]

        self.assertEqual(units["length_unit"], "MILLI_METRE")
        self.assertEqual(units["conversion_factor_to_meters"], 0.001)

    def test_entity_inventory_is_deterministic_and_inheritance_labeled(self) -> None:
        first = inspect_ifc(self.path)["entity_inventory"]
        second = inspect_ifc(self.path)["entity_inventory"]

        self.assertEqual(first, second)
        self.assertEqual(first["counts"]["IfcWall"], 8)
        self.assertEqual(first["counts"]["IfcColumn"], 6)
        self.assertIn("overlap", first["counting_mode"])

    def test_hierarchy_and_storey_mapping_are_extracted(self) -> None:
        report = inspect_ifc(self.path)

        self.assertEqual(report["ifc_hierarchy"], {
            "project_count": 1, "site_count": 1, "building_count": 1,
            "building_storey_count": 1,
        })
        self.assertEqual(len(report["storeys"]), 1)
        self.assertEqual(report["storeys"][0]["entity_counts"]["IfcWall"], 8)
        self.assertEqual(report["storeys"][0]["entity_counts"]["IfcSpace"], 1)
        self.assertEqual(report["pipeline_stages"]["storey_mapping"]["status"], "PASS")

    def test_entity_without_representation_is_reported_without_crashing(self) -> None:
        model = ifcopenshell.open(str(self.path))
        model.by_type("IfcColumn")[0].Representation = None
        broken_geometry = self.directory / "missing-representation.ifc"
        model.write(str(broken_geometry))

        report = inspect_ifc(broken_geometry)

        self.assertEqual(report["geometry_summary"]["entities_without_representation"], 1)
        self.assertEqual(report["pipeline_stages"]["architectural_geometry_extraction"]["status"], "PARTIAL")

    def test_invalid_path_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            inspect_ifc(self.directory / "absent.ifc")

    def test_broken_ifc_returns_file_parsing_failure(self) -> None:
        broken = self.directory / "broken.ifc"
        broken.write_text("not an IFC file", encoding="utf-8")

        report = inspect_ifc(broken)

        self.assertEqual(report["pipeline_stages"]["file_parsing"]["status"], "FAIL")
        self.assertFalse(report["router_readiness"]["router_ready"])

    def test_voxel_safety_limit_prevents_pathological_allocation(self) -> None:
        report = inspect_ifc(self.path, options=ValidationOptions(max_voxel_cells=10))
        voxel = report["voxelization_summary"]

        self.assertFalse(voxel["processing_success"])
        self.assertFalse(voxel["operationally_reasonable"])
        self.assertIn("exceeding safety limit", voxel["failure_reason"])
        self.assertEqual(report["pipeline_stages"]["voxelization"]["status"], "FAIL")

    def test_every_partial_and_fail_status_has_a_reason(self) -> None:
        report = inspect_ifc(self.path, options=ValidationOptions(max_voxel_cells=10))

        for stage in report["pipeline_stages"].values():
            if stage["status"] in ("PARTIAL", "FAIL"):
                self.assertTrue(stage["reason"])

    def test_generated_ifc_is_explicitly_labeled_as_control(self) -> None:
        report = build_validation_report([self.path], control_paths=[self.path])
        sample = report["samples"][0]

        self.assertEqual(sample["metadata"]["sample_kind"], "CONTROL_GENERATED_IFC")
        self.assertTrue(sample["voxelization_summary"]["processing_success"])
        self.assertGreater(sample["voxelization_summary"]["reserved_shaft_voxel_count"], 0)
        self.assertTrue(any("not external IFC evidence" in item for item in sample["limitations_and_warnings"]))

    def test_router_readiness_does_not_invent_endpoints(self) -> None:
        report = inspect_ifc(self.path)

        self.assertFalse(report["router_readiness"]["router_ready"])
        self.assertTrue(any("endpoints" in reason for reason in report["router_readiness"]["reasons"]))

    def test_json_and_markdown_are_byte_deterministic(self) -> None:
        report = build_validation_report([self.path], control_paths=[self.path])
        first_json, second_json = self.directory / "first.json", self.directory / "second.json"
        first_md, second_md = self.directory / "first.md", self.directory / "second.md"

        write_json(report, first_json)
        write_json(report, second_json)
        write_markdown(report, first_md)
        write_markdown(report, second_md)

        self.assertEqual(first_json.read_bytes(), second_json.read_bytes())
        self.assertEqual(first_md.read_bytes(), second_md.read_bytes())
        self.assertIn("# Real IFC Sanity Validation", markdown_report(report))

    def test_validation_does_not_mutate_benchmark_inputs(self) -> None:
        before = {path: digest(path) for path in IMMUTABLE_INPUTS}

        inspect_ifc(self.path)

        self.assertEqual(before, {path: digest(path) for path in IMMUTABLE_INPUTS})


if __name__ == "__main__":
    unittest.main()
