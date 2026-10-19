"""Phase 11B clearance occupancy, endpoint preservation, and request selection."""

from src.endpoint_grid import assemble_physical_polyline
from src.endpoint_qualification import (
    QUALIFIED_BOUNDARY_ALLOWANCE,
    REJECTED_CELL_SOLID,
    decide_terminal,
)
from src.external_ifc_routing import mark_obstacle_aabbs
from src.ifc_mesh_section import cells_touching_mesh, triangles_from_mesh
from src.phase11b_clearance import (
    BOUNDARY_HOST_CONNECTION_CONTACT,
    CLEARANCE_VIOLATION,
    CONFIRMED_GEOMETRIC_COLLISION,
    GEOMETRIC_VALIDATION_INCOMPLETE,
    PHYSICALLY_ACCEPTABLE,
    SEARCH_NO_PATH,
    SEARCH_PATH_FOUND,
    SEPARATED,
    classify_proximity,
    geometric_state,
    phase11b_rank,
    point_in_allowance_cell,
    search_state,
    shared_roi_group,
)
from src.voxel import BLOCKED_FIXED, FREE, GridSpec, OccupancyGrid


def _box(x0, x1, y0, y1, z0, z1):
    vertices = [
        x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0,
        x0, y0, z1, x1, y0, z1, x1, y1, z1, x0, y1, z1,
    ]
    faces = [
        0, 1, 2, 0, 2, 3,
        4, 6, 5, 4, 7, 6,
        0, 4, 5, 0, 5, 1,
        1, 5, 6, 1, 6, 2,
        2, 6, 7, 2, 7, 3,
        3, 7, 4, 3, 4, 0,
    ]
    return triangles_from_mesh(vertices, faces)


def _grid():
    spec = GridSpec(0.1, 0.0, 0.0, 0.0, 10, 10, 10)
    return OccupancyGrid("case", "hvac", spec, bytearray(spec.total_cell_count), (0, 0, 0, 0, 0, 0), {})


def _preview(discipline, replaced, obstacles, distance=4.0):
    return {
        "discipline": discipline,
        "straight_line_m": distance,
        "same_system": True,
        "same_storey": True,
        "approximate_obstacle_count": obstacles,
        "port_inside_slab_aabb": False,
        "replaced_host_global_ids": list(replaced),
        "boundary_host_global_ids": ["boundary-a", "boundary-b"],
    }


def test_endpoint_coordinates_stay_fixed_when_clearance_changes():
    point = (1.25, 2.5, 3.05)
    loose = decide_terminal(
        point_m=point,
        cell_index=(1, 2, 3),
        cell_center_m=(1.25, 2.55, 3.05),
        cell_blocked=True,
        outside_grid=False,
        boundary_host_global_id="duct",
        blockers=[{"global_id": "duct"}, {"global_id": "ceiling"}],
        mesh_facts_by_id={
            "duct": {"resolved": True, "port_surface_distance_m": 0.0, "port_enclosed": False, "other_enclosed": False, "other_on_surface": False, "other_surface_distance_m": 0.2},
            "ceiling": {"resolved": True, "port_surface_distance_m": 0.2, "port_enclosed": False, "other_enclosed": False, "other_on_surface": False, "other_surface_distance_m": 0.02},
        },
        solid_contact_m=0.001,
    )
    tight = decide_terminal(
        point_m=point,
        cell_index=(1, 2, 3),
        cell_center_m=(1.25, 2.55, 3.05),
        cell_blocked=True,
        outside_grid=False,
        boundary_host_global_id="duct",
        blockers=[{"global_id": "duct"}, {"global_id": "ceiling"}],
        mesh_facts_by_id={
            "duct": {"resolved": True, "port_surface_distance_m": 0.0, "port_enclosed": False, "other_enclosed": False, "other_on_surface": False, "other_surface_distance_m": 0.2},
            "ceiling": {"resolved": True, "port_surface_distance_m": 0.2, "port_enclosed": False, "other_enclosed": False, "other_on_surface": False, "other_surface_distance_m": 0.02},
        },
        solid_contact_m=0.105,
    )
    assert loose["immutable_point_m"] == [1.25, 2.5, 3.05]
    assert tight["immutable_point_m"] == loose["immutable_point_m"]
    assert loose["relocated"] is False and tight["relocated"] is False
    assert loose["cell_index"] == tight["cell_index"] == [1, 2, 3]


