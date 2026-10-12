# Decision Log and Risk Register

## Decisions

| Decision | Status | Reason |
|---|---|---|
| Preserve research-v1.0 immutable baseline | Accepted | Reproducibility and provenance. |
| Hard clashes are feasibility constraints | Accepted | A production route cannot hide collisions in a score. |
| Reuse deterministic A* as low-level foundation | Accepted | Tested, explainable base; constraints must be expanded. |
| PBS-inspired coordinator | Proposed | Dynamic partial priorities fit conflict-driven coordination; evaluate against CBS. |
| CBS | Reference alternative | Useful correctness comparison for small components; may be costly. |
| Same-system sharing only through explicit topology | Accepted | Supports trunks while rejecting accidental overlap. |
| 0.1 m voxel as universal geometry | Rejected | Benchmark resolution is too coarse/general. |
| Independent validator | Accepted | Router cannot self-certify. |
| Viewer before validity | Rejected | Visualization follows validated geometry. |
| Single clearance source | Accepted | Removes current duplicated-constant risk. |
| Normalize endpoint and connection demand before production routing | Accepted | Phase 9A adds immutable world-coordinate models and a read-only benchmark adapter while preserving research-v1.0 inputs and results. |
| Separate topology data model from topology planning | Accepted | Phase 9B first delivers immutable MAIN/BRANCH/HEADER/RISER graphs with explicit share ownership; planning, RoutingProblem wiring, and geometry-level clash checks remain later gated work. |
| Unauthorized coincident topology segments are rejected without ownership | Accepted | Two primary edges on the same undirected node pair for one system are duplicate segment declarations; legal sharing requires `owner_edge_id` to a primary same-system owner. |
| Associate topology via separate demand layer, not by mutating RoutingProblem | Accepted | `TopologicalRoutingDemand` keeps Phase 9A connection identities authoritative and adds ordered edge-walk assignments without coupling route results or inventing geometry. |
| Benchmark topology adapter uses one dedicated branch edge per required connection | Accepted | Preserves legacy required-connection counts/IDs; avoids unverified shared-trunk implications; breakouts remain non-routed metadata. |
| Diagnose proposed topology-demand coverage before strict association | Accepted | A read-only report exposes missing, unknown, and duplicate authoritative IDs without repairing a candidate or mistaking metadata for demand. |
| Future topology and router milestones require explicit safety gates | Accepted | Demand completeness, legal same-system sharing, clearance policy, spatial semantics, independent validation, truthful statuses, early debugging visibility, and IFC round-trip/scalability evidence remain mandatory before production claims. |
| Initial topology planning is deterministic and abstract | Accepted | Direct fallback and same-system shared-main candidates preserve authoritative demand but do not claim spatial, fitting, clearance, or clash feasibility. |
| Spatial evidence precedes spatial selection | Accepted | Phase 9C.0 records only geometry derivable from the existing IFC and voxel pipeline; a selector based on invented corridors, junction positions or costs is rejected. |
| Reuse existing IFC tessellation and occupancy grids for evidence | Accepted | Identity-preserving AABBs feed `src.voxel.build_occupancy_grid` unchanged; grids are byte-identical to legacy output and referenced by digest. |
| Unsupported or unknown geometry is never free space | Accepted | Failed tessellation, non-metre units and unmodelled elements in the occupancy domain yield explicit diagnostics and a non-available status. |
| AABB evidence is approximate, labelled per element | Accepted | A volume match is labelled `VOLUME_MATCH_UNCERTAIN`, not exact: tessellation validity and numerical tolerance are not sufficient for solid equivalence. Rotated placements are flagged; no exact collision certification is claimed. |
| Existing IFC openings are not MEP penetrations | Accepted | Openings are recorded with host and fillings; approval is structurally impossible in the evidence model. |
| Candidate assessment is evidence-only | Accepted | Statuses describe evidence availability; feasibility and collision-freedom flags are fixed false; virtual junctions remain unresolved requirements. |
| Junction proposals are FREE cells of the junction system's own grid | Accepted | Phase 9C.1 reuses Phase 3A semantics and FREE-only router behaviour; no other system's grid is substituted and no second voxel engine is built. |
| `RESERVED_SHAFT` is not a usable junction cell | Accepted | Its only documented meaning is "shaft volume is reserved" and legacy routers never traverse it; it is excluded without being declared forbidden. |
| Bounded L1-shell search from an endpoint median seed | Accepted | Deterministic, explicitly bounded by candidate and exploration limits; the ordering is exploration order, not a route or fitting cost. |
| Empty bounded search is unresolved, never infeasible | Accepted | Statuses are `PROPOSALS_GENERATED` or `UNRESOLVED`; global infeasibility cannot be claimed from a bounded neighbourhood. |
| Authoritative endpoints are never snapped for placement | Accepted | Containing cells are read as-is; non-FREE endpoint cells are flagged, not moved. |
| Coincident proposals across junctions are flagged, not merged | Accepted | Independent groups, including different systems, never share a junction; joint selection needs clearance evaluation. |
| Single versioned clearance policy (`BENCHMARK_V1` first) | Accepted | Phase 9C.2's immutable read-only contract separates envelope, obstacle, inter-system, same-system and legal-shared rules. It characterizes existing values but does not yet replace a legacy consumer. |

## Risks

| Risk | Impact | Likelihood | Mitigation | Required evidence |
|---|---|---|---|---|
| Coordination state explosion | High | High | bounded PBS, component decomposition, LNS | budgeted dense fixtures |
| IFC connector gaps | High | High | provenance/explicit manual fallback | port-readiness reports |
| Rotated/curved geometry | High | Medium | exact/conservative geometry adapter | rotated/curved fixtures |
| Coarse-grid false infeasibility | High | Medium | corridor/multi-resolution refinement | coarse-vs-fine comparison |
| Topology explosion | High | Medium | candidate pruning and staged planning | branch/header benchmarks |
| Drainage feasibility | High | Medium | continuous slope and fitting model | segment-level slope tests |
| Structural penetration policy | High | High | policy boundary and approval state | host/opening audit records |
| Large-model memory | High | Medium | sparse/tiled space | project-scale memory measurement |
| Exact geometry performance | Medium | Medium | broad/narrow phases | profiling with validation parity |
| IFC round-trip fidelity | Medium | Medium | schema/relationship validation | import-export-inspect fixtures |
| Duplicated clearance sources (`clash.py` and `sequential.py` constants vs config `routing.clearance_m`) | High | Medium | single clearance policy consumed by occupancy, routing and validation | fixture with non-default clearance across all consumers |
| Name-based shaft identification | Medium | High | semantic shaft classification from real IFC data | real-model shaft audit |
| Plenum-only, config-bounded occupancy domain | High | Medium | IFC-derived vertical zones and multi-storey occupancy | multi-storey and ceiling-element fixtures |
| Snap convention mistaken for connector validity | Medium | Medium | treat snapping as benchmark convenience; validate connectors separately | connector-port fixtures |
| Junction proposal mistaken for a physical junction | High | Medium | structural false flags, validation ledger, unresolved requirements on every proposal | 9D routed geometry and 9H validation per selected proposal |
| Straight-run FREE cell too permissive for a fitting | Medium | High | fitting-envelope requirement recorded per proposal | fitting-geometry fixtures (9G) |
| Same-system independent-run clearance undefined | Medium | High | explicit `UNDEFINED` in clearance policy, treated as unresolved | same-system parallel-run fixtures |
