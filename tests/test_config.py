"""Focused tests for building configuration validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.config import load_building_config


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "building.json"


class BuildingConfigTests(unittest.TestCase):
    def load_with_changes(self, changes: dict[str, object]) -> dict[str, object]:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config = copy.deepcopy(config)
        for field, value in changes.items():
            if "." in field:
                parent, child = field.split(".", maxsplit=1)
                config[parent][child] = value
            else:
                config[field] = value

        with TemporaryDirectory() as directory:
            path = Path(directory) / "building.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            return load_building_config(path)

    def test_current_configuration_loads(self) -> None:
        config = load_building_config(CONFIG_PATH)

        self.assertEqual(config["floors"], 1)
        self.assertEqual(config["wall_thickness_m"], 0.2)
        self.assertEqual(config["shaft"], {"width_m": 2.0, "length_m": 2.0, "position": "center"})

    def test_zero_or_negative_wall_thickness_fails(self) -> None:
        for wall_thickness in (0.0, -0.1):
            with self.subTest(wall_thickness=wall_thickness):
                with self.assertRaisesRegex(ValueError, "wall_thickness_m"):
                    self.load_with_changes({"wall_thickness_m": wall_thickness})

    def test_grid_and_column_configuration_validates(self) -> None:
        config = self.load_with_changes({})

        self.assertEqual(config["column_size_m"], 0.4)
        self.assertEqual(config["grid_spacing_x_m"], 5.0)
        self.assertEqual(config["grid_spacing_y_m"], 5.0)

    def test_grid_spacing_at_building_extent_fails(self) -> None:
        for field, value in (("grid_spacing_x_m", 20.0), ("grid_spacing_y_m", 15.0)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    self.load_with_changes({field: value})

    def test_column_size_at_grid_spacing_fails(self) -> None:
        for field in ("grid_spacing_x_m", "grid_spacing_y_m"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "column_size_m"):
                    self.load_with_changes({"column_size_m": 5.0})

    def test_oversized_wall_thickness_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "wall_thickness_m"):
            self.load_with_changes({"wall_thickness_m": 15.0})

    def test_missing_required_key_fails(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        del config["width_m"]

        with TemporaryDirectory() as directory:
            path = Path(directory) / "building.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "width_m"):
                load_building_config(path)

    def test_negative_building_dimension_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "width_m"):
            self.load_with_changes({"width_m": -1.0})

    def test_ceiling_at_floor_to_floor_height_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "ceiling_height_m"):
            self.load_with_changes({"ceiling_height_m": 3.5})

    def test_ceiling_and_slab_without_routing_plenum_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "ceiling_height_m plus slab_thickness_m"):
            self.load_with_changes({"ceiling_height_m": 3.3})

    def test_invalid_shaft_position_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "shaft.position"):
            self.load_with_changes({"shaft.position": "north"})

    def test_oversized_shaft_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "shaft"):
            self.load_with_changes({"shaft.width_m": 21.0})

    def test_shaft_wall_envelope_touching_perimeter_inner_boundary_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "shaft wall envelope"):
            self.load_with_changes({"shaft.width_m": 19.2})

    def test_shaft_column_intersection_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "intersects the shaft"):
            self.load_with_changes({"shaft.length_m": 6.0})

    def test_zero_voxel_size_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "routing.voxel_size_m"):
            self.load_with_changes({"routing.voxel_size_m": 0.0})
