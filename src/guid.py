"""Deterministic identifiers for semantic BIM elements."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5


def semantic_guid(element_kind: str, element_name: str) -> str:
    """Return a stable UUID for a semantic element name."""
    if not element_kind or not element_name:
        raise ValueError("Element kind and name must be non-empty.")

    return str(uuid5(NAMESPACE_URL, f"mep-research/{element_kind}/{element_name}"))
