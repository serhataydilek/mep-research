# Validation Strategy

## Tiers

| Tier | Purpose | Required evidence |
|---|---|---|
| A: regression | Preserve v1.0 4×3/292 benchmark and deterministic totals. | Existing tests, byte-stable generated results, reference totals. |
| B: zero-clash synthetic | Isolate crossing, impossible corridor, wall/opening, legal/illegal same-system sharing, deadlock, reroute, congestion, slope and topology branch. | Small fixtures with expected valid/partial/invalid status. |
| C: public benchmark | Evaluate the licensed plant-room dataset after adapter review. | Provenance, version/license, adapter, reproducible metrics. |
| D: real IFC | Measure readiness separately from routing success. | Parse/geometry/ports/topology/router/validation statuses. |

## Independent validator

The validator receives immutable demands/topology, candidate routes, spatial model, policies, and exact geometry. It verifies every required connection, endpoint provenance, route continuity, bounds/storey, architecture/structure, authorized penetrations, cross-system collisions/clearance, same-system topology ownership, continuous slope, bend/straight-run rules, and modeled access zones.

Statuses are `VALID`, `INVALID`, or `PARTIAL`. `PARTIAL` reports valid accepted routes plus unresolved requests. Reasons include `NO_GEOMETRIC_CORRIDOR`, `ENDPOINT_BLOCKED`, `INSUFFICIENT_CLEARANCE`, `NO_VALID_PENETRATION`, `SLOPE_INFEASIBLE`, `TOPOLOGY_INFEASIBLE`, `SEARCH_LIMIT_REACHED`, `PROVEN_INFEASIBLE`, and `NOT_FOUND_WITHIN_SEARCH_LIMIT` as appropriate.

Production primary acceptance metrics are all zero: hard conflicts, clearance violations, forbidden structural intersections, invalid penetrations, and hard system-rule violations. Always also report requested, valid completed, failed/unresolved, and reason counts. Secondary metrics are length, bends/fittings, vertical travel, penetrations, expanded nodes, runtime, memory, and reroute count.
