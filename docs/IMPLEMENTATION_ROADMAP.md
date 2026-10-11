# Implementation Roadmap

Each phase is gated; none promises a calendar date. Existing v1.0 code remains the Tier A regression baseline.

| Phase | Goal and scope | Non-goal | Acceptance gate / artifact | Key risk |
|---|---|---|---|---|
| 9A | Immutable demand model and provenanced ports. **Implemented:** typed endpoint/connection models and read-only synthetic benchmark adapter. | General routing and IFC extraction. | Focused endpoint fixtures; no inferred endpoint silently authoritative. | Sparse IFC metadata. |
| 9B | Topology graph for mains, branches, headers and legal sharing. **Partial:** immutable topology data model; demand association and read-only coverage diagnostics; deterministic abstract direct/shared-main candidates for compatible authoritative upstream groups. Geometry-independent candidate planning, header semantics, and routing integration remain open. | Global geometry optimization; geometry-level clash certification; automated trunk/header synthesis as authoritative geometry. | Branch/shared-overlap synthetic cases; exact connection-ID coverage and source-to-terminal topology walks. | Topology explosion. |
| 9C | Semantic spatial classification plus opening/penetration records. **In progress (9C.0):** immutable spatial evidence from existing synthetic IFC geometry and read-only topology-candidate evidence assessment. Junction placement, penetration policy and spatially informed selection remain open. | Structural approval automation; route feasibility claims. | Existing-opening and forbidden-wall cases. | Geometry policy variance. |
| 9D | Zero-clash constrained low-level router. | Global multi-route optimization. | Every returned route passes local hard checks. | Over-conservative state space. |
| 9E | PBS-inspired global coordination. | Claim of optimality. | Conflict cases end valid or structured unresolved. | Search-state explosion. |
| 9F | Neighborhood rip-up/reroute recovery. | Unbounded search. | Deadlock/reroute fixtures; deterministic budgets. | Local minima. |
| 9G | Continuous centerlines, profiles, fittings and slopes. | IFC serialization. | Exact/conservative geometry validation fixtures. | Fitting complexity. |
| 9H | Independent production validator. | Router-owned certification. | Deliberately invalid candidates rejected with location/reason. | Validator/router drift. |
| 9I | Real IFC routing integration and readiness adapters. | Universal IFC support claim. | Stage-specific public/real-model reports. | Placement/connector variance. |
| 9J | IFC output with ports, fittings, openings and relationships. | Fabrication release. | Round-trip/schema/relationship checks. | Round-trip fidelity. |
| 9K | Interactive 3D inspector. | Routing algorithm changes. | Validated layer/route inspection. | Viewer scale. |
| 9L | External benchmark and controlled real-project validation. | General code compliance. | Reproducible dataset and project evidence. | Data licensing and project diversity. |

Dependencies are sequential where a later phase consumes authoritative artifacts from an earlier one: 9A→9B→9C→9D→9E/9F→9G→9H→9I→9J→9K/9L. A risk register and decision statuses are maintained in [DECISION_LOG.md](DECISION_LOG.md).

## Phase 9A implementation boundary

The delivered model stores finite world coordinates, optional normalized orientation, flow state, shape-consistent dimensions, provenance, source identifiers, confidence, and immutable metadata. `ConnectionRequest` is independent of candidate route output, and `RoutingProblem` validates endpoint references and system consistency. Existing benchmark anchors and connection dictionaries remain the immutable regression input; an adapter produces normalized models without altering their generated values. IFC port extraction, routing integration, topology, and production-validator changes remain intentionally unimplemented.

## Phase 9B implementation boundary

**Partial — not complete.** Delivered so far:

