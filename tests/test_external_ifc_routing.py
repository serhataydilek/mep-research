"""Unit tests for external IFC probing and the real-IFC A* request builder."""

from src.external_ifc_probe import scale_placement_translation, world_vertices_metres
from src.external_ifc_routing import (
    CONTROLLED_GEOMETRY_ENDPOINT,
    NATIVE_PORT_PLACEMENT,
    RELATIONSHIP_CONSTRAINED_GEOMETRIC,
    VOXEL_SIZE_M,
    classify_interface_point,
    mark_obstacle_aabbs,
    nearest_free_cell,
    obstacle_decision,
    select_flow_subpaths,
)
from src.routing import astar_3d
from src.voxel import BLOCKED_FIXED, FREE, GridSpec, OccupancyGrid


def test_placement_translation_is_scaled_once_and_world_meshes_are_not():
    assert scale_placement_translation((1000.0, 2000.0, 3000.0), 0.001) == (1.0, 2.0, 3.0)
    vertices = [1000.0, 2000.0, 3000.0]
    assert world_vertices_metres(vertices) == vertices


def test_flow_subpath_selection_is_deterministic_and_bounded():
    adjacency = {
        "J": {"a"},
        "a": {"J", "b"},
        "b": {"a", "c"},
        "c": {"b", "K"},
        "K": {"c"},
    }
    flow_ids = {"a", "b", "c"}
    first = select_flow_subpaths({key: set(value) for key, value in adjacency.items()}, flow_ids)
    second = select_flow_subpaths({key: set(value) for key, value in adjacency.items()}, flow_ids)
    assert first == second
    assert first[0]["replaced_host_global_ids"] == ["a", "b", "c"]
    assert first[0]["boundary_host_global_ids"] == ["J", "K"]

    long_flow = [f"n{index:02d}" for index in range(15)]
    long_adjacency = {"L": {long_flow[0]}, "R": {long_flow[-1]}}
    for index, node in enumerate(long_flow):
        neighbours = set()
        neighbours.add("L" if index == 0 else long_flow[index - 1])
        neighbours.add("R" if index == len(long_flow) - 1 else long_flow[index + 1])
        long_adjacency[node] = neighbours
    selected = select_flow_subpaths(long_adjacency, set(long_flow))
    assert selected[0]["replaced_host_global_ids"] == long_flow[:6]
    assert selected[0]["boundary_host_global_ids"][1] == long_flow[6]


def test_interface_classification_distinguishes_native_geometric_and_controlled_points():
    native, point, _ = classify_interface_point(
        port_world=(1.0, 2.0, 3.0), local_identity=False, mesh_distance_m=0.01, ports_agree=True,
        geometric_midpoint=(9.0, 9.0, 9.0), geometric_gap_m=0.01, controlled_point=(8.0, 8.0, 8.0),
    )
    assert native == NATIVE_PORT_PLACEMENT and point == [1.0, 2.0, 3.0]
    geometric, point, _ = classify_interface_point(
        port_world=(1.0, 2.0, 3.0), local_identity=True, mesh_distance_m=0.0, ports_agree=True,
        geometric_midpoint=(4.0, 5.0, 6.0), geometric_gap_m=0.02, controlled_point=(8.0, 8.0, 8.0),
    )
    assert geometric == RELATIONSHIP_CONSTRAINED_GEOMETRIC and point == [4.0, 5.0, 6.0]
    controlled, point, evidence = classify_interface_point(
        port_world=None, local_identity=True, mesh_distance_m=None, ports_agree=False,
        geometric_midpoint=(4.0, 5.0, 6.0), geometric_gap_m=2.0, controlled_point=(7.0, 7.0, 7.0),
    )
    assert controlled == CONTROLLED_GEOMETRY_ENDPOINT and point == [7.0, 7.0, 7.0]
    assert "replaced-host mesh" in evidence["rule"]


def test_replaced_hosts_are_excluded_and_openings_do_not_open_cells():
    assert obstacle_decision("IfcPipeSegment", "heating", "pipe", {"pipe"}) == "REPLACED_HOST_EXCLUDED"
    assert obstacle_decision("IfcOpeningElement", "architecture", "opening", set()) == "OPENING_INVENTORIED_NOT_SUBTRACTED"
    spec = GridSpec(VOXEL_SIZE_M, 0.0, 0.0, 0.0, 20, 10, 5)
    grid = OccupancyGrid("unit", "heating", spec, bytearray(spec.total_cell_count), (0, 0, 0, 0, 0, 0), {})
    obstacles = [
        {"global_id": "wall", "bounds_m": [1.0, 1.2, 0.4, 0.6, 0.0, 0.5]},
        {"global_id": "kept-pipe", "bounds_m": [0.0, 0.2, 0.4, 0.6, 0.1, 0.3]},
    ]
    mark_obstacle_aabbs(grid, obstacles, 0.0)
    blocked = grid.cells.count(BLOCKED_FIXED)
    assert blocked > 0
    assert grid.cells.count(BLOCKED_FIXED) == blocked
    start = nearest_free_cell(grid, (0.05, 0.05, 0.05))
    goal = nearest_free_cell(grid, (1.85, 0.85, 0.25))
    assert start and goal and start["grid_index"] != goal["grid_index"]
    result = astar_3d(grid, tuple(start["grid_index"]), tuple(goal["grid_index"]))
    assert result.path_found
    assert all(grid.cells[spec.linear_index(*cell)] == FREE for cell in result.path_cells)
