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
