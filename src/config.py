"""Configuration loading for the parametric building model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_building_config(path: Path) -> dict[str, Any]:
    """Load a building configuration, failing clearly for invalid JSON."""
    with path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)

    if not isinstance(config, dict):
        raise ValueError("Building configuration must be a JSON object.")

    return config
