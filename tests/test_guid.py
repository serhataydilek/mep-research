"""Focused tests for deterministic semantic UUIDs."""

from __future__ import annotations

from uuid import UUID
import unittest

import ifcopenshell.guid

from src.guid import semantic_ifc_guid, semantic_uuid


class SemanticUuidTests(unittest.TestCase):
    def test_same_path_produces_same_uuid(self) -> None:
        path = "storey/0/wall/north"

        self.assertEqual(semantic_uuid(path), semantic_uuid(path))

    def test_different_paths_produce_different_uuids(self) -> None:
        self.assertNotEqual(
            semantic_uuid("storey/0/wall/north"),
            semantic_uuid("storey/0/wall/south"),
        )

    def test_empty_path_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "semantic_path"):
            semantic_uuid("")

    def test_result_is_uuid_and_remains_stable_across_calls(self) -> None:
        expected = semantic_uuid("storey/0/shaft/main")

        self.assertIsInstance(expected, UUID)
        for _ in range(3):
            self.assertEqual(semantic_uuid("storey/0/shaft/main"), expected)

    def test_ifc_guid_is_deterministic_and_compressed(self) -> None:
        guid = semantic_ifc_guid("storey/0/wall/north")

        self.assertEqual(guid, semantic_ifc_guid("storey/0/wall/north"))
        self.assertEqual(len(guid), 22)
        self.assertEqual(ifcopenshell.guid.expand(guid), semantic_uuid("storey/0/wall/north").hex)
