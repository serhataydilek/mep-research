# Phase 10R-B3.3: Viewer and Federation Gate

## Chunked asset execution

The completed 119,022,316-byte, 6,666-mesh source fixture was converted without IFC retessellation into `viewer/medical_dental_clinic_progressive.json` plus 69 deterministic JSON geometry chunks (100 meshes maximum per chunk). Conversion took 3.716 seconds. The manifest retains source hashes, GlobalIds, original world coordinates, exact coverage, and chunk SHA-256 values. The viewer recognizes this manifest and sequentially reports chunk progress before rendering; chunks are never silently omitted.

The viewer must be launched with `python -m http.server 8000 -d viewer` and opened with `?fixture=medical_dental_clinic_progressive.json`. It preserves element picking, discipline toggles, fit, orbit, section clipping, and source metadata. The chunked layout removes a monolithic fetch but still assembles all chunks in browser memory; true on-toggle/lazy disposal remains necessary for production-scale browser safety.

## Quantitative federation assessment

All values below are extracted unchanged from the official IFC world coordinates after each file's declared unit conversion:

| Discipline | First-floor meshes | World bounds (x, y, z metres) |
|---|---:|---|
| Architecture | 128 | x -50.742–0.113; y -0.113–55.910; z 0.000–2.000 |
| Structure | 463 | x -52.016–2.196; y -19.096–56.256; z -1.370–7.261 |
| HVAC | 1,196 | x -0.0516–-0.0002; y 0.0006–0.0558; z -0.00015–0.00448 |
| Plumbing | 3,714 | x -0.0538–0.0004; y -0.0070–0.0580; z -0.00068–0.00504 |
| Electrical | 1,165 | x -52.200–0.167; y -0.167–56.307; z 0.000–4.468 |

Architecture/structure/electrical are mutually plausible in a tens-of-metres frame. HVAC and plumbing are roughly three orders of magnitude smaller after their source-declared conversion. No per-discipline correction was applied. This is stronger than a bounding-box-overlap observation: **federation is UNVERIFIED and quantitatively inconsistent for HVAC/plumbing**. Missing evidence is authoritative unit/placement-export documentation or a sanctioned common-reference transform; visual alignment cannot supply it.

## Routing region gate

No bounded real routing region is authorized. Architecture/structure/electrical could supply obstacles in approximately `x=-50.7..0.2`, `y=-0.2..56.3`, but HVAC/plumbing cannot be placed in that same region under their current source coordinates. Although explicit HVAC/plumbing port relationships exist, no compatible federated native request, clearance policy, opening semantics, or safe voxel-memory assessment exists. Existing geometry must remain retained; exclusion would itself require a documented replacement scope.

## Decision

1. **Inspectable building:** partially—complete chunked source assets exist, but browser assembly is still eager rather than demand-disposed.
2. **Federation verified:** **No.** HVAC/plumbing frame inconsistency blocks it.
3. **Real routing requests recoverable:** explicit connectivity inventories exist, but not as a registered, legitimate new routing request.
4. **Proceed to routing:** **No.** Resolve source frame/unit registration and implement true lazy chunk loading first.
