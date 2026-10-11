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
