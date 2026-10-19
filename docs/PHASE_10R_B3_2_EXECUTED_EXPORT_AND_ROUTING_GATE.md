# Phase 10R-B3.2: Executed Export and Routing Gate

## Executed result

The no-limit command was executed against the official ignored Medical-Dental Clinic payloads:

```powershell
$env:PYTHONPATH='.'
python -m src.medical_dental_ifc --progress --output viewer\medical_dental_clinic_fixture.json
```

The regenerated JSON parses successfully and is 119,022,316 bytes.  Full selected-First-Floor tessellation completed for every candidate; no `--max-meshes` limit was used.

That local monolithic export is ignored rather than committed: its 105 MB Git
working-tree size exceeds GitHub's 100 MB per-file limit. The publication
artifact is the tracked progressive manifest and deterministic chunks described
in B3.3.

| Discipline | Candidate / exported / skipped | Coverage | Triangles | Tessellation seconds |
|---|---:|---:|---:|---:|
| Architecture | 128 / 128 / 0 | 100% | 45,088 | 11.857 |
| Structure | 463 / 463 / 0 | 100% | 191,604 | 26.416 |
| HVAC | 1,196 / 1,196 / 0 | 100% | 184,530 | 45.138 |
| Plumbing | 3,714 / 3,714 / 0 | 100% | 1,884,287 | 527.392 |
| Electrical | 1,165 / 1,165 / 0 | 100% | 233,749 | 38.117 |
| **Total** | **6,666 / 6,666 / 0** | **100%** | **2,539,258** | **649.0** |

The exporter’s resident working set rose to approximately 845 MB during complete export/serialization.  Plumbing is the dominant cost.  This is an actual execution measurement and establishes that a single monolithic 119 MB fixture is not an acceptable default browser-delivery strategy, despite complete geometry coverage.  The next implementation must write deterministic spatial chunks and lazy-load them per discipline/storey; it must preserve the same counts and GlobalIds.

## Federation alignment

All input models are IFC2X3 and their declared-unit conversion is retained. No recentering, rotation, or scale transform was applied. First-floor containment labels differ: structure uses `First Floor`, plumbing uses `Level 1`, and architecture/HVAC/electrical use room identifiers beginning `1`.  This supports a first-floor selection convention only; it does not establish a federated reference frame. **Alignment classification: UNVERIFIED.** Visual overlap, if observed, cannot promote this classification.

## Viewer and screenshot gate

The regenerated JSON is parseable and its layer counts agree with exported meshes. The existing viewer can parse its real-IFC layer schema, but loading a 119 MB fixture with 2.54 million triangles is not yet a defensible usability result. A screenshot is therefore intentionally not offered as evidence of successful full-browser inspection. B3.3 must implement spatial chunking/progressive load, then capture a screenshot of the chunked complete First Floor view and record browser load timing/memory.

## First routing-test gate

The First Floor is genuinely complex: the complete source export contains architecture, structure, HVAC, plumbing, and electrical geometry. Yet a legitimate routing test is **not authorized**. The engine still lacks: independently verified cross-discipline registration; opening/shaft semantics; an approved obstacle/clearance solid policy; native source-to-terminal demand selection; and a bounded voxelization policy for this 2.54-million-triangle set. Existing explicit port inventories do not provide a proven new-routing request, and no endpoints were invented.

A future read-only test proposal is: after registration evidence, select one documented First Floor service-zone box around a real explicit source/terminal pair; tessellate registered architecture/structure into an explicitly sized grid; retain openings only when semantically validated; and run unchanged routing only if source-frame, endpoint, and memory gates pass. This is a proposal, not an execution or feasibility claim.
