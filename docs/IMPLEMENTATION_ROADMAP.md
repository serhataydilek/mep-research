# Implementation Roadmap

Each phase is gated; none promises a calendar date. Existing v1.0 code remains the Tier A regression baseline.

| Phase | Goal and scope | Non-goal | Acceptance gate / artifact | Key risk |
|---|---|---|---|---|
| 9A | Immutable demand model and provenanced ports. **Implemented:** typed endpoint/connection models and read-only synthetic benchmark adapter. | General routing and IFC extraction. | Focused endpoint fixtures; no inferred endpoint silently authoritative. | Sparse IFC metadata. |
| 9B | Topology graph for mains, branches, headers and legal sharing. **Partial:** immutable topology data model; demand association and read-only coverage diagnostics; deterministic abstract direct/shared-main candidates for compatible authoritative upstream groups. Geometry-independent candidate planning, header semantics, and routing integration remain open. | Global geometry optimization; geometry-level clash certification; automated trunk/header synthesis as authoritative geometry. | Branch/shared-overlap synthetic cases; exact connection-ID coverage and source-to-terminal topology walks. | Topology explosion. |
| 9C | Semantic spatial classification plus opening/penetration records. **In progress:** 9C.0 immutable spatial evidence; 9C.1 bounded FREE-cell junction proposals; 9C.2 `BENCHMARK_V1`; 9C.3 parity-gated clearance adapters and read-only edge-routing readiness. Penetration policy and spatial selection remain open. | Structural approval automation; route feasibility claims. | Existing-opening and forbidden-wall cases. | Geometry policy variance. |
| 9D | **In progress (9D.0):** bounded deterministic routing of one topology edge through an existing service grid, returning ordered cells and voxel-centre geometry. | Global multi-route optimization, fittings and zero-clash claims. | Per-edge grid continuity and endpoint checks. | Over-conservative state space. |
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

**Existing clearance inconsistencies (unchanged here; full inventory in the 9C.2 design below).** `src/clash.py` and `src/sequential.py` each hardcode `CLEARANCE_M = 0.05`; `clash.py` applies half of it to each route. Occupancy reads `routing.clearance_m` per scenario and inflates obstacles by half-extent plus the full clearance. The two agree today only because every scenario uses 0.05 m. `src/ifc_validation.py` rasterizes uninflated AABBs with an any-overlap rule, while the routing grid uses voxel-centre-in-inflated-AABB, so its counts are not routing occupancy. A single scalar clearance applies to every system pair. Rectangular services use `max(width, height)/2` as an orientation-agnostic planar half extent.

**Early 3D debugging viewer (planned, not implemented).** Before constrained routing, a lightweight read-only viewer should render evidence AABBs by classification and grid slices by cell state. It should also show endpoints with containing and snapped cells, and diagnostics by GlobalId. This is for debugging only and does not replace 9H validation or the 9K inspector.

## Phase 9C.1 junction candidate placement boundary

**Implemented, uncommitted for review.** `src/junction_placement.py` proposes grid cells where a logical SHARED_MAIN junction could later be evaluated. It consumes `TopologyCandidate`, `SpatialEvidence` and its `OccupancyGridReference` snapshots; it builds no grid and modifies no topology, demand or endpoint.

**Group identification.** Each virtual node (`endpoint_id is None`) must be a JUNCTION with exactly one MAIN edge to an authoritative start endpoint, two or more BRANCH edges to distinct authoritative terminals, no other incident or share-owned edges, and two or more assigned connections through the MAIN edge that all start at that endpoint. The start endpoint is the planner's grouping key whatever its flow direction (benchmark groups start at egress anchors with `UNKNOWN` flow). Anything else is `UNSUPPORTED_JUNCTION_STRUCTURE`. DIRECT candidates report `NO_VIRTUAL_JUNCTIONS`.

**Gates before searching.** Evidence status must be `EVIDENCE_AVAILABLE`; the caller-declared endpoint frame must equal the evidence frame; the junction's own system grid must exist (no other grid is substituted); every group endpoint must have authoritative provenance and lie inside the grid. Each failure is a structured `UNRESOLVED` reason with a diagnostic; all failing gates are reported, and the reason follows a fixed precedence. An endpoint whose containing cell is not FREE is used unchanged and flagged `ENDPOINT_CELL_NOT_FREE`; nothing is snapped.

**Usable cells.** Only `FREE` cells of the junction system's grid are usable. That is the existing Phase 3A meaning (nominal straight-run envelope plus `routing.clearance_m` clears fixed-obstacle AABBs and the plenum boundary) and matches the FREE-only legacy routers. `RESERVED_SHAFT` is documented only as "shaft volume is reserved" and has no routable semantics, so it is not usable; it is not declared forbidden either. Cells containing a group endpoint are excluded because a junction there would collapse a topology edge. Because grids are per system, a larger envelope (HVAC) yields fewer usable cells than a smaller one (water) around the same obstacle.

