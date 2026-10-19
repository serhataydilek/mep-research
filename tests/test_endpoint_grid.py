"""Regression tests for port-to-grid mapping and physical terminal stubs."""

import ifcopenshell
import pytest

from src.endpoint_grid import (
    ENDPOINT_OK,
    ENDPOINT_OUTSIDE_GRID,
    TERMINAL_RELOCATED,
    assemble_physical_polyline,
    cell_center,
    containing_index,
    locate_containing_cell,
    orthogonal_connector,
    polyline_metrics,
    relocation_detected,
    trace_relocated_terminals,
)
from src.external_ifc_probe import placement_origin_m, scale_placement_translation, world_vertices_metres
from src.external_ifc_routing import (
    broad_phase_world_polyline,
    mark_obstacle_aabbs,
    obstacle_decision,
    resolve_physical_terminal,
)
from src.routing import astar_3d, route_metrics
from src.voxel import FREE, GridSpec, OccupancyGrid


DIGITALHUB_ORIGIN = (48.6, 13.100000000000001, -1.7000000000000002)
START_PORT = (50.245955, 15.827534, 0.100)
END_PORT = (50.149955, 14.687534, -0.120)


def _empty_grid(origin, counts):
    spec = GridSpec(0.1, origin[0], origin[1], origin[2], counts[0], counts[1], counts[2])
    return OccupancyGrid("endpoint", "heating", spec, bytearray(spec.total_cell_count), (0, 0, 0, 0, 0, 0), {})


def test_vertical_gap_of_0_220_m_does_not_collapse_to_one_physical_z():
    origin = (0.0, 0.0, -0.2)
    start = (0.0, 0.0, 0.100)
    end = (0.40, 0.0, -0.120)
    grid = _empty_grid(origin, (6, 2, 5))
    start_cell = containing_index(origin, 0.1, start)
    end_cell = containing_index(origin, 0.1, end)
    assert start_cell[2] != end_cell[2]
    assert abs(end[2] - start[2]) == 0.220
    result = astar_3d(grid, start_cell, end_cell)
    assert result.path_found
    centers = [grid.spec.cell_center(*cell) for cell in result.path_cells]
    physical = assemble_physical_polyline(start, centers, end)
    metrics = polyline_metrics(physical["polyline_m"])
    grid_metrics = route_metrics(result, 0.1)
    assert physical["exact_port_to_port"]
    assert physical["polyline_m"][0] == list(start)
    assert physical["polyline_m"][-1] == list(end)
    assert physical["polyline_m"][0][2] != physical["polyline_m"][-1][2]
    assert metrics["vertical_travel_m"] >= 0.220
    assert metrics["orthogonal"]
    assert metrics["net_vertical_m"] == end[2] - start[2]
    assert grid_metrics["vertical_travel_m"] == abs(end_cell[2] - start_cell[2]) * 0.1
    assert grid_metrics["vertical_step_count"] > 0


def test_world_grid_world_round_trip_uses_a_nonzero_origin():
    point = START_PORT
    index = containing_index(DIGITALHUB_ORIGIN, 0.1, point)
    center = cell_center(DIGITALHUB_ORIGIN, 0.1, index)
    assert containing_index(DIGITALHUB_ORIGIN, 0.1, center) == index
    assert center[2] != point[2]
    assert index == (16, 27, 18)


def test_negative_and_boundary_coordinates_are_not_shifted():
    assert containing_index((0.0, 0.0, 0.0), 0.1, (-0.25, -1.0, -0.1)) == (-3, -10, -1)
    assert containing_index((0.0, 0.0, 0.0), 0.1, (0.0, 0.0, 0.0)) == (0, 0, 0)
    assert containing_index((0.0, 0.0, 0.0), 0.1, (0.1, 0.2, 0.3)) == (1, 2, 3)
    assert containing_index(DIGITALHUB_ORIGIN, 0.1, END_PORT)[2] == 15


def test_nested_millimetre_placement_is_scaled_once():
    model = ifcopenshell.file(schema="IFC4")

    def axis2(coordinates):
        location = model.create_entity("IfcCartesianPoint", Coordinates=list(coordinates))
        return model.create_entity("IfcAxis2Placement3D", Location=location)

    parent = model.create_entity("IfcLocalPlacement", RelativePlacement=axis2((1000.0, 0.0, 200.0)))
    child = model.create_entity(
        "IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=axis2((0.0, 500.0, 20.0)),
    )
    port = model.create_entity(
        "IfcDistributionPort", GlobalId=ifcopenshell.guid.new(), Name="P", ObjectPlacement=child,
    )
    world = placement_origin_m(port, 0.001)
    assert world == (1.0, 0.5, 0.22)
    assert scale_placement_translation((1000.0, 500.0, 220.0), 0.001) == world
    assert tuple(value * 0.001 for value in world) != world
    assert world_vertices_metres([1000.0, 500.0, 220.0]) == [1000.0, 500.0, 220.0]


def test_clamping_and_projection_are_reported_instead_of_applied():
    outside = locate_containing_cell((0.0, 0.0, 0.0), 0.1, (4, 4, 4), (-0.2, 0.05, 9.0))
    assert outside["status"] == ENDPOINT_OUTSIDE_GRID
    assert outside["containing_index"] == [-2, 0, 90]
    assert outside["clamped"] is False
    assert outside["projected"] is False
    detected = relocation_detected(outside["containing_index"], (0, 0, 3))
    assert detected["status"] == TERMINAL_RELOCATED
    assert detected["z_index_changed"] is True


