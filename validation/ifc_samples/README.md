# IFC Validation Samples

External IFC files are downloaded to ignored `validation/ifc_samples/external/` and supplied by path to `python -m src.ifc_validation`. Third-party IFC payloads are not committed; immutable provenance and file digests are tracked in `manifest.json`.

| Sample ID | Original filename | Source | Schema | Purpose | Tracking | License/source note |
|---|---|---|---|---|---|---|
| `control_generated_ifc` | `architecture.ifc` | Project-controlled generator | IFC4 | Regression control only | Generated locally under ignored `out/` | Project source; not external evidence |
| `esplanades_ifc2x3` | `1807_EP_AR_v18.ifc` | Community Sample Test Files, commit `7ddf57a` | IFC2X3 | Project-scale, seven-storey architectural/georeferencing validation | Ignored external payload | CC BY 4.0 |
| `certification_building_architecture_ifc4` | `Building-Architecture.ifc` | Certification Datasets, commit `80d976a` | IFC4 | IFC4 building, hierarchy, geometry, and voxelization validation | Ignored external payload | CC BY 4.0 |
| `medical_dental_clinic_hvac_ifc2x3` | `Clinic_HVAC.ifc` | Community Sample Test Files, commit `7ddf57a` | IFC2X3 | Multi-storey MEP distribution inspection | Ignored external payload | CC BY 4.0 |

The generated control must always be labelled `CONTROL_GENERATED_IFC`. It cannot be cited as evidence that a real-world IFC was tested.

All three downloaded files were verified as IFC STEP payloads rather than Git LFS pointers or HTML responses. See `manifest.json` for exact immutable source URLs, byte sizes, SHA-256 digests, and validation purposes.
