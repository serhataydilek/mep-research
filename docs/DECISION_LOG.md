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
