"""Execute one DigitalHub V1 subpath reroute with the existing orthogonal 3D A*."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.digitalhub_port_pilot import execute_port_pilot


DEFAULT_DATA = ROOT / "validation" / "ifc_samples" / "external" / "rwth_digitalhub_v1"
DEFAULT_OUTPUT = ROOT / "experiments" / "digitalhub_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualify DigitalHub V1 ports and run one orthogonal 3D A* search.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    result = execute_port_pilot(args.data_dir, args.output_dir, threads=args.threads)
    print(json.dumps(result, indent=2))
    return 0 if result.get("candidates_evaluated") else 2


if __name__ == "__main__":
    raise SystemExit(main())
