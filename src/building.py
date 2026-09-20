"""Phase 1 building-model generation entry point (not yet implemented)."""

from __future__ import annotations

from pathlib import Path

from .config import load_building_config


def load_model_input(config_path: Path) -> dict[str, object]:
    """Load Phase 1 model input without generating IFC yet."""
    return load_building_config(config_path)
