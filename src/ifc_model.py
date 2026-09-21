"""Creation of the deterministic IFC4 spatial model with perimeter walls."""

from __future__ import annotations

from typing import Any

import ifcopenshell
import ifcopenshell.api.geometry
import ifcopenshell.util.unit

from .config import interior_grid_positions
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


def _create_contexts(
    model: ifcopenshell.file,
) -> tuple[ifcopenshell.entity_instance, ifcopenshell.entity_instance]:
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
    body_context = model.create_entity(
        "IfcGeometricRepresentationSubContext",
        ContextIdentifier="Body",
        ContextType="Model",
        ParentContext=model_context,
        TargetView="MODEL_VIEW",
    )
    return model_context, body_context


def _create_local_placement(
    model: ifcopenshell.file,
    relative_to: ifcopenshell.entity_instance | None,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> ifcopenshell.entity_instance:
    location = model.create_entity("IfcCartesianPoint", Coordinates=(x, y, z))
    axis_placement = model.create_entity("IfcAxis2Placement3D", Location=location)
    return model.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=relative_to,
        RelativePlacement=axis_placement,
    )


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


def _create_slab(
    model: ifcopenshell.file,
    body_context: ifcopenshell.entity_instance,
    storey: ifcopenshell.entity_instance,
    floor_index: int,
    width: float,
    length: float,
    thickness: float,
) -> ifcopenshell.entity_instance:
    representation = ifcopenshell.api.geometry.add_slab_representation(
        model,
        context=body_context,
        depth=thickness,
        polyline=[(0.0, 0.0), (width, 0.0), (width, length), (0.0, length)],
    )
    slab = model.create_entity(
        "IfcSlab",
        GlobalId=semantic_ifc_guid(f"storey/{floor_index}/slab/main"),
        Name=f"Floor Slab {floor_index}",
        PredefinedType="FLOOR",
        ObjectPlacement=_create_local_placement(model, storey.ObjectPlacement),
    )
    slab.Representation = model.create_entity(
        "IfcProductDefinitionShape",
        Representations=[representation],
    )
    return slab


def _create_perimeter_walls(
    model: ifcopenshell.file,
    body_context: ifcopenshell.entity_instance,
    storey: ifcopenshell.entity_instance,
    floor_index: int,
    width: float,
    length: float,
    slab_thickness: float,
    wall_thickness: float,
    wall_height: float,
) -> list[ifcopenshell.entity_instance]:
    """Create the four inside-footprint wall volumes in stable compass order."""
    wall_specs = (
        ("south", "South", 0.0, 0.0, width, wall_thickness),
        ("north", "North", 0.0, length - wall_thickness, width, wall_thickness),
        (
            "west",
            "West",
            0.0,
            wall_thickness,
            wall_thickness,
            length - (2 * wall_thickness),
        ),
        (
            "east",
            "East",
            width - wall_thickness,
            wall_thickness,
            wall_thickness,
            length - (2 * wall_thickness),
        ),
    )
    walls = []
    for direction, label, x, y, wall_width, wall_length in wall_specs:
        representation = ifcopenshell.api.geometry.add_slab_representation(
            model,
            context=body_context,
            depth=wall_height,
            polyline=[
                (0.0, 0.0),
                (wall_width, 0.0),
                (wall_width, wall_length),
                (0.0, wall_length),
            ],
        )
        wall = model.create_entity(
            "IfcWall",
            GlobalId=semantic_ifc_guid(f"storey/{floor_index}/wall/{direction}"),
            Name=f"{label} Wall {floor_index}",
            ObjectPlacement=_create_local_placement(
                model, storey.ObjectPlacement, x, y, slab_thickness
            ),
        )
        wall.Representation = model.create_entity(
            "IfcProductDefinitionShape", Representations=[representation]
        )
        walls.append(wall)
    return walls