**Search.** The region is the index box of the group's endpoint containing cells, expanded by `region_margin_cells` and clipped to the grid domain; the domain is inside the model bounds whenever evidence is `EVIDENCE_AVAILABLE`. The seed is the component-wise lower median of those cells, which minimises summed L1 index distance to them. Cells are visited in L1 shells around the seed, ordered by `(distance, k, j, i)`, which is an exploration order and not a cost. Search stops at `max_candidates_per_junction` (`CANDIDATE_LIMIT_REACHED`), at `max_explored_cells` (`EXPLORATION_LIMIT_REACHED`) or when the region is exhausted (`REGION_EXHAUSTED`). Each proposal's centre must map back to its own cell index or it is rejected with an `INVALID` diagnostic.

**Results.** `JunctionPlacementProposal` records candidate and junction IDs, system, rank, grid and linear index, world centre, the grid evidence reference (frame, source model, scenario, system, cells SHA-256, voxel size, clearance and margins), seed L1 distance, generation reason and unresolved physical requirements. The requirements are fitting geometry, shared-trunk profile, inter-system clearance, collision-free certification, route geometry for the main and each branch, and drainage slope and invert. Physical-position, fitting, reachability and collision-free flags are structurally false. `ValidationLedger` separates structural topology, spatial evidence and candidate placement (establishable here) from fitting feasibility, reachable geometry and collision-free certification (always `NOT_EVALUATED`). Statuses are `PROPOSALS_GENERATED` or `UNRESOLVED`; there is no `ROUTED` or `INFEASIBLE`, and `globally_infeasible_claimed` cannot be set. A region with no FREE cell says explicitly that this is not evidence that no placement or route exists. Distinct junctions proposing the same cell centre are flagged (`COINCIDENT_PROPOSALS_ACROSS_JUNCTIONS`, or `CROSS_SYSTEM_COINCIDENT_PROPOSALS`) and never merged.

**Complexity.** With region `R` cells, candidate limit `C` and exploration limit `E`, cell inspections are at most `min(R, E)`. Shell enumeration only iterates in-region `(k, j)` pairs, so each shell costs `O(Δk·Δj)` plus sorting, over at most `Δi+Δj+Δk` shells. Memory is `O(C)` proposals plus one shell; grids are read in place and never copied. On the first benchmark case the four shared-main groups stop at `C = 8` after 8 to 31 inspected cells, from regions of 98 k to 167 k cells.

**Not established.** Proposals are not physical junction positions. Fitting envelopes, take-offs, combined trunk sizing, inter-system clearance, reachability of the main or branches, gravity constraints and collision freedom all remain unevaluated. Selecting a proposal, and comparing DIRECT with SHARED_MAIN candidates, requires routed geometry from 9D and independent validation from 9H.

**Selection limitation.** The candidate cap preserves exploration order, so a bounded result can contain many nearby cells rather than spatially diverse alternatives. Future selection must add diversity-aware ranking after clearance and fitting evidence exists; this milestone intentionally does not pretend proximity is physical quality.

## Phase 9C.2 clearance policy contract

**Current inventory (benchmark behaviour, preserved).**

| Consumer | Source of value | Rule |
|---|---|---|
| `src/voxel.py` occupancy | config `routing.clearance_m` | obstacle AABB inflated by service half extent + full clearance (planar and vertical); plenum boundary uses vertical half extent + clearance; voxel-centre-in-inflated-AABB raster |
| `src/demand.py` margins, preflight and anchors | config `routing.clearance_m` | same `service_routing_margins` formula |
| `src/sequential.py` B1 reservation | hardcoded `CLEARANCE_M = 0.05` | prior half extent + target half extent + full 0.05 between systems; same-system siblings never reserved |
| `src/clash.py` evaluation | hardcoded `CLEARANCE_M = 0.05` | each route's swept AABB grown by 0.025, positive overlap is a violation (0.05 pairwise gap); same-system pairs only count shared centreline cells |
| `src/ifc_validation.py` | none | uninflated AABBs, any-overlap raster; validation counts only, not a clearance consumer |
| `src/route_geometry.py` / `src/demand.py` | duplicated half-extent formulas | rectangular planar half extent is orientation-agnostic `max(w, h)/2` |

All scenarios use 0.05 m, so the three sources agree only by coincidence. A non-default `routing.clearance_m` would change obstacle occupancy but not B1 reservation or clash evaluation. Same-system independent-run clearance is not defined anywhere.

**Implemented contract.** `src/clearance_policy.py` provides one immutable, versioned `ClearancePolicy`, identified by a SHA-256 of canonical JSON. It is read-only: no legacy routing, reservation, clash, junction or IFC-validation consumer has been switched. It supplies adapters for Phase 3A obstacle margins and B1 pairwise reservation margins, so migration can be parity-gated rather than hidden behind defaults:

