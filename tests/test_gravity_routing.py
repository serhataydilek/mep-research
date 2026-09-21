from pathlib import Path
import unittest
from src.gravity_routing import GRAVITY_OFFSETS, endpoint_candidates, gravity_route, run_gravity_benchmark
from src.constructability import drainage_gravity_metrics
from src.voxel import FREE, BLOCKED_PRIOR_ROUTE, GridSpec, OccupancyGrid

ROOT=Path(__file__).resolve().parents[1]
class GravityTests(unittest.TestCase):
 def synthetic(self):
  spec=GridSpec(.1,0,0,0,8,3,6)
  return OccupancyGrid('x','drainage',spec,bytearray(spec.total_cell_count),(0,0,0,0,0,0),{})
 def anchor(self,i,j,k,grid):
  x,y,z=grid.spec.cell_center(i,j,k); return {'anchor_id':'terminal/drainage/0','x_m':x,'y_m':y,'z_m':z}
 def test_metrics_and_candidates(self):
  self.assertTrue(all(offset!=(0,0,1) for offset in GRAVITY_OFFSETS)); self.assertIn((0,0,-1),GRAVITY_OFFSETS)
  route={'path_found':True,'path_cells':[[0,0,2],[1,0,2],[2,0,1]]}
  self.assertTrue(drainage_gravity_metrics(route,.1,.01)['drainage_gravity_pass'])
  self.assertFalse(drainage_gravity_metrics({'path_found':True,'path_cells':[[0,0,2],[1,0,2]]},.1,.01)['minimum_slope_pass'])
  grid=self.synthetic(); a=self.anchor(2,1,3,grid); candidates=endpoint_candidates(grid,a)
  self.assertEqual(candidates,endpoint_candidates(grid,a))
  self.assertTrue(all(c['grid_index'][:2]==[2,1] and grid.state_at(*c['grid_index'])==FREE and c['snap_distance_m']<=.2+1e-9 for c in candidates))
  self.assertTrue(any(c['elevation_adjustment_m']<0 for c in candidates))
  grid.cells[grid.spec.linear_index(2,1,3)]=BLOCKED_PRIOR_ROUTE
  self.assertNotIn([2,1,3],[c['grid_index'] for c in endpoint_candidates(grid,a)])
 def test_gravity_pair_rejections_and_success(self):
  grid=self.synthetic(); start=self.anchor(0,1,4,grid); goal=self.anchor(5,1,1,grid)
  selected,reason=gravity_route(grid,start,goal,.01); self.assertIsNone(reason); self.assertTrue(selected[-2]['drainage_gravity_pass'])
  selected,reason=gravity_route(grid,self.anchor(0,1,1,grid),self.anchor(5,1,4,grid),.01)
  if selected is not None:
   self.assertGreaterEqual(selected[6]['selected_voxel_center_z'],selected[7]['selected_voxel_center_z'])
  else:
   self.assertIn(reason,{'INSUFFICIENT_ENDPOINT_ELEVATION_DROP','NO_MONOTONIC_GRAVITY_PATH','GRAVITY_SLOPE_NOT_SATISFIED'})
 def test_no_uphill_and_benchmark(self):
  self.assertNotIn((0,0,1),GRAVITY_OFFSETS)
  r=run_gravity_benchmark(ROOT/'experiments/scenarios',ROOT/'experiments/demands',ROOT/'config/mep_systems.json',ROOT/'config/constructability.json')
  routes=[x for c in r['cases'] for x in c['routes']]
  self.assertEqual(len(routes),48)
  for route in routes:
   if route['path_found']:
    self.assertTrue(route['gravity']['drainage_gravity_pass'])
    self.assertEqual(route['gravity']['uphill_step_count'],0)
  by_key={}
  for case in r['cases']:
   for route in case['routes']:
    key=(case['scenario_id'],route['connection_id']); value=(route['path_found'],route.get('selected_start'),route.get('selected_end'),route['path_cells'],route['gravity'])
    self.assertEqual(by_key.setdefault(key,value),value)