1. **Topology data model** (`src/topology.py`): immutable `TopologyNode`, `TopologyEdge`, and `TopologyGraph` with MAIN/BRANCH/HEADER/RISER roles, optional endpoint references, optional capacity/profile metadata, deterministic canonical serialization, and explicit same-system shared-segment ownership via `owner_edge_id`. Unauthorized duplicate primary segments and cross-system sharing are rejected at model construction.
2. **Demand association** (`src/topology_demand.py`): `ConnectionTopologyAssignment` and `TopologicalRoutingDemand` bind Phase 9A `RoutingProblem` / `ConnectionRequest` identities to ordered topology edge walks. Validation covers exact required-connection coverage (no missing/unknown/duplicate IDs), endpoint-node bindings, system consistency, and contiguous undirected walks. Shared edges remain usable when already declared in the graph. Required connections stay independent of route results.
3. **Benchmark star adapter**: `benchmark_case_to_topological_demand` / `star_topology_for_routing_problem` map each legacy required connection to a dedicated BRANCH edge. Fixed source-to-egress breakouts stay metadata only. No artificial shared trunks are invented.
4. **Preparation fixtures and diagnostics**: controlled main-with-two-branches fixtures demonstrate multiple endpoint-to-endpoint edge walks and legal shared trunk use. `inspect_topology_demand_coverage` reports authoritative required, assigned, missing, unknown, and duplicate assignment IDs before strict association; it does not invent connections, repair invalid proposals, or validate connectivity.
5. **Abstract candidate planner** (`src/topology_planner.py`): always emits a direct fallback and emits bounded shared-main alternatives only for same-system connections with the same authoritative start endpoint and distinct terminals. Virtual junctions are logical only; candidates do not carry geometry or physical-feasibility claims. The model has no reliable semantic distinction for a HEADER candidate, so no header is invented. Parallel same-system connections on one endpoint pair are rejected explicitly because they would need an undeclared segment-sharing policy.

Still out of scope for completed 9B: header synthesis, spatially informed candidate selection, semantic penetrations (9C), constrained A* (9D), PBS coordination (9E), continuous geometry (9G), and IFC export (9J). Owned topology shares authorize future legal-overlap semantics; they do not prove geometric coincidence or clash freedom.

## Phase 9C.0 spatial evidence boundary

**In progress — Phase 9C is not complete.** `src/spatial_evidence.py` derives immutable, deterministically serialized evidence from existing geometry and reuses the existing IFC generator, IfcOpenShell tessellation and `src.voxel.build_occupancy_grid`; it adds no parser or voxel engine.

**Available evidence.** Source provenance (scenario, config SHA-256, IFC schema, `IfcProject` GlobalId, IfcOpenShell version) and one coordinate frame: IfcOpenShell world-coordinate tessellation in metres with the `IfcLocalPlacement` chain resolved. Each product with geometry is recorded by GlobalId with its class, policy classification (perimeter wall, shaft wall, column, slab, shaft space, other space, unclassified), occupancy role and world AABB. Existing openings carry their voided host and fillings. The single reserved shaft comes from the existing `Service Shaft 0` naming convention. Five service grids are referenced by spec, margins, state counts and a SHA-256 of an immutable cell snapshot; they are byte-identical to the legacy grids.

**Correctness checks.** Each element's local tessellation, transformed by its resolved placement matrix, must match its world tessellation; a mismatch is `INVALID`. Placement translations are scaled from project units to metres before that comparison, so a non-metre model is reported `UNSUPPORTED`, not falsely `INVALID`. Grid references reject cell values outside the Phase 3A state vocabulary, a scenario other than the evidence scenario, or a domain differing from the evidence grid domain. Endpoint assessment reads the immutable snapshots through a zero-copy legacy view rather than copying each grid. The AABB is labelled `EXACT` only when the mesh volume equals the box volume, otherwise `OVER_APPROXIMATION`. Rotated placements are flagged. Non-metre project units, failed or degenerate tessellation, and unmodelled geometry (for example a beam) inside the occupancy domain produce explicit `UNSUPPORTED` diagnostics; they are never treated as free space. A missing shaft reservation, missing policy obstacles, or a grid domain extending beyond the model geometry is `INSUFFICIENT`. The grid cell count is bounded by a safety limit (25 M cells per service).

**What remains approximate.** All occupancy is broad-phase AABB evidence. Rotated or curved obstacles are over-approximated, which is conservative for blocking but can cause false infeasibility. The occupancy domain is the storey-0 plenum taken from config ceiling height; no IFC ceiling bounds it. Upper storeys are not in the occupancy domain. Slabs and openings lie outside that domain and are recorded but not used. An existing opening is never an approved MEP penetration. Shaft identity relies on a synthetic naming convention.

