"""Tests for local IFC endpoint qualification and mesh-versus-AABB evidence."""

from src.endpoint_grid import assemble_physical_polyline, polyline_metrics
from src.endpoint_qualification import (
    REJECTED_EMBEDDED,
    QUALIFIED_BOUNDARY_ALLOWANCE,
    QUALIFIED_LOCAL_REFINEMENT,
    decide_terminal,
    execution_status_flags,
)
from src.external_ifc_routing import obstacle_decision
from src.digitalhub_port_pilot import pilot_sort_key
from src.ifc_mesh_section import mesh_facts, triangles_from_mesh


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


def test_boundary_allowance_is_only_the_identified_host():
    decision = decide_terminal(
        point_m=(0.0, 0.0, 0.1),
        cell_index=(1, 2, 3),
        cell_center_m=(0.05, 0.05, 0.15),
        cell_blocked=True,
        outside_grid=False,
        boundary_host_global_id="radiator",
        blockers=[
            {"global_id": "radiator", "ifc_class": "IfcSpaceHeater", "file_role": "heating"},
            {"global_id": "other-pipe", "ifc_class": "IfcPipeSegment", "file_role": "heating"},
        ],
        mesh_facts_by_id={
            "radiator": {"resolved": True, "port_enclosed": True, "port_surface_distance_m": 0.0, "other_enclosed": True, "other_on_surface": False, "other_surface_distance_m": 0.02},
            "other-pipe": {"resolved": True, "port_enclosed": False, "port_surface_distance_m": 1.0, "other_enclosed": False, "other_on_surface": False, "other_surface_distance_m": 1.0},
        },
    )
    assert decision["qualified"]
    assert decision["status"] == QUALIFIED_BOUNDARY_ALLOWANCE
    assert decision["allowance"]["host_global_id"] == "radiator"
    assert decision["allowance"]["whole_host_excluded"] is False
    assert decision["allowance"]["discipline_or_system_excluded"] is False
    assert decision["immutable_point_m"] == [0.0, 0.0, 0.1]
    assert "other-pipe" in decision["false_aabb_obstacle_ids"]
    assert obstacle_decision("IfcPipeSegment", "heating", "other-pipe", {"radiator"}) == "RETAINED_MEP_OBSTACLE"
    assert obstacle_decision("IfcSlab", "architecture", "slab", {"radiator"}) == "ARCHITECTURAL_OBSTACLE"


def test_slab_aabb_without_mesh_enclosure_is_local_refinement():
    slab = _box(0.0, 1.0, 0.0, 1.0, -0.37, -0.15)
    point = (0.2, 0.2, -0.12)
    center = (0.25, 0.25, -0.10)
    facts = mesh_facts(point, center, slab)
    assert facts["port_enclosed"] is False
    decision = decide_terminal(
        point_m=point,
        cell_index=(2, 2, 1),
        cell_center_m=center,
        cell_blocked=True,
        outside_grid=False,
        boundary_host_global_id="pipe",
        blockers=[{"global_id": "slab", "ifc_class": "IfcSlab", "file_role": "architecture"}],
        mesh_facts_by_id={"slab": facts},
    )
    assert decision["status"] == QUALIFIED_LOCAL_REFINEMENT
    assert decision["release_cell"] is True
    assert decision["immutable_point_m"][2] == -0.12


def test_mesh_enclosure_rejects_a_physically_blocked_endpoint():
    floor = _box(0.0, 2.0, 0.0, 2.0, -0.15, 0.0)
    point = (0.5, 0.5, -0.12)
    facts = mesh_facts(point, (0.55, 0.55, -0.15), floor)
    assert facts["port_enclosed"] is True
    decision = decide_terminal(
        point_m=point,
        cell_index=(5, 5, 1),
        cell_center_m=(0.55, 0.55, -0.15),
        cell_blocked=True,
        outside_grid=False,
        boundary_host_global_id="pipe",
        blockers=[{"global_id": "floor", "ifc_class": "IfcSlab", "file_role": "architecture"}],
        mesh_facts_by_id={"floor": facts},
        openings_by_host={"floor": [{"opening_global_id": "void", "relation_global_id": "rel"}]},
    )
    assert decision["status"] == REJECTED_EMBEDDED
    assert decision["qualified"] is False
    assert decision["opening_notes"][0]["host_mesh_encloses_port"] is True
    assert "do not clear" in decision["opening_notes"][0]["effect"]


