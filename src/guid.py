"""Deterministic identifiers for semantic BIM elements."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

import ifcopenshell.guid


def semantic_uuid(semantic_path: str) -> UUID:
    """Return a stable UUID for an explicit hierarchical semantic path."""
    if not semantic_path or not semantic_path.strip():
        raise ValueError("semantic_path must be non-empty.")

    return uuid5(NAMESPACE_URL, f"mep-research/{semantic_path}")


def semantic_ifc_guid(semantic_path: str) -> str:
    """Return a deterministic IFC-compressed GlobalId for a semantic path."""
    return ifcopenshell.guid.compress(semantic_uuid(semantic_path).hex)
