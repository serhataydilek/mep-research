"""Focused tests for the Phase 1B IFC spatial skeleton."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.unit

from src.ifc_model import create_ifc_model


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "building.json"


def load_config() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def tessellated_bounds(
    model: ifcopenshell.file, slab: ifcopenshell.entity_instance
) -> tuple[float, float, float, float, float, float]:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, slab)
    vertices = shape.geometry.verts
    coordinates = list(zip(vertices[::3], vertices[1::3], vertices[2::3], strict=True))
    xs, ys, zs = zip(*coordinates, strict=True)
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


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
        self.assertEqual(len(self.model.by_type("IfcSlab")), self.config["floors"])

    def test_current_configuration_has_one_storey_at_zero_elevation(self) -> None:
        storeys = self.model.by_type("IfcBuildingStorey")

        self.assertEqual(len(storeys), 1)
        self.assertEqual(storeys[0].Elevation, 0.0)

    def test_current_configuration_has_one_slab(self) -> None:
        self.assertEqual(len(self.model.by_type("IfcSlab")), 1)

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

    def test_slab_geometry_matches_configured_dimensions(self) -> None:
        slab = self.model.by_type("IfcSlab")[0]
        minimum_x, maximum_x, minimum_y, maximum_y, minimum_z, maximum_z = tessellated_bounds(
            self.model, slab
        )

        self.assertIsNotNone(slab.Representation)
        self.assertTrue(slab.Representation.Representations)
        self.assertAlmostEqual(maximum_x - minimum_x, self.config["width_m"], places=6)
        self.assertAlmostEqual(maximum_y - minimum_y, self.config["length_m"], places=6)
        self.assertAlmostEqual(maximum_z - minimum_z, self.config["slab_thickness_m"], places=6)

    def test_slab_is_contained_in_exactly_one_matching_storey(self) -> None:
        slab = self.model.by_type("IfcSlab")[0]
        storey = self.model.by_type("IfcBuildingStorey")[0]

        self.assertEqual(len(slab.ContainedInStructure), 1)
        self.assertEqual(slab.ContainedInStructure[0].RelatingStructure, storey)

    def test_slab_global_id_is_deterministic_between_models(self) -> None:
        second_model = create_ifc_model(self.config)

        self.assertEqual(
            self.model.by_type("IfcSlab")[0].GlobalId,
            second_model.by_type("IfcSlab")[0].GlobalId,
        )

    def test_multiple_floors_create_slabs_at_storey_elevations(self) -> None:
        config = copy.deepcopy(self.config)
        config["floors"] = 3
        model = create_ifc_model(config)

        self.assertEqual(len(model.by_type("IfcSlab")), 3)
        self.assertEqual(
            [tessellated_bounds(model, slab)[4] for slab in model.by_type("IfcSlab")],
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
        self.assertEqual(len(reopened.by_type("IfcSlab")), 1)
        minimum_x, maximum_x, minimum_y, maximum_y, minimum_z, maximum_z = tessellated_bounds(
            reopened, reopened.by_type("IfcSlab")[0]
        )
        self.assertAlmostEqual(maximum_x - minimum_x, self.config["width_m"], places=6)
        self.assertAlmostEqual(maximum_y - minimum_y, self.config["length_m"], places=6)
        self.assertAlmostEqual(maximum_z - minimum_z, self.config["slab_thickness_m"], places=6)
