# Research framing

## Working title

Generative Constructability-Aware Coordination of Heterogeneous MEP Systems in IFC-Based Building Models

## Research problem

Existing automatic MEP routing research often focuses on a single discipline, pipe-only or duct-only routing, fixed or sequential routing, clash avoidance without full constructability reasoning, or isolated optimization objectives. This project investigates coordinated generation of heterogeneous building MEP systems with discipline-specific engineering and constructability constraints.

The intended future systems include HVAC ductwork, gravity drainage, pressurized water piping, fire suppression, and electrical containment. They must not be treated as geometrically identical routing agents.

## Main research question

Can a generative, constructability-aware coordination framework produce feasible heterogeneous multi-discipline MEP layouts more reliably than independent and fixed-priority sequential routing approaches?

## Main hypothesis

Conflict-aware generative coordination with discipline-specific constructability constraints and iterative repair will achieve a higher feasible-layout success rate than independent and fixed-priority sequential routing, particularly under high spatial congestion.

## Primary objective and planned metrics

The primary objective is **feasible-layout success rate**, not simply shortest route length. A generated layout will count as feasible only when all required hard constraints are satisfied. Future hard constraints may include:

- Zero hard MEP-to-MEP clashes
- Zero invalid structural intersections
- All required terminals connected
- Minimum clearance satisfied
- Drainage slope satisfied
- Discipline-specific routing rules satisfied

These rules are future work and are not implemented in the current prototype.

Planned secondary metrics are:

- Total route length
- Number of bends/fittings
- Vertical movement
- Material/cost proxy
- Runtime
- Reroute count
- Constraint violations
- Terminal connection success rate

## Planned baselines

The following future comparison is intended for experimental evaluation:

| Label | Approach |
| --- | --- |
| B0 | Independent routing: systems routed independently. |
| B1 | Fixed-priority sequential routing: systems routed in a predefined physical-priority order. |
| B2 | Conflict-aware coordination baseline: routing decisions account for conflicts between systems. |
| P | Proposed generative constructability-aware coordination: heterogeneous discipline constraints, multiple candidate layouts, verification, diagnosis, and selective repair/rerouting. |

These are planned comparisons and hypotheses; no superiority claim has been demonstrated.

## Generative design and evaluation

The proposed framework should eventually generate multiple valid design alternatives rather than only one route. Future alternatives may trade off route length, installation complexity, maintenance accessibility, congestion, and material/cost proxy. Long-term evaluation may use Pareto-style comparison; optimization is not implemented yet.

Experiments are planned around parametric benchmark scenarios, varying floor dimensions, column spacing, shaft size and position, terminal density, routing-space height, obstacle density, service count, and congestion level. The goal is to assess robustness across many reproducible scenarios rather than one hand-picked case study.

Planned ablation studies include comparisons of the full proposed system against variants without iterative repair, constructability constraints, dynamic/conflict-aware coordination, or generative alternative search.

## Role of LLMs

LLMs are not the core routing engine. They may later translate natural-language engineering requirements into structured constraints, explain violations, and assist rule specification. Geometric routing and validation should remain deterministic and algorithmic.

## Out of scope for the current research phase

- Construction-site visual tracking
- LiDAR or point-cloud as-built monitoring
- Design-versus-as-built progress tracking

These are possible future extensions, not part of the current core research question.
