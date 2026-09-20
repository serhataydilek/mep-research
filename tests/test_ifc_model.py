"""Focused tests for the Phase 1B IFC spatial skeleton."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import ifcopenshell
import ifcopenshell.util.unit

from src.ifc_model import create_ifc_model


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "building.json"


def load_config() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class IfcModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.model = create_ifc_model(self.config)

    def test_schema_and_spatial_entity_counts(self) -> None:
        self.assertEqual(self.model.schema, "IFC4")
        self.assertEqual(len(self.model.by_type("IfcProject")), 1)
        self.assertEqual(len(self.model.by_type("IfcSite")), 1)
        self.assertEqual(len(self.model.by_type("IfcBuilding")), 1)
        self.assertEqual(len(self.model.by_type("IfcBuildingStorey")), self.config["floors"])

    def test_current_configuration_has_one_storey_at_zero_elevation(self) -> None:
        storeys = self.model.by_type("IfcBuildingStorey")

        self.assertEqual(len(storeys), 1)
        self.assertEqual(storeys[0].Elevation, 0.0)

    def test_aggregation_links_project_to_storey(self) -> None:
        project = self.model.by_type("IfcProject")[0]
        site = self.model.by_type("IfcSite")[0]
        building = self.model.by_type("IfcBuilding")[0]
        storey = self.model.by_type("IfcBuildingStorey")[0]

        self.assertIn(site, project.IsDecomposedBy[0].RelatedObjects)
        self.assertIn(building, site.IsDecomposedBy[0].RelatedObjects)
        self.assertIn(storey, building.IsDecomposedBy[0].RelatedObjects)

    def test_multiple_floors_use_configured_elevations(self) -> None:
        config = copy.deepcopy(self.config)
        config["floors"] = 3
        model = create_ifc_model(config)

        self.assertEqual(
            [storey.Elevation for storey in model.by_type("IfcBuildingStorey")],
            [0.0, 3.5, 7.0],
        )

    def test_length_unit_scale_is_one_metre(self) -> None:
        self.assertEqual(ifcopenshell.util.unit.calculate_unit_scale(self.model), 1.0)

    def test_model_and_body_contexts_exist(self) -> None:
        contexts = self.model.by_type("IfcGeometricRepresentationContext")
        body_contexts = self.model.by_type("IfcGeometricRepresentationSubContext")

        self.assertTrue(any(context.ContextIdentifier == "Model" for context in contexts))
        self.assertTrue(
            any(
                context.ContextIdentifier == "Body" and context.TargetView == "MODEL_VIEW"
                for context in body_contexts
            )
        )

    def test_semantic_global_ids_are_deterministic_between_models(self) -> None:
        second_model = create_ifc_model(self.config)

        first_ids = [entity.GlobalId for entity in self.model.by_type("IfcRoot")]
        second_ids = [entity.GlobalId for entity in second_model.by_type("IfcRoot")]
        self.assertEqual(first_ids, second_ids)
        self.assertTrue(all(len(global_id) == 22 for global_id in first_ids))

    def test_generated_file_reopens(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "architecture.ifc"
            self.model.write(str(output))
            reopened = ifcopenshell.open(output)

        self.assertEqual(reopened.schema, "IFC4")
        self.assertEqual(len(reopened.by_type("IfcBuildingStorey")), 1)
