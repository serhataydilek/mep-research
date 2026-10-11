# Implementation Roadmap

Each phase is gated; none promises a calendar date. Existing v1.0 code remains the Tier A regression baseline.

| Phase | Goal and scope | Non-goal | Acceptance gate / artifact | Key risk |
|---|---|---|---|---|
| 9A | Immutable demand model and provenanced ports. **Implemented:** typed endpoint/connection models and read-only synthetic benchmark adapter. | General routing and IFC extraction. | Focused endpoint fixtures; no inferred endpoint silently authoritative. | Sparse IFC metadata. |
| 9B | Topology graph for mains, branches, headers and legal sharing. **Partial:** immutable `TopologyNode` / `TopologyEdge` / `TopologyGraph` data model with explicit share ownership and synthetic fixtures. Topology *planning* and routing integration remain open. | Global geometry optimization; geometry-level clash certification from topology alone. | Branch/shared-overlap synthetic cases for the data model. | Topology explosion. |
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

**Partial (data model only).** `src/topology.py` defines immutable `TopologyNode`, `TopologyEdge`, and `TopologyGraph` with MAIN/BRANCH/HEADER/RISER roles, optional endpoint references, optional capacity/profile metadata, deterministic canonical serialization, and explicit same-system shared-segment ownership via `owner_edge_id`. Unauthorized duplicate primary segments and cross-system sharing are rejected at model construction. This is not topology planning, not `RoutingProblem` wiring, and not geometry-level clash detection: owned shares authorize future legal overlap semantics; they do not prove that independently generated routes collide or coincide in space. Remaining 9B work includes topology candidate generation from endpoints, routing/validator integration, and richer branch/header planning fixtures.