def test_blocked_terminal_keeps_its_containing_cell():
    grid = _empty_grid((0.0, 0.0, 0.0), (4, 4, 4))
    obstacles = [{
        "global_id": "slab",
        "ifc_class": "IfcSlab",
        "file_role": "architecture",
        "decision": "ARCHITECTURAL_OBSTACLE",
        "bounds_m": [0.0, 0.09, 0.0, 0.09, 0.0, 0.09],
    }]
    mark_obstacle_aabbs(grid, obstacles, 0.0)
    terminal = resolve_physical_terminal(grid, (0.05, 0.05, 0.05), obstacles, 0.0, role="goal")
    assert terminal["status"] == "ENDPOINT_CELL_BLOCKED"
    assert terminal["containing_index"] == [0, 0, 0]
    assert terminal["relocated"] is False
    assert terminal["blocking_obstacles"][0]["global_id"] == "slab"
    assert grid.cells[grid.spec.linear_index(1, 0, 0)] == FREE


def test_orthogonal_stubs_account_for_length_and_vertical_travel():
    stub = orthogonal_connector((0.0, 0.0, 0.100), (0.15, 0.05, 0.15))
    assert stub == [[0.0, 0.0, 0.100], [0.15, 0.0, 0.100], [0.15, 0.05, 0.100], [0.15, 0.05, 0.15]]
    metrics = polyline_metrics(stub)
    assert metrics["orthogonal"]
    assert metrics["length_m"] == pytest.approx(0.15 + 0.05 + 0.05)
    assert metrics["vertical_travel_m"] == pytest.approx(0.05)
    assert metrics["bend_count"] == 2
    route = assemble_physical_polyline((0.0, 0.0, 0.100), [(0.15, 0.05, 0.15), (0.15, 0.05, -0.15)], (0.40, 0.0, -0.120))
    route_metrics_physical = polyline_metrics(route["polyline_m"])
    assert route["exact_port_to_port"]
    assert route_metrics_physical["vertical_travel_m"] >= 0.220
    assert route_metrics_physical["orthogonal"]


def test_replaced_host_exclusion_does_not_cover_retained_elements_or_openings():
    replaced = {f"host-{index}" for index in range(6)}
    for global_id in replaced:
        assert obstacle_decision("IfcPipeSegment", "heating", global_id, replaced) == "REPLACED_HOST_EXCLUDED"
    assert obstacle_decision("IfcSpaceHeater", "heating", "retained-heater", replaced) == "RETAINED_MEP_OBSTACLE"
    assert obstacle_decision("IfcPipeSegment", "heating", "retained-pipe", replaced) == "RETAINED_MEP_OBSTACLE"
    assert obstacle_decision("IfcSlab", "architecture", "slab", replaced) == "ARCHITECTURAL_OBSTACLE"
    assert obstacle_decision("IfcOpeningElement", "architecture", "opening", replaced) == "OPENING_INVENTORIED_NOT_SUBTRACTED"


def test_stub_collision_is_explicit_and_zero_hits_are_not_mesh_clearance():
    points = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    wall = {
        "global_id": "wall",
        "ifc_class": "IfcWall",
        "file_role": "architecture",
        "decision": "ARCHITECTURAL_OBSTACLE",
        "bounds_m": [0.4, 0.6, -0.1, 0.1, -0.1, 0.1],
    }
    hit = broad_phase_world_polyline(points, [wall], 0.01, set())
    assert hit["label"] == "BROAD_PHASE_AABB_HITS"
    assert hit["hit_count"] == 1
    assert hit["confirmed_collision_result"] is False
    clear = broad_phase_world_polyline(points, [wall], 0.01, {"wall"})
    assert clear["label"] == "ZERO_BROAD_PHASE_CANDIDATES"
    assert clear["hit_count"] == 0
    assert clear["confirmed_collision_result"] is False


def test_phase11a_proxy_nodes_are_not_the_port_containing_cells():
    trace = trace_relocated_terminals(
        DIGITALHUB_ORIGIN,
        0.1,
        START_PORT,
        END_PORT,
        (50.224799, 15.734954, 0.044635),
        (50.1521, 14.797433, -0.118746),
        (15, 26, 17),
        (15, 16, 17),
    )
    assert trace["port_z_delta_m"] == -0.220
    assert trace["start_containing_index"] == [16, 27, 18]
    assert trace["end_containing_index"] == [15, 15, 15]
    assert trace["start_proxy_containing_index"][2] == 17
    assert trace["end_proxy_containing_index"][2] == 15
    assert trace["used_start_index"][2] == 17
    assert trace["used_end_index"][2] == 17
    assert trace["containing_z_indices_differ"]
    assert trace["used_z_indices_equal"]
    assert trace["start_relocation"]["status"] == TERMINAL_RELOCATED
    assert trace["end_relocation"]["z_index_changed"] is True
    reconstructed = assemble_physical_polyline(START_PORT, [cell_center(DIGITALHUB_ORIGIN, 0.1, (16, 27, 18))], END_PORT)
    assert reconstructed["polyline_m"][0][2] == 0.100
    assert reconstructed["polyline_m"][-1][2] == -0.120
    assert polyline_metrics(reconstructed["polyline_m"])["vertical_travel_m"] >= 0.220
    assert locate_containing_cell(DIGITALHUB_ORIGIN, 0.1, (35, 62, 39), START_PORT)["status"] == ENDPOINT_OK