def test_opening_already_removed_from_host_mesh_is_not_enclosure():
    # Two separated boxes leave x=1.5 outside both solids. An AABB from 0 to 3 would still contain it.
    left = _box(0.0, 1.0, 0.0, 1.0, 0.0, 1.0)
    right_vertices = [
        2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 3.0, 1.0, 0.0, 2.0, 1.0, 0.0,
        2.0, 0.0, 1.0, 3.0, 0.0, 1.0, 3.0, 1.0, 1.0, 2.0, 1.0, 1.0,
    ]
    right_faces = [
        0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1,
        1, 5, 6, 1, 6, 2, 2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0,
    ]
    import numpy as np
    triangles = np.concatenate([left, triangles_from_mesh(right_vertices, right_faces)])
    facts = mesh_facts((1.5, 0.5, 0.5), (1.5, 0.5, 0.5), triangles)
    assert facts["port_enclosed"] is False
    decision = decide_terminal(
        point_m=(1.5, 0.5, 0.5),
        cell_index=(1, 1, 1),
        cell_center_m=(1.5, 0.5, 0.5),
        cell_blocked=True,
        outside_grid=False,
        boundary_host_global_id="pipe",
        blockers=[{"global_id": "host", "ifc_class": "IfcSlab", "file_role": "architecture"}],
        mesh_facts_by_id={"host": facts},
        openings_by_host={"host": [{"opening_global_id": "hole", "relation_global_id": "rel"}]},
    )
    assert decision["qualified"] is True
    assert decision["opening_notes"][0]["host_mesh_encloses_port"] is False


def test_physical_polyline_keeps_exact_ports_and_vertical_travel():
    start = (0.0, 0.0, 0.10)
    goal = (1.0, 0.0, -0.12)
    route = assemble_physical_polyline(start, [(0.05, 0.0, 0.15), (0.95, 0.0, 0.15), (0.95, 0.0, -0.15)], goal)
    metrics = polyline_metrics(route["polyline_m"])
    assert route["exact_port_to_port"]
    assert route["polyline_m"][0] == list(start)
    assert route["polyline_m"][-1] == list(goal)
    assert metrics["orthogonal"]
    assert metrics["vertical_travel_m"] >= 0.22
    assert metrics["length_m"] > abs(goal[0] - start[0])


def test_candidate_order_is_deterministic_and_records_rejection_inputs():
    first = {
        "straight_line_m": 4.0, "port_inside_slab_aabb": False, "same_system": True, "same_storey": True,
        "replaced_host_global_ids": ["b"], "boundary_host_global_ids": ["a", "c"], "discipline": "heating",
    }
    second = {
        "straight_line_m": 4.0, "port_inside_slab_aabb": True, "same_system": True, "same_storey": True,
        "replaced_host_global_ids": ["a"], "boundary_host_global_ids": ["b", "d"], "discipline": "heating",
    }
    assert pilot_sort_key(first) < pilot_sort_key(second)
    assert sorted([second, first], key=pilot_sort_key)[0]["replaced_host_global_ids"] == ["b"]


def test_covering_aabb_does_not_occupy_a_cell_centre_away_from_the_shell():
    from src.ifc_mesh_section import cells_touching_mesh
    # A horizontal quad at z=0. A cell centre 25 mm above it is inside a thick AABB but not the shell.
    triangles = triangles_from_mesh(
        [0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 2.0, 2.0, 0.0, 0.0, 2.0, 0.0],
        [0, 1, 2, 0, 2, 3],
    )
    import numpy as np
    points = np.array([[1.0, 1.0, 0.025], [1.0, 1.0, 0.0]])
    occupied = cells_touching_mesh(points, triangles, contact_m=1e-3)
    assert occupied.tolist() == [False, True]


def test_cli_launch_is_not_the_same_fact_as_astar_invocation():
    launched = execution_status_flags(cli_completed=True, astar_invoked=False, path_found=False, validation_label="NOT_RUN_ENDPOINT_INVALID")
    searched = execution_status_flags(cli_completed=True, astar_invoked=True, path_found=True, validation_label="MESH_SEPARATED_BROAD_PHASE_CANDIDATES")
    assert launched["cli_completed"] is True
    assert launched["a_star_executed"] is False
    assert searched["astar_invoked"] is True
    assert searched["path_found"] is True
    assert searched["geometric_validation_status"] != "NOT_RUN_ENDPOINT_INVALID"