**Candidate assessment.** `assess_topology_candidate` requires the caller to declare its endpoint frame; a mismatch is `INVALID_SPATIAL_INPUT` and positions are not reinterpreted. For each bound endpoint it reports provenance authority, whether the endpoint lies in the domain, the state of its containing cell, and whether a free cell exists under the existing two-voxel benchmark snap convention. Every topology edge is reported as `EDGE_ROUTE_GEOMETRY_UNRESOLVED`. Every SHARED_MAIN virtual junction is `JUNCTION_POSITION_UNRESOLVED`, and its main edge is `SHARED_TRUNK_FIT_UNEVALUATED`. Statuses (`EVIDENCE_AVAILABLE`, `INSUFFICIENT_EVIDENCE`, `UNSUPPORTED_GEOMETRY`, `INVALID_SPATIAL_INPUT`) describe evidence only. `physical_feasibility_evaluated` and `collision_free_claimed` are structurally always false. No route length, clash count, clearance or geometric cost is computed. A free endpoint cell does not make a DIRECT candidate collision-free.

**Needed for shared-trunk placement.** Junction placement needs candidate junction coordinates generated inside free, same-system-compatible space. It also needs the trunk profile, the downstream branch demands, and fitting take-off geometry with minimum straight lengths. Drainage additionally needs slope and invert constraints. The trunk must fit at the combined profile with clearance to other systems. Corridor/zone semantics would make this efficient; the IFC does not currently provide them.

**Feeding the future selector and constrained A\*.** A selector may rank only candidates whose assessment is `EVIDENCE_AVAILABLE` with grid-compatible endpoints. Ranking must use geometry produced by a later junction-placement and routing search, never placeholder costs. Constrained A* (9D) will consume the same frame, the referenced service grids (verified by digest) and the unresolved-requirement list. Each requirement must be resolved by an actual path or a structured unresolved status.

**Required independent validation.** Routes and junctions produced later must be re-checked by an independent validator (9H) against exact or conservatively certified geometry, not against the router's own grid. AABB occupancy evidence cannot certify zero clashes.

**Existing clearance inconsistencies (unchanged here).** `src/clash.py` hardcodes `CLEARANCE_M = 0.05` and applies half of it to each route. Occupancy reads `routing.clearance_m` per scenario and inflates obstacles by half-extent plus the full clearance. The two agree today only because every scenario uses 0.05 m. `src/ifc_validation.py` rasterizes uninflated AABBs with an any-overlap rule, while the routing grid uses voxel-centre-in-inflated-AABB, so its counts are not routing occupancy. A single scalar clearance applies to every system pair. Rectangular services use `max(width, height)/2` as an orientation-agnostic planar half extent.

**Early 3D debugging viewer (planned, not implemented).** Before constrained routing, a lightweight read-only viewer should render evidence AABBs by classification and grid slices by cell state. It should also show endpoints with containing and snapped cells, and diagnostics by GlobalId. This is for debugging only and does not replace 9H validation or the 9K inspector.

## Future safety gates

The following are acceptance gates for later work, not delivered functionality:

- Verify authoritative demand completeness and source-to-terminal connectivity independently of generated route outputs.
- Handle same-system collisions by distinguishing explicitly legal shared physical segments from illegal overlaps.
- Use one authoritative clearance policy consistently for routing, reservation, and validation.
- Define geometry semantics for walls, shafts, openings, and permitted penetrations before treating them as routable.
- Begin incremental independent validation during constrained-routing development; add full continuous-geometry validation later.
- Report `ROUTED`, `NOT_FOUND`, `TIMEOUT`, `INVALID_INPUT`, and `PROVEN_INFEASIBLE` distinctly; only claim infeasibility with justification.
- Provide an early 3D debugging viewer before the final production visualization milestone.
- Normalize IFC coordinates and units; require continuous fitting geometry, round-trip export verification, and scalability benchmarks.
