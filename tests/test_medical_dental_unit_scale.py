from src.medical_dental_ifc import bounds
def test_kernel_world_metres_are_not_rescaled_by_project_unit_factor():
    raw=[0.0,0.0,0.0,1000.0,2000.0,3000.0]
    assert bounds(raw)['max_z_m']==3000.0
