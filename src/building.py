"""Phase 1 building-model generation entry point (not yet implemented)."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_building_config
from .ifc_model import create_ifc_model


def load_model_input(config_path: Path) -> dict[str, object]:
    """Load and validate Phase 1 model input."""
    return load_building_config(config_path)


def main() -> int:
    """Generate the configured IFC spatial skeleton."""
    parser = argparse.ArgumentParser(description="Generate the IFC spatial skeleton.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    config = load_model_input(args.config)
    model = create_ifc_model(config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(args.output))
    print(f"Wrote IFC model to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