def test_clearance_inflation_occupies_cells_the_body_can_reach():
    obstacle = {"bounds_m": [0.45, 0.55, 0.45, 0.55, 0.45, 0.55], "global_id": "wall"}
    small = _grid()
    large = _grid()
    mark_obstacle_aabbs(small, [dict(obstacle)], 0.0)
    mark_obstacle_aabbs(large, [dict(obstacle)], 0.1)
    assert large.cells.count(BLOCKED_FIXED) > small.cells.count(BLOCKED_FIXED)
    assert small.cells.count(FREE) > large.cells.count(FREE)
    triangles = _box(0.0, 1.0, 0.0, 1.0, 0.0, 1.0)
    point = [[0.5, 0.5, 1.02]]
    assert not bool(cells_touching_mesh(point, triangles, contact_m=0.001)[0])
    assert bool(cells_touching_mesh(point, triangles, contact_m=0.05)[0])


def test_boundary_host_allowance_does_not_excuse_another_solid():
    facts = {
        "duct": {
            "resolved": True, "port_surface_distance_m": 0.001, "port_enclosed": False,
            "other_enclosed": False, "other_on_surface": False, "other_surface_distance_m": 0.2,
        },
        "ceiling": {
            "resolved": True, "port_surface_distance_m": 0.2, "port_enclosed": False,
            "other_enclosed": False, "other_on_surface": False, "other_surface_distance_m": 0.02,
        },
    }
    blockers = [{"global_id": "duct", "ifc_class": "IfcDuctSegment"}, {"global_id": "ceiling", "ifc_class": "IfcCovering"}]
    loose = decide_terminal(
        point_m=(0.0, 0.0, 0.1), cell_index=(1, 2, 3), cell_center_m=(0.05, 0.05, 0.15),
        cell_blocked=True, outside_grid=False, boundary_host_global_id="duct",
        blockers=blockers, mesh_facts_by_id=facts, solid_contact_m=0.001,
    )
    tight = decide_terminal(
        point_m=(0.0, 0.0, 0.1), cell_index=(1, 2, 3), cell_center_m=(0.05, 0.05, 0.15),
        cell_blocked=True, outside_grid=False, boundary_host_global_id="duct",
        blockers=blockers, mesh_facts_by_id=facts, solid_contact_m=0.105,
    )
    assert loose["status"] == QUALIFIED_BOUNDARY_ALLOWANCE
    assert loose["allowance"]["whole_host_excluded"] is False
    assert loose["allowance"]["discipline_or_system_excluded"] is False
    assert loose["allowance"]["host_global_id"] == "duct"
    assert tight["qualified"] is False
    assert tight["status"] == REJECTED_CELL_SOLID
    assert tight["obstruction_global_id"] == "ceiling"
    assert tight["release_cell"] is False


