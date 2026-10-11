# Implementation Roadmap

Each phase is gated; none promises a calendar date. Existing v1.0 code remains the Tier A regression baseline.

| Phase | Goal and scope | Non-goal | Acceptance gate / artifact | Key risk |
|---|---|---|---|---|
| 9A | Immutable demand model and provenanced ports. **Implemented:** typed endpoint/connection models and read-only synthetic benchmark adapter. | General routing and IFC extraction. | Focused endpoint fixtures; no inferred endpoint silently authoritative. | Sparse IFC metadata. |
| 9B | Topology graph for mains, branches, headers and legal sharing. **Partial:** immutable topology data model; `TopologicalRoutingDemand` associates `RoutingProblem` connection IDs with topology edge walks; benchmark star adapter preserves legacy required-connection identities. Topology *planning* remains open. | Global geometry optimization; geometry-level clash certification from topology alone; automated trunk/header synthesis as authoritative geometry. | Branch/shared-overlap synthetic cases; exact connection-ID coverage between demand and assignments. | Topology explosion. |
| 9C | Semantic spatial classification plus opening/penetration records. | Structural approval automation. | Existing-opening and forbidden-wall cases. | Geometry policy variance. |
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

Still out of scope for completed 9B: automated topology planning (mains/headers/candidate generation), semantic penetrations (9C), constrained A* (9D), PBS coordination (9E), continuous geometry (9G), and IFC export (9J). Owned topology shares authorize future legal-overlap semantics; they do not prove geometric coincidence or clash freedom.
