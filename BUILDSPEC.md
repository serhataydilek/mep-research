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

## Phase 3C — B0 Inter-System Conflict Evaluation

Phase 3C jointly evaluates immutable B0 paths after independent routing. Its primary metrics cover unordered inter-system route pairs; same-system centerline overlap is a separate diagnostic. Maximal collinear centerlines are swept into nominal service AABBs, with 0.05 m total pairwise clearance represented by 0.025 m inflation per route. Positive nominal overlap is a hard envelope conflict; inflated-only overlap is a clearance-only violation. These deterministic benchmark proxies are not exact fabrication clash detection and never modify routing.

### Phase 3C gate

1. All 12 B0 cases evaluate.
2. Each unordered inter-system pair is tested once.
3. Hard conflicts and clearance-only violations are distinguished.
4. Same-system overlap is excluded from primary metrics.
5. Nested-profile classifications are invariant.
6. Output is byte-identical across runs.
7. B0 paths remain unchanged.
8. Earlier phase regressions remain green.

## Phase 4A — B1 Fixed-Priority Sequential Routing

Phase 4A uses the fixed controlled priority HVAC, drainage, water, fire, electrical. Successful higher-priority systems reserve pair-specific nominal-envelope plus 0.05 m clearance space on copied lower-system grids; same-system requests remain independent and may overlap. Earlier routes are never repaired, so a blocked endpoint or no path is a legitimate order-dependent baseline failure. B1 generates routes only: it does not evaluate B1 clashes in this phase.

### Phase 4A gate

1. Fixed priority is deterministic.
2. All 292 requests are attempted.
3. Higher-priority successful routes dynamically constrain lower systems.
4. Same-system routes do not block each other.
5. Base Phase 3A occupancy is unchanged.
6. First-priority HVAC routes match B0.
7. B1 results are byte-identical across runs.
8. Earlier phase regressions remain green.

## Phase 4B — B0 vs B1 Baseline Comparison

Phase 4B applies the same deterministic swept-envelope evaluator to successful B0 and B1 geometry. Conflict rates use only successful inter-system route pairs, so connection success remains a required companion metric. A common-success subset compares both geometries on identical routed connection membership, while routing-complete-and-compliant counts remain provisional coordination metrics rather than feasible-layout claims. Fixed-priority order dependence is descriptive baseline behavior; no routes are modified.

### Phase 4B gate

1. Both baselines attempt all 292 requests.
2. B0 conflict evaluation remains byte-identical.
3. B1 uses the same geometric evaluator.
4. Common-success pair denominators match between methods.
5. Routing completion and conflict compliance remain separate metrics.
6. Comparison output is byte-identical across runs.
7. Earlier phase regressions remain green.

## Phase 5A — C0 Constructability Verification

Phase 5A defines C0, the benchmark-only Core Heterogeneous Constructability Constraints. C0 requires complete connections, valid continuous endpoints and base-grid obstacle compliance, zero inter-system hard conflicts and clearance violations, plus a 1% monotonic drainage stair-step gravity abstraction. It is not building-code or fabrication compliance: sizing, supports, access, fittings, continuous slope geometry, and jurisdiction rules remain outside C0.

### Phase 5A gate

1. Both baselines are verified under the same C0 rules.
2. C0 preserves route and fixed-obstacle integrity checks.
3. Drainage slope and no-uphill checks are deterministic.
4. Incomplete routing cannot pass C0.
5. Conflict and clearance checks reuse the shared evaluator.
6. Verification output is byte-identical across runs.
7. Earlier phase regressions remain green.

## Phase 5B — Gravity-Aware Drainage Routing

Phase 5B adds a reusable drainage-only primitive using terminal-to-egress flow, no uphill neighbour, and the same C0 1% aggregate stair-step slope check. It preserves snapped XY anchors, permits controlled vertical candidates only within the existing two-voxel tolerance, and accepts arbitrary occupancy grids. Synthetic demand anchors permit this elevation flexibility; external IFC endpoints will need explicit metadata. This is not multi-system coordination.

### Phase 5B gate

1. All 48 current benchmark drainage requests are attempted.
2. Every successful route is C0-gravity compliant with no uphill step.
3. Endpoint XY and tolerance constraints are preserved.
4. Base occupancy is respected and nested standalone demand routes are invariant.
5. Gravity output and B0/B1/Phase 3C/Phase 4B/Phase 5A regressions are byte-identical.
6. The complete suite is green.

## Phase 6A — Conflict Diagnosis and Repair Candidate Selection

Phase 6A builds a diagnostic graph from immutable routed connections and the shared Phase 3C inter-system evaluator. Nodes are successful routed connections; edges are hard-envelope or clearance-only violations. It reports route conflict burdens, deterministic connected local conflict components, and a deterministic per-component greedy candidate edge cover. The cover is not an exact minimum vertex cover: candidates only ensure every current violation edge is incident to a candidate. No routes, occupancy, or engineering discipline priorities are changed. `SYSTEM_ORDER` is only the final deterministic tie-break, not a repair priority.

### Phase 6A gate

1. Existing conflict geometry remains the sole geometry truth source.
2. Components, burdens, and candidate selection are deterministic.
3. The selected candidates cover every current violation edge.
4. Candidate selection does not modify routes or occupancy.
5. No constructability or discipline priority is introduced.

## Phase 6B — B2 Conflict-Aware Single-Pass Selective Repair

B2 starts from B0 and uses the Phase 6A edge-cover candidates once. Non-candidates are frozen; all candidates are initially absent, then reinserted in the recorded component and selection order using ordinary A* and fixed B0 endpoints. Different-system frozen geometry uses the existing B1 reservation semantics. Failed attempts retain their original B0 geometry. B2 neither uses gravity routing nor discipline/constructability priority, and it does not iterate. Final geometry is evaluated with the shared evaluator; C0 is post-hoc reporting only.

## Phase 6C1 — P-CORE Hybrid Seed and Routing Primitives

P-CORE-SEED retains B0 geometry for non-drainage connections and replaces drainage with the existing Phase 5B gravity-aware routes. It standardizes those routes for the shared evaluator and C0 verifier, and provides a deterministic vertical-only endpoint-pair A* primitive for later coordination. This phase evaluates only the initial seed; it performs no repair rounds, diagnosis loop, or rollback.

## Phase 6C2A — Single Repair Round and Global Rollback

The reusable P-CORE round primitive diagnoses current geometry, removes current candidates from frozen geometry, performs discipline-specific one-pass repairs, evaluates the complete trial, and accepts it only for strict global conflict improvement. Otherwise the full pre-round case, including metadata, is returned unchanged. No iteration is performed here.
