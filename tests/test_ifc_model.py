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
    model: ifcopenshell.file, element: ifcopenshell.entity_instance
) -> tuple[float, float, float, float, float, float]:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, element)
    vertices = shape.geometry.verts
    coordinates = list(zip(vertices[::3], vertices[1::3], vertices[2::3], strict=True))
    xs, ys, zs = zip(*coordinates, strict=True)
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def tessellated_volume(model: ifcopenshell.file, element: ifcopenshell.entity_instance) -> float:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, element)
    vertices = shape.geometry.verts
    faces = shape.geometry.faces
    volume = 0.0
    for index in range(0, len(faces), 3):
        first, second, third = (faces[index] * 3, faces[index + 1] * 3, faces[index + 2] * 3)
        ax, ay, az = vertices[first : first + 3]
        bx, by, bz = vertices[second : second + 3]
        cx, cy, cz = vertices[third : third + 3]
        volume += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    return abs(volume / 6)


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

    def test_current_configuration_creates_four_named_perimeter_walls(self) -> None:
        walls = [wall for wall in self.model.by_type("IfcWall") if not wall.Name.startswith("Shaft")]

        self.assertEqual(len(walls), 4)
        self.assertEqual(
            [wall.Name for wall in walls],
            ["South Wall 0", "North Wall 0", "West Wall 0", "East Wall 0"],
        )
        self.assertTrue(all(wall.Representation for wall in walls))

    def test_current_configuration_creates_four_shaft_walls_with_stable_order(self) -> None:
        shaft_walls = [wall for wall in self.model.by_type("IfcWall") if wall.Name.startswith("Shaft")]

        self.assertEqual(len(self.model.by_type("IfcWall")), 8)
        self.assertEqual(
            [wall.Name for wall in shaft_walls],
            [
                "Shaft South Wall 0",
                "Shaft North Wall 0",
                "Shaft West Wall 0",
                "Shaft East Wall 0",
            ],
        )
        expected_bounds = {
            "Shaft South Wall 0": (8.8, 11.2, 6.3, 6.5, 0.2, 3.5),
            "Shaft North Wall 0": (8.8, 11.2, 8.5, 8.7, 0.2, 3.5),
            "Shaft West Wall 0": (8.8, 9.0, 6.5, 8.5, 0.2, 3.5),
            "Shaft East Wall 0": (11.0, 11.2, 6.5, 8.5, 0.2, 3.5),
        }
        for wall in shaft_walls:
            with self.subTest(wall=wall.Name):
                for actual, expected in zip(tessellated_bounds(self.model, wall), expected_bounds[wall.Name], strict=True):
                    self.assertAlmostEqual(actual, expected, places=6)
                self.assertEqual(len(wall.ContainedInStructure), 1)
                self.assertEqual(wall.ContainedInStructure[0].RelatingStructure, self.model.by_type("IfcBuildingStorey")[0])

    def test_shaft_space_has_clear_zone_geometry_and_storey_aggregation(self) -> None:
        spaces = self.model.by_type("IfcSpace")

        self.assertEqual(len(spaces), 1)
        shaft_space = spaces[0]
        self.assertEqual(shaft_space.Name, "Service Shaft 0")
        self.assertEqual(tessellated_bounds(self.model, shaft_space), (9.0, 11.0, 6.5, 8.5, 0.0, 3.5))
        self.assertEqual(shaft_space.Decomposes[0].RelatingObject, self.model.by_type("IfcBuildingStorey")[0])
        containment = self.model.by_type("IfcBuildingStorey")[0].ContainsElements[0]
        self.assertNotIn(shaft_space, containment.RelatedElements)

    def test_slab_opening_is_deterministic_and_geometrically_voids_slab(self) -> None:
        slab = self.model.by_type("IfcSlab")[0]
        openings = self.model.by_type("IfcOpeningElement")
        second_model = create_ifc_model(self.config)

        self.assertEqual(len(openings), 1)
        opening = openings[0]
        self.assertEqual(opening.GlobalId, second_model.by_type("IfcOpeningElement")[0].GlobalId)
        self.assertEqual(tessellated_bounds(self.model, opening), (9.0, 11.0, 6.5, 8.5, -0.01, 0.21))
        self.assertFalse(opening.ContainedInStructure)
        self.assertEqual(len(slab.HasOpenings), 1)
        void_relationship = slab.HasOpenings[0]
        self.assertEqual(void_relationship.RelatedOpeningElement, opening)
        self.assertEqual(
            void_relationship.GlobalId,
            second_model.by_type("IfcSlab")[0].HasOpenings[0].GlobalId,
        )
        self.assertAlmostEqual(tessellated_volume(self.model, slab), 59.2, places=6)

    def test_current_configuration_creates_six_deterministic_columns(self) -> None:
        columns = self.model.by_type("IfcColumn")

        self.assertEqual(len(columns), 6)
        self.assertEqual(
            [column.Name for column in columns],
            [
                "Column 0-0-0",
                "Column 0-0-1",
                "Column 0-1-0",
                "Column 0-1-1",
                "Column 0-2-0",
                "Column 0-2-1",
            ],
        )
        self.assertTrue(all(column.Representation for column in columns))

    def test_column_geometry_bounds_centers_and_wall_clearance(self) -> None:
        expected_bounds = [
            (4.8, 5.2, 4.8, 5.2, 0.2, 3.5),
            (4.8, 5.2, 9.8, 10.2, 0.2, 3.5),
            (9.8, 10.2, 4.8, 5.2, 0.2, 3.5),
            (9.8, 10.2, 9.8, 10.2, 0.2, 3.5),
            (14.8, 15.2, 4.8, 5.2, 0.2, 3.5),
            (14.8, 15.2, 9.8, 10.2, 0.2, 3.5),
        ]
        wall_thickness = self.config["wall_thickness_m"]
        for column, expected in zip(self.model.by_type("IfcColumn"), expected_bounds, strict=True):
            with self.subTest(column=column.Name):
                bounds = tessellated_bounds(self.model, column)
                for actual, expected_value in zip(bounds, expected, strict=True):
                    self.assertAlmostEqual(actual, expected_value, places=6)
                self.assertAlmostEqual(bounds[1] - bounds[0], 0.4, places=6)
                self.assertAlmostEqual(bounds[3] - bounds[2], 0.4, places=6)
                self.assertAlmostEqual(bounds[5] - bounds[4], 3.3, places=6)
                self.assertGreaterEqual(bounds[0], wall_thickness)
                self.assertLessEqual(bounds[1], self.config["width_m"] - wall_thickness)
                self.assertGreaterEqual(bounds[2], wall_thickness)
                self.assertLessEqual(bounds[3], self.config["length_m"] - wall_thickness)

    def test_columns_have_deterministic_ids_and_storey_containment(self) -> None:
        second_model = create_ifc_model(self.config)
        storey = self.model.by_type("IfcBuildingStorey")[0]
        columns = self.model.by_type("IfcColumn")

        self.assertEqual(
            [column.GlobalId for column in columns],
            [column.GlobalId for column in second_model.by_type("IfcColumn")],
        )
        for column in columns:
            self.assertEqual(len(column.ContainedInStructure), 1)
            self.assertEqual(column.ContainedInStructure[0].RelatingStructure, storey)

        containment = storey.ContainsElements[0]
        self.assertEqual(len(containment.RelatedElements), 15)

    def test_walls_have_deterministic_ids_and_storey_containment(self) -> None:
        second_model = create_ifc_model(self.config)
        storey = self.model.by_type("IfcBuildingStorey")[0]
        walls = self.model.by_type("IfcWall")

        self.assertEqual(
            [wall.GlobalId for wall in walls],
            [wall.GlobalId for wall in second_model.by_type("IfcWall")],
        )
        for wall in walls:
            self.assertEqual(len(wall.ContainedInStructure), 1)
            self.assertEqual(wall.ContainedInStructure[0].RelatingStructure, storey)

    def test_wall_tessellated_bounds_form_the_configured_perimeter(self) -> None:
        expected_bounds = {
            "South Wall 0": (0.0, 20.0, 0.0, 0.2, 0.2, 3.5),
            "North Wall 0": (0.0, 20.0, 14.8, 15.0, 0.2, 3.5),
            "West Wall 0": (0.0, 0.2, 0.2, 14.8, 0.2, 3.5),
            "East Wall 0": (19.8, 20.0, 0.2, 14.8, 0.2, 3.5),
        }

        for wall in self.model.by_type("IfcWall"):
            if wall.Name not in expected_bounds:
                continue
            with self.subTest(wall=wall.Name):
                bounds = tessellated_bounds(self.model, wall)
                self.assertIsNotNone(wall.Representation)
                for actual, expected in zip(bounds, expected_bounds[wall.Name], strict=True):
                    self.assertAlmostEqual(actual, expected, places=6)
                self.assertLessEqual(bounds[0], self.config["width_m"])
                self.assertLessEqual(bounds[1], self.config["width_m"])
                self.assertLessEqual(bounds[2], self.config["length_m"])
                self.assertLessEqual(bounds[3], self.config["length_m"])

    def test_multiple_floors_create_twelve_walls_at_expected_elevations(self) -> None:
        config = copy.deepcopy(self.config)
        config["floors"] = 3
        model = create_ifc_model(config)
        walls = [wall for wall in model.by_type("IfcWall") if not wall.Name.startswith("Shaft")]

        self.assertEqual(len(walls), 12)
        for floor_index in range(3):
            floor_walls = walls[floor_index * 4 : (floor_index + 1) * 4]
            expected_bottom = floor_index * config["floor_to_floor_m"] + config["slab_thickness_m"]
            expected_top = (floor_index + 1) * config["floor_to_floor_m"]
            storey = model.by_type("IfcBuildingStorey")[floor_index]
            for wall in floor_walls:
                bounds = tessellated_bounds(model, wall)
                self.assertAlmostEqual(bounds[4], expected_bottom, places=6)
                self.assertAlmostEqual(bounds[5], expected_top, places=6)
                self.assertEqual(wall.ContainedInStructure[0].RelatingStructure, storey)

    def test_multiple_floors_create_shaft_elements_with_storey_correct_geometry(self) -> None:
        config = copy.deepcopy(self.config)
        config["floors"] = 3
        model = create_ifc_model(config)

        self.assertEqual(len(model.by_type("IfcSpace")), 3)
        self.assertEqual(len([wall for wall in model.by_type("IfcWall") if wall.Name.startswith("Shaft")]), 12)
        self.assertEqual(len(model.by_type("IfcOpeningElement")), 3)
        for floor_index, (storey, slab, space, opening) in enumerate(
            zip(
                model.by_type("IfcBuildingStorey"),
                model.by_type("IfcSlab"),
                model.by_type("IfcSpace"),
                model.by_type("IfcOpeningElement"),
                strict=True,
            )
        ):
            self.assertEqual(len(slab.HasOpenings), 1)
            self.assertEqual(slab.HasOpenings[0].RelatedOpeningElement, opening)
            self.assertEqual(space.Decomposes[0].RelatingObject, storey)
            self.assertEqual(tessellated_bounds(model, space)[4:], (floor_index * 3.5, (floor_index + 1) * 3.5))
            floor_shaft_walls = [
                wall for wall in model.by_type("IfcWall") if wall.Name.endswith(f" {floor_index}") and wall.Name.startswith("Shaft")
            ]
            self.assertEqual(len(floor_shaft_walls), 4)
            for wall in floor_shaft_walls:
                bounds = tessellated_bounds(model, wall)
                self.assertEqual(bounds[4:], (floor_index * 3.5 + 0.2, (floor_index + 1) * 3.5))
                self.assertEqual(wall.ContainedInStructure[0].RelatingStructure, storey)

    def test_multiple_floors_create_eighteen_columns_at_expected_elevations(self) -> None:
        config = copy.deepcopy(self.config)
        config["floors"] = 3
        model = create_ifc_model(config)
        columns = model.by_type("IfcColumn")

        self.assertEqual(len(columns), 18)
        for floor_index in range(3):
            floor_columns = columns[floor_index * 6 : (floor_index + 1) * 6]
            storey = model.by_type("IfcBuildingStorey")[floor_index]
            expected_bottom = floor_index * config["floor_to_floor_m"] + config["slab_thickness_m"]
            expected_top = (floor_index + 1) * config["floor_to_floor_m"]
            for column in floor_columns:
                bounds = tessellated_bounds(model, column)
                self.assertAlmostEqual(bounds[4], expected_bottom, places=6)
                self.assertAlmostEqual(bounds[5], expected_top, places=6)
                self.assertEqual(column.ContainedInStructure[0].RelatingStructure, storey)

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
        self.assertEqual(len(reopened.by_type("IfcWall")), 8)
        self.assertEqual(len(reopened.by_type("IfcColumn")), 6)
        self.assertEqual(len(reopened.by_type("IfcSpace")), 1)
        self.assertEqual(len(reopened.by_type("IfcOpeningElement")), 1)
        minimum_x, maximum_x, minimum_y, maximum_y, minimum_z, maximum_z = tessellated_bounds(
            reopened, reopened.by_type("IfcSlab")[0]
        )
        self.assertAlmostEqual(maximum_x - minimum_x, self.config["width_m"], places=6)
        self.assertAlmostEqual(maximum_y - minimum_y, self.config["length_m"], places=6)
        self.assertAlmostEqual(maximum_z - minimum_z, self.config["slab_thickness_m"], places=6)
        self.assertEqual(tessellated_bounds(reopened, reopened.by_type("IfcSpace")[0]), (9.0, 11.0, 6.5, 8.5, 0.0, 3.5))
        self.assertEqual(tessellated_bounds(reopened, reopened.by_type("IfcOpeningElement")[0]), (9.0, 11.0, 6.5, 8.5, -0.01, 0.21))
