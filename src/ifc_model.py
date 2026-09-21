"""Creation of the deterministic IFC4 spatial model with perimeter walls."""

from __future__ import annotations

from typing import Any

import ifcopenshell
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.util.unit

from .config import interior_grid_positions, shaft_clear_bounds, shaft_outer_bounds
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


def _create_shaft_walls(
    model: ifcopenshell.file,
    body_context: ifcopenshell.entity_instance,
    storey: ifcopenshell.entity_instance,
    floor_index: int,
    clear_bounds: tuple[float, float, float, float],
    wall_thickness: float,
    slab_thickness: float,
    wall_height: float,
) -> list[ifcopenshell.entity_instance]:
    """Create the shaft envelope with non-overlapping corner wall volumes."""
    clear_min_x, clear_max_x, clear_min_y, clear_max_y = clear_bounds
    outer_min_x, outer_max_x, outer_min_y, outer_max_y = shaft_outer_bounds(
        clear_bounds, wall_thickness
    )
    wall_specs = (
        ("south", "South", outer_min_x, outer_min_y, outer_max_x - outer_min_x, wall_thickness),
        ("north", "North", outer_min_x, clear_max_y, outer_max_x - outer_min_x, wall_thickness),
        ("west", "West", outer_min_x, clear_min_y, wall_thickness, clear_max_y - clear_min_y),
        ("east", "East", clear_max_x, clear_min_y, wall_thickness, clear_max_y - clear_min_y),
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
            GlobalId=semantic_ifc_guid(f"storey/{floor_index}/shaft/main/wall/{direction}"),
            Name=f"Shaft {label} Wall {floor_index}",
            ObjectPlacement=_create_local_placement(
                model, storey.ObjectPlacement, x, y, slab_thickness
            ),
        )
        wall.Representation = model.create_entity(
            "IfcProductDefinitionShape", Representations=[representation]
        )
        walls.append(wall)
    return walls


def _create_shaft_space(
    model: ifcopenshell.file,
    body_context: ifcopenshell.entity_instance,
    storey: ifcopenshell.entity_instance,
    floor_index: int,
    clear_bounds: tuple[float, float, float, float],
    floor_to_floor: float,
) -> ifcopenshell.entity_instance:
    clear_min_x, clear_max_x, clear_min_y, clear_max_y = clear_bounds
    representation = ifcopenshell.api.geometry.add_slab_representation(
        model,
        context=body_context,
        depth=floor_to_floor,
        polyline=[
            (0.0, 0.0),
            (clear_max_x - clear_min_x, 0.0),
            (clear_max_x - clear_min_x, clear_max_y - clear_min_y),
            (0.0, clear_max_y - clear_min_y),
        ],
    )
    shaft_space = model.create_entity(
        "IfcSpace",
        GlobalId=semantic_ifc_guid(f"storey/{floor_index}/shaft/main/space"),
        Name=f"Service Shaft {floor_index}",
        CompositionType="ELEMENT",
        PredefinedType="INTERNAL",
        ObjectPlacement=_create_local_placement(
            model, storey.ObjectPlacement, clear_min_x, clear_min_y
        ),
    )
    shaft_space.Representation = model.create_entity(
        "IfcProductDefinitionShape", Representations=[representation]
    )
    _aggregate(
        model,
        f"relationship/storey/{floor_index}/shaft/main/space",
        storey,
        shaft_space,
    )
    return shaft_space


def _create_slab_shaft_opening(
    model: ifcopenshell.file,
    body_context: ifcopenshell.entity_instance,
    storey: ifcopenshell.entity_instance,
    slab: ifcopenshell.entity_instance,
    floor_index: int,
    clear_bounds: tuple[float, float, float, float],
    slab_thickness: float,
) -> ifcopenshell.entity_instance:
    """Create a semantic opening whose representation voids the host slab."""
    clear_min_x, clear_max_x, clear_min_y, clear_max_y = clear_bounds
    epsilon = 0.01
    representation = ifcopenshell.api.geometry.add_slab_representation(
        model,
        context=body_context,
        depth=slab_thickness + (2 * epsilon),
        polyline=[
            (0.0, 0.0),
            (clear_max_x - clear_min_x, 0.0),
            (clear_max_x - clear_min_x, clear_max_y - clear_min_y),
            (0.0, clear_max_y - clear_min_y),
        ],
    )
    opening = model.create_entity(
        "IfcOpeningElement",
        GlobalId=semantic_ifc_guid(f"storey/{floor_index}/shaft/main/slab-opening"),
        Name=f"Shaft Slab Opening {floor_index}",
        ObjectPlacement=_create_local_placement(
            model, storey.ObjectPlacement, clear_min_x, clear_min_y, -epsilon
        ),
    )
    opening.Representation = model.create_entity(
        "IfcProductDefinitionShape", Representations=[representation]
    )
    void_relationship = ifcopenshell.api.feature.add_feature(
        model, feature=opening, element=slab
    )
    void_relationship.GlobalId = semantic_ifc_guid(
        f"relationship/storey/{floor_index}/slab/main/voids/shaft/main"
    )
    return opening


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
    shaft_width = config["shaft"]["width_m"]
    shaft_length = config["shaft"]["length_m"]
    shaft_clear = shaft_clear_bounds(width, length, shaft_width, shaft_length)

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
        _create_slab_shaft_opening(
            model,
            body_context,
            storey,
            slab,
            floor_index,
            shaft_clear,
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
        shaft_walls = _create_shaft_walls(
            model,
            body_context,
            storey,
            floor_index,
            shaft_clear,
            wall_thickness,
            slab_thickness,
            floor_to_floor - slab_thickness,
        )
        _create_shaft_space(
            model,
            body_context,
            storey,
            floor_index,
            shaft_clear,
            floor_to_floor,
        )
        model.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=semantic_ifc_guid(f"relationship/storey/{floor_index}/contains/elements"),
            RelatedElements=[slab, *walls, *columns, *shaft_walls],
            RelatingStructure=storey,
        )

    if ifcopenshell.util.unit.calculate_unit_scale(model) != 1.0:
        raise ValueError("IFC project length unit scale must be exactly 1.0 metre.")

    return model
