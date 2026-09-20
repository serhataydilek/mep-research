"""Creation of the deterministic IFC4 spatial skeleton for Phase 1B."""

from __future__ import annotations

from typing import Any

import ifcopenshell
import ifcopenshell.util.unit

from .guid import semantic_ifc_guid


def _create_units(model: ifcopenshell.file) -> ifcopenshell.entity_instance:
    length_unit = model.create_entity(
        "IfcSIUnit",
        UnitType="LENGTHUNIT",
        Prefix=None,
        Name="METRE",
    )
    area_unit = model.create_entity(
        "IfcSIUnit",
        UnitType="AREAUNIT",
        Prefix=None,
        Name="SQUARE_METRE",
    )
    volume_unit = model.create_entity(
        "IfcSIUnit",
        UnitType="VOLUMEUNIT",
        Prefix=None,
        Name="CUBIC_METRE",
    )
    return model.create_entity("IfcUnitAssignment", Units=[length_unit, area_unit, volume_unit])


def _create_contexts(model: ifcopenshell.file) -> ifcopenshell.entity_instance:
    origin = model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    placement = model.create_entity("IfcAxis2Placement3D", Location=origin)
    model_context = model.create_entity(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Model",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=0.00001,
        WorldCoordinateSystem=placement,
    )
    model.create_entity(
        "IfcGeometricRepresentationSubContext",
        ContextIdentifier="Body",
        ContextType="Model",
        ParentContext=model_context,
        TargetView="MODEL_VIEW",
    )
    return model_context


def _aggregate(
    model: ifcopenshell.file,
    semantic_path: str,
    parent: ifcopenshell.entity_instance,
    child: ifcopenshell.entity_instance,
) -> None:
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=semantic_ifc_guid(semantic_path),
        RelatingObject=parent,
        RelatedObjects=[child],
    )


def create_ifc_model(config: dict[str, Any]) -> ifcopenshell.file:
    """Create an IFC4 project, site, building, and configured storeys."""
    floors = config["floors"]
    floor_to_floor = config["floor_to_floor_m"]

    model = ifcopenshell.file(schema="IFC4")
    units = _create_units(model)
    model_context = _create_contexts(model)
    project = model.create_entity(
        "IfcProject",
        GlobalId=semantic_ifc_guid("project"),
        Name="MEP Routing Research",
        RepresentationContexts=[model_context],
        UnitsInContext=units,
    )
    site = model.create_entity(
        "IfcSite",
        GlobalId=semantic_ifc_guid("site"),
        Name="Research Site",
    )
    building = model.create_entity(
        "IfcBuilding",
        GlobalId=semantic_ifc_guid("building"),
        Name="Research Building",
    )

    _aggregate(model, "relationship/project-site", project, site)
    _aggregate(model, "relationship/site-building", site, building)

    for floor_index in range(floors):
        storey = model.create_entity(
            "IfcBuildingStorey",
            GlobalId=semantic_ifc_guid(f"storey/{floor_index}"),
            Name=f"Storey {floor_index}",
            Elevation=floor_index * floor_to_floor,
        )
        _aggregate(
            model,
            f"relationship/building-storey/{floor_index}",
            building,
            storey,
        )

    if ifcopenshell.util.unit.calculate_unit_scale(model) != 1.0:
        raise ValueError("IFC project length unit scale must be exactly 1.0 metre.")

    return model
