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
