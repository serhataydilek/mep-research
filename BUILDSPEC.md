# Build specification

## Phase 1: deterministic architecture model

### Goal

Generate a deterministic IFC architecture model from `config/building.json`.

This model provides the benchmark environment for later routing experiments; it does not add those experiments to Phase 1.

### In scope

- One configured storey
- Perimeter walls
- Structural columns based on the configured grid
- One centrally positioned service shaft
- Geometry for all required modeled elements
- Stable semantic element GUIDs across repeated runs

### Explicitly out of scope

- MEP routing
- HVAC terminals and route geometry
- LLM or agent functionality
- 3D visualization

### Phase 1 gate

1. The IFC opens successfully.
2. Exactly one storey exists.
3. Required perimeter walls exist.
4. Required structural columns exist.
5. One service shaft exists.
6. Required modeled elements have geometry.
7. Semantic element GUIDs are deterministic across repeated runs.

Phase 1 is implemented and validated: every gate item above is demonstrated by the focused test suite and IFC CLI verification.

## Phase 2A — Benchmark Scenario Framework

Phase 2A provides four deterministic architectural/structural configurations in `experiments/scenarios/` for future routing experiments. The benchmark CLI measures gross and interior floor area, fixed structural obstruction area, geometric obstruction ratio, plenum height, and a geometric routing-volume proxy. Geometric obstruction is fixed XY obstacle density, not MEP congestion; the proxy is not true available routing volume because MEP envelopes are not modeled yet. Results contain no timestamps or machine-dependent values.

### Phase 2A gate

1. Four scenarios validate.
2. Four scenarios generate valid IFC models.
3. Metrics are deterministic.
4. Obstruction ratios follow the intended ordering.
5. Generated benchmark results are reproducible.
6. Phase 1 regressions remain green.

## Phase 2B — Deterministic Routing Demand

Phase 2B separates three independent demand profiles from the four geometry scenarios. Five heterogeneous services use controlled nominal benchmark envelopes, not code-compliant engineering sizes, deterministic shaft source anchors, and terminal anchors in a simplified star topology. The resulting 4 × 3 cross-product reports raw demand metrics only; it creates routing problems, not paths or route occupancy.

### Phase 2B gate

1. All three demand profiles validate.
2. All 12 geometry-demand combinations generate successfully.
3. All anchors are geometrically valid.
4. Low, medium, and high terminal layouts are nested.
5. Deterministic output is byte-identical across runs.
6. Connection-direction semantics are correct.
7. Prior phase regressions remain green.

## Phase 2C — Routing Feasibility Hardening

Phase 2C applies nominal service-envelope plus clearance margins to routing centerlines and rejects plenum-infeasible cases before routing. Source anchors remain inside the shaft; deterministic room-side egress anchors define the horizontal routing boundary. A fixed breakout record links each source to its egress as benchmark metadata only, not an IFC opening or optimized route. Architecture remains unchanged and no pathfinding is implemented.

### Phase 2C gate

1. Every scenario can fit all nominal service envelopes vertically.
2. Every source anchor remains inside the shaft.
3. Every egress anchor is in the room-side routing domain.
4. Every terminal and egress anchor respects service-aware margins.
5. All horizontal requests use egress endpoints.
6. All 12 cases are deterministic.
7. Prior phase regressions remain green.

## Phase 3A — IFC-Derived Voxel Occupancy

Phase 3A derives fixed obstacle and shaft-reservation bounds from tessellated IFC geometry. The 0.1 m centerline grid rasterizes IFC AABBs, which is exact for the current orthogonal benchmark but not arbitrary rotated or curved IFC. Service-specific envelope margins and vertical clearance define occupancy; room-side demand endpoints snap deterministically to free cells. No pathfinding is implemented.

### Phase 3A gate

1. All four scenario IFCs voxelize successfully.
2. Twenty service-specific grids build deterministically.
3. Fixed obstacles come from tessellated IFC geometry.
4. Shaft volume is reserved.
5. Service margins affect occupancy.
6. Horizontal endpoints map to free cells within tolerance.
7. Summary output is reproducible.
8. Earlier phase regressions remain green.

## Phase 3B — B0 Independent 3D A*

Phase 3B routes every horizontal connection independently through its Phase 3A service-aware voxel grid. B0 uses six orthogonal neighbours, Manhattan heuristic, unit integer voxel-step cost, and a stable heap tie-break with fixed neighbour expansion. Raw voxel paths retain route length, horizontal and vertical travel, bends, endpoint snapping, and expanded-node metrics. B0 neither mutates occupancy nor considers MEP-to-MEP collisions, priorities, constructability, or rerouting; individual connection success is not a feasible-layout claim.

### Phase 3B gate

1. Deterministic A* correctness tests pass.
2. All 292 route instances are attempted.
3. Every successful route stays in FREE occupancy.
4. Repeated routing problems produce identical paths.
5. B0 does not mutate occupancy.
6. Nested-profile routes are invariant.
7. Generated route output is byte-identical across runs.
8. Earlier phase regressions remain green.