1. *Service envelope*: per system shape and dimensions from `config/mep_systems.json`, with an explicit half-extent rule (`ORIENTATION_AGNOSTIC_MAX` reproduces today's rule; an oriented rule may be added later). Envelope is geometry, not clearance.
2. *Architectural obstacle clearance*: `obstacle_clearance(system, obstacle_class)` in metres for each evidence classification and for the plenum boundary.
3. *Inter-system clearance*: a symmetric `inter_system_clearance(a, b)` for every unordered pair of distinct systems, defined as the envelope-to-envelope gap. Consumers derive their own split (for example half per side) from it.
4. *Same-system independent-run clearance*: `same_system_clearance(system)` for two runs that are not one owned segment. It may be `UNDEFINED`; consumers must treat `UNDEFINED` as unresolved, never as zero.
5. *Legal shared physical segments*: clearance does not apply between assignments traversing the same owned topology edge (`owner_edge_id`, or one SHARED_MAIN edge walked by several connections), because they are one physical segment. Everything else about that segment uses rules 2 to 4. Sharing remains authorised only by topology, never inferred from geometry.

**Benchmark preservation.** The first policy, `BENCHMARK_V1`, encodes exactly today's behaviour: obstacle clearance from `routing.clearance_m` for every class and the boundary, inter-system clearance 0.05 m for every pair (the existing constants), same-system clearance `UNDEFINED`. The compatibility report names every remaining legacy consumer and reports a non-default config clearance as a divergence rather than silently choosing a value. Characterization covers all 12 synthetic benchmark case definitions, the configured 0.05 m values, service-specific circular/rectangular envelopes, pair symmetry and read-only adapters. It does not claim byte-identical B0/B1/clash reruns through the policy because the consumers intentionally still use their legacy sources. No engineering or regulatory values are introduced; any other policy must come from an explicit, cited input.

**Consumers in later milestones.** First parity-gate voxel obstacle inflation against `legacy_obstacle_margin_m`; then B1 reservation against `legacy_reservation_margin_m`; then make clash use the inter-system pair lookup and require an explicit result for independent same-system runs. Junction proposals can then add the policy digest to their grid evidence reference. Constrained A* (9D) and the 9H validator must use the same policy digest, or report a mismatch. IFC validation remains a separate uninflated, any-overlap rasterization until its semantics are deliberately aligned.

## Phase 9C.3 clearance integration and routing readiness

**Implemented, uncommitted for review.** Voxel occupancy obtains its existing service margin through `BENCHMARK_V1`; B1 reservation and clash expansion obtain their preserved pairwise 0.05 m value from the policy's legacy-provenanced inter-system rule. This retains the deliberately different configured obstacle clearance when a scenario differs from 0.05 m. No route, reservation ordering, conflict classification or archived output is changed.

`OccupancyGridReference` and junction proposal evidence carry the policy SHA-256 when evidence is produced through the current extractor. `src/routing_readiness.py` is a small, immutable preflight joining authoritative candidate demand, spatial evidence, optional junction placement and policy. It reports structured blockers for frames, grids, endpoint containment, missing proposals and digest mismatches. A true virtual junction is only a topology node with no endpoint identity. Readiness may permit a bounded edge-search attempt, but always leaves fitting, shared-trunk geometry, physical feasibility and collision freedom unresolved.

## Phase 9D.0 first physical edge routing — implementation specification

1. Accept one topology edge, its authoritative endpoint or selected virtual-junction coordinate, matching `SpatialEvidence` grid and matching policy digest.
2. Reuse `src.routing.astar_3d` as the deterministic bounded grid-search core; add a narrow edge wrapper rather than replacing B0/B1/B2/P-CORE.
3. Construct the service grid with the same policy digest, validate endpoint containment without changing endpoint identities, and emit ordered cell indices plus voxel-centre world coordinates.
4. Return only `ROUTED`, `NOT_FOUND`, `TIMEOUT`, or structured invalid/unsupported input statuses. Search limits are `NOT_FOUND`/`TIMEOUT`, never proven infeasibility.
5. Keep the returned centreline distinct from fitting, tee, sleeve, continuous-envelope and independent collision certification. Preserve sufficient edge, grid, policy and evidence identifiers for 9H validation.

**Implemented, uncommitted for review.** `src/physical_edge_routing.py` reuses the existing Manhattan heuristic and six-neighbour occupancy convention in a bounded adapter. It resolves endpoint-backed nodes without snapping and virtual nodes only from caller-selected grid-derived proposals. `ROUTED` paths include topology/grid/policy identities, ordered free cells, matching voxel centres, length and expansion count. A benchmark `s01` DIRECT drainage edge routed in 197 cells; a selected `SHARED_MAIN` edge routed in 65 cells (6.4 m, 221 expansions). These are grid-centreline demonstrations only.

**9D.1, uncommitted.** `src/physical_network_routing.py` composes a selected proposal's MAIN and every BRANCH exactly once for one controlled SHARED_MAIN group. Per-connection paths reference the MAIN then their branch; completeness requires every group demand path, never a partial result. A viewer can consume its canonical JSON centreline payload later, but no viewer or continuous-geometry certification is implemented.

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
