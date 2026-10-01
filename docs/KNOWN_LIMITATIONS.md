# Known Limitations

## Intentional research simplifications

| Limitation | Current behavior | Production consequence |
|---|---|---|
| Same-system overlap | Primary conflict metrics exclude it; B1 permits independent sibling overlap. | Valid only when an explicit shared main/trunk/header/stack topology owns that geometry; otherwise collision. |
| Star demands | Independent shaft/egress-to-terminal connections. | Requires topology planning for mains, branches, tees, headers and shared-route semantics. |
| Breakout metadata | Source-to-egress is not routed. | Shaft/wall penetrations must be planned, authorized, sized and validated. |
| Synthetic endpoints | Deterministic anchor lattice. | Endpoints must have provenance: `IFC_DISTRIBUTION_PORT`, exporter metadata, manual, or explicitly `INFERRED`. |
| Voxel obstacles | Orthogonal walls/columns via AABBs. | Rotated/curved IFC needs robust solids/mesh or conservative signed-distance representation. |
| Clash proxy | Swept AABBs of compressed centerlines. | Final geometry needs pipes as cylinders/capsules, ducts/trays as swept profiles, and fitting envelopes. |
| Drainage | No uphill steps plus 1% aggregate stair-step slope. | Need continuous segment-level slopes, drops, fittings, topology and system/diameter rule sets. |

## Architecture and robustness blockers

- `clash.py` and `sequential.py` each hardcode `CLEARANCE_M = 0.05`, while scenario configuration also holds `routing.clearance_m`. Matching values preserve v1.0 consistency but are a desynchronization risk. Future policy has one versioned authoritative source.
- `verify_route_result()` uses route records as the expected identity set when `connection_requests` is absent. The controlled pipeline keeps all records, but production validation must receive immutable demand/topology separately and never let a candidate solution define its own obligations.
- Dense uniform grids are memory-sensitive; a real model can exceed the current 25,000,000-cell safety limit. Use sparse/tiled/corridor-bounded representation and multi-resolution planning.
- C0 checks selected proxy constraints, not building code, sizing, supports, access, fitting geometry, or fabrication tolerances.

## What external IFC evidence does and does not show

It shows parsing across IFC2X3/IFC4, schema and unit inspection, hierarchy, world-coordinate geometry, placement analysis, distribution inventory, and guarded voxel decisions. It does not show arbitrary real-model routing, connector extraction, penetration planning, topology reconstruction, fitting generation, IFC output, or fabrication-ready geometry. A model is not router-ready merely because it parses.

## Required reporting language

Use **hard conflict**, **clearance violation**, **route-pair conflict**, **route completion**, **constructability proxy**, and **benchmark feasibility** precisely. Never call P-CORE clash-free, call B1 a complete solution, call C0 code compliant, or call a search miss mathematically infeasible without proof.
