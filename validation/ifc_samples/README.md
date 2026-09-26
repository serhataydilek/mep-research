# IFC Validation Samples

External IFC files should be supplied by path to `python -m src.ifc_validation`; large third-party models are not committed here.

| Sample ID | Original filename | Source | Schema | Purpose | Tracking | License/source note |
|---|---|---|---|---|---|---|
| `control_generated_ifc` | `architecture.ifc` | Project-controlled generator | IFC4 | Regression control only | Generated locally under ignored `out/` | Project source; not external evidence |
| _required_ | _not yet supplied_ | _external source required_ | _unknown_ | Real-world compatibility validation | External path; not tracked | Record provenance and applicable license before use |

The generated control must always be labelled `CONTROL_GENERATED_IFC`. It cannot be cited as evidence that a real-world IFC was tested.