def test_physical_stub_contact_is_separated_from_the_rest_of_the_route():
    polyline = assemble_physical_polyline((0.02, 0.02, 0.05), [[0.05, 0.05, 0.05], [1.05, 0.05, 0.05]], (1.08, 0.02, 0.05))
    assert polyline["exact_port_to_port"] is True
    assert polyline["polyline_m"][0] == [0.02, 0.02, 0.05]
    assert polyline["polyline_m"][-1] == [1.08, 0.02, 0.05]
    allowance = {"host_global_id": "duct", "cell_center_m": [0.05, 0.05, 0.05]}
    assert point_in_allowance_cell(polyline["polyline_m"][0], allowance)
    assert not point_in_allowance_cell([1.05, 0.05, 0.05], allowance)
    assert classify_proximity(
        distance_m=0.0, required_separation_m=0.105, enclosed=False,
        obstacle_is_boundary_host=True, sample_inside_boundary_allowance_cell=True,
        measurement="MESH_SURFACE_DISTANCE",
    ) == BOUNDARY_HOST_CONNECTION_CONTACT
    assert classify_proximity(
        distance_m=0.0504, required_separation_m=0.105, enclosed=False,
        obstacle_is_boundary_host=True, sample_inside_boundary_allowance_cell=False,
        measurement="MESH_SURFACE_DISTANCE",
    ) == CLEARANCE_VIOLATION
    assert classify_proximity(
        distance_m=0.2, required_separation_m=0.105, enclosed=True,
        obstacle_is_boundary_host=False, sample_inside_boundary_allowance_cell=False,
        measurement="MESH_SURFACE_DISTANCE",
    ) == CONFIRMED_GEOMETRIC_COLLISION
    assert classify_proximity(
        distance_m=0.2, required_separation_m=0.105, enclosed=False,
        obstacle_is_boundary_host=False, sample_inside_boundary_allowance_cell=False,
        measurement="MESH_SURFACE_DISTANCE",
    ) == SEPARATED


def test_request_selection_is_reproducible_and_skips_the_rejected_heating_identity():
    heating_rejected = ["pipe-b", "pipe-a"]
    previews = [
        _preview("heating", heating_rejected, 30),
        _preview("heating", ["pipe-c"], 12),
        _preview("heating", ["pipe-d"], 4, distance=1.0),
        _preview("ventilation", ["duct-z"], 1),
        _preview("ventilation", ["duct-a"], 8),
    ]
    first = phase11b_rank(previews, "heating", skip_replaced=(heating_rejected,))
    second = phase11b_rank(previews, "heating", skip_replaced=(["pipe-a", "pipe-b"],))
    assert [item["replaced_host_global_ids"] for item in first] == [item["replaced_host_global_ids"] for item in second]
    assert first[0]["replaced_host_global_ids"] == ["pipe-c"]
    ventilation = phase11b_rank(previews, "ventilation", prefer_replaced=["duct-z"])
    assert ventilation[0]["replaced_host_global_ids"] == ["duct-z"]
    repeated = phase11b_rank(previews, "ventilation", prefer_replaced=["duct-z"])
    assert [item["replaced_host_global_ids"] for item in ventilation] == [item["replaced_host_global_ids"] for item in repeated]


def test_a_found_path_is_acceptable_only_after_mesh_separation():
    assert search_state(astar_invoked=True, path_found=True, endpoint_statuses=[]) == SEARCH_PATH_FOUND
    assert search_state(astar_invoked=True, path_found=False, endpoint_statuses=[]) == SEARCH_NO_PATH
    assert geometric_state("CLEARANCE_VIOLATION", True) == CLEARANCE_VIOLATION
    assert geometric_state("MESH_SEPARATED_BROAD_PHASE_CANDIDATES", True) == PHYSICALLY_ACCEPTABLE
    assert geometric_state("ZERO_BROAD_PHASE_CANDIDATES", True) == GEOMETRIC_VALIDATION_INCOMPLETE
    assert geometric_state("CONFIRMED_MESH_INTERSECTION", True) == CONFIRMED_GEOMETRIC_COLLISION
    assert geometric_state("CLEARANCE_VIOLATION", False) is None


def test_three_routes_must_share_a_roi_before_coordination():
    def item(name, origin):
        return {"request_id": name, "roi_bounds_m": [origin, origin + 3, 0, 3, 0, 3]}
    assert shared_roi_group([item("a", 0), item("b", 1), item("c", 1.5)]) == ["a", "b", "c"]
    assert shared_roi_group([item("a", 0), item("b", 10), item("c", 20)]) is None
