# Algorithm Design

## Feasibility contract

Production feasibility is lexicographic: hard constraints first, then coverage, then quality. An accepted route set has zero hard clashes, zero clearance violations, no forbidden structural intersection, valid endpoints/penetrations, and system constraints. Report `PROVEN_INFEASIBLE` only when the method establishes it; otherwise use `NOT_FOUND_WITHIN_SEARCH_LIMIT`.

```text
parse IFC → normalize units/placements → extract provenanced ports
→ generate topology candidates → classify spatial model/openings
→ route topology edges with constrained A* → detect conflicts
→ PBS-style coordination → neighborhood rip-up/reroute if needed
→ generate continuous geometry/fittings → independently validate
→ VALID, INVALID, or PARTIAL with immutable-demand diagnostics
```

## Constrained A*

```text
CONSTRAINED_ASTAR(request, spatial_model, reservations, constraints):
  initial ← valid endpoint state; goal ← endpoint acceptance state
  push initial using admissible distance lower bound
  while open is not empty and budget remains:
    state ← deterministic best state
    if state satisfies goal and all local constraints: return path
    for deterministic successor in legal moves/fittings:
      reject if forbidden geometry, reservation, clearance, slope, bend, or access rule fails
      relax cost = length + bend/vertical/soft-cost terms
  return NOT_FOUND_WITHIN_SEARCH_LIMIT
```

At coarse level, nodes are corridor regions; at fine level they are local cells/continuous samples. Complexity depends on spatial representation, state dimensions, geometry checks, and constraints; no useful global Big-O claim exists beyond graph-search bounds for a fixed finite state graph.

## PBS-inspired coordination

```text
PBS(requests):
  root ← routes under no partial priorities
  queue ← {root}
  while queue and budget remain:
    node ← best deterministic feasible-prefix node
    conflict ← earliest/severest hard conflict in node
    if none: return node.routes
    for order in [a before b, b before a]:
      child ← node + order
      replan only the lower-priority affected closure against ancestors
      if every replan succeeds: enqueue child
  return NOT_FOUND_WITHIN_SEARCH_LIMIT
```

Conflict selection is deterministic and severity-first: structural/penetration violations, hard geometry conflicts, clearance conflicts, then stable request IDs. PBS explores partial rather than one fixed total order. It is preferred for scalable coordination experiments, but it is not assumed complete or optimal for this enriched MEP problem. CBS is retained as a reference for small conflict components: it branches on conflict-specific route constraints and replans one constrained route, at potentially exponential high-level cost.

## Recovery and validation

```text
RECOVER(seed): select a connected conflict/deadlock neighborhood
remove its accepted routes; freeze every external accepted route
try alternate topology/priority/corridor/penetration choices
accept only if hard-feasible coverage improves; otherwise restore seed

VALIDATE(candidate, immutable_demands):
for every required topology edge: verify exactly one valid representation
verify endpoints, continuity, geometry, profile, clearance, structure, openings,
slope, bends, access, bounds and storey; then return VALID or structured reasons
```

Solution comparison: minimize unresolved hard violations; maximize valid completed immutable requests; then minimize length, fittings/bends, vertical travel, penetration count, runtime/resources. Do not trade a hard violation for a shorter route.