def _create_structural_columns(
    model: ifcopenshell.file,
    body_context: ifcopenshell.entity_instance,
    storey: ifcopenshell.entity_instance,
    floor_index: int,
    width: float,
    length: float,
    column_size: float,
    grid_spacing_x: float,
    grid_spacing_y: float,
    slab_thickness: float,
    wall_height: float,
) -> list[ifcopenshell.entity_instance]:
    """Create interior square columns in stable X-then-Y grid order."""
    columns = []
    half_column = column_size / 2
    for x_index, x_center in interior_grid_positions(grid_spacing_x, width):
        for y_index, y_center in interior_grid_positions(grid_spacing_y, length):
            representation = ifcopenshell.api.geometry.add_slab_representation(
                model,
                context=body_context,
                depth=wall_height,
                polyline=[
                    (0.0, 0.0),
                    (column_size, 0.0),
                    (column_size, column_size),
                    (0.0, column_size),
                ],
            )
            column = model.create_entity(
                "IfcColumn",
                GlobalId=semantic_ifc_guid(
                    f"storey/{floor_index}/column/{x_index}/{y_index}"
                ),
                Name=f"Column {floor_index}-{x_index}-{y_index}",
                PredefinedType="COLUMN",
                ObjectPlacement=_create_local_placement(
                    model,
                    storey.ObjectPlacement,
                    x_center - half_column,
                    y_center - half_column,
                    slab_thickness,
                ),
            )
            column.Representation = model.create_entity(
                "IfcProductDefinitionShape", Representations=[representation]
            )
            columns.append(column)
    return columns


def create_ifc_model(config: dict[str, Any]) -> ifcopenshell.file:
    """Create an IFC4 project, site, building, and configured storeys."""
    floors = config["floors"]
    floor_to_floor = config["floor_to_floor_m"]
    width = config["width_m"]
    length = config["length_m"]
    slab_thickness = config["slab_thickness_m"]
    wall_thickness = config["wall_thickness_m"]
    column_size = config["column_size_m"]
    grid_spacing_x = config["grid_spacing_x_m"]
    grid_spacing_y = config["grid_spacing_y_m"]

    model = ifcopenshell.file(schema="IFC4")
    units = _create_units(model)
    model_context, body_context = _create_contexts(model)
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
        ObjectPlacement=_create_local_placement(model, None),
    )
    building = model.create_entity(
        "IfcBuilding",
        GlobalId=semantic_ifc_guid("building"),
        Name="Research Building",
        ObjectPlacement=_create_local_placement(model, site.ObjectPlacement),
    )

    _aggregate(model, "relationship/project-site", project, site)
    _aggregate(model, "relationship/site-building", site, building)

    for floor_index in range(floors):
        storey = model.create_entity(
            "IfcBuildingStorey",
            GlobalId=semantic_ifc_guid(f"storey/{floor_index}"),
            Name=f"Storey {floor_index}",
            Elevation=floor_index * floor_to_floor,
            ObjectPlacement=_create_local_placement(
                model,
                building.ObjectPlacement,
                z=floor_index * floor_to_floor,
            ),
        )
        _aggregate(
            model,
            f"relationship/building-storey/{floor_index}",
            building,
            storey,
        )
        slab = _create_slab(
            model,
            body_context,
            storey,
            floor_index,
            width,
            length,
            slab_thickness,
        )
        walls = _create_perimeter_walls(
            model,
            body_context,
            storey,
            floor_index,
            width,
            length,
            slab_thickness,
            wall_thickness,
            floor_to_floor - slab_thickness,
        )
        columns = _create_structural_columns(
            model,
            body_context,
            storey,
            floor_index,
            width,
            length,
            column_size,
            grid_spacing_x,
            grid_spacing_y,
            slab_thickness,
            floor_to_floor - slab_thickness,
        )
        model.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=semantic_ifc_guid(f"relationship/storey/{floor_index}/contains/elements"),
            RelatedElements=[slab, *walls, *columns],
            RelatingStructure=storey,
        )

    if ifcopenshell.util.unit.calculate_unit_scale(model) != 1.0:
        raise ValueError("IFC project length unit scale must be exactly 1.0 metre.")

    return model
