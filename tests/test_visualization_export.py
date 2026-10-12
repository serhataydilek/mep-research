import json
import tempfile
from pathlib import Path
import unittest

from src.junction_placement import propose_junction_placements
from src.physical_network_routing import route_shared_main_group
from src.topology_planner import TopologyCandidateKind, plan_topology_candidates
from src.visualization_export import export_single_system_fixture, export_viewer_fixture, fixture_bounds, validate_viewer_fixture
from tests.test_physical_edge_routing import PhysicalEdgeRoutingTests


class VisualizationExportTests(PhysicalEdgeRoutingTests):
 def _fixture(self):
  candidate=next(item for item in plan_topology_candidates(self.p) if item.kind is TopologyCandidateKind.SHARED_MAIN)
  placement=propose_junction_placements(candidate,self.e,endpoint_frame_id=self.f);junction=placement.junctions[0]
  route=route_shared_main_group(candidate,self.e,self.pol,placement,junction_node_id=junction.junction_node_id,endpoint_frame_id=self.f)
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'fixture.json';data=export_viewer_fixture(route,self.e,candidate,path)
   self.assertEqual(data,json.loads(path.read_text()))
  return data
 def test_fixture_schema_routes_and_identity(self):
  data=self._fixture();main,branches=validate_viewer_fixture(data)
  self.assertEqual(main['status'],'ROUTED');self.assertTrue(all(item['status']=='ROUTED' for item in branches));self.assertEqual(len(branches),3);self.assertEqual(len({main['edge_id'],*(item['edge_id'] for item in branches)}),4)
  self.assertEqual(len(data['endpoints']),4);self.assertEqual(data['scenario_id'],'s01')
 def test_fixture_bounds_include_real_routes_and_obstacles(self):
  data=self._fixture();minimum,maximum=fixture_bounds(data)
  self.assertLess(minimum[0],maximum[0]);self.assertLess(minimum[1],maximum[1]);self.assertLess(minimum[2],maximum[2])
  self.assertTrue(all(minimum[i]<=point[i]<=maximum[i] for route in (data['network']['main'],*data['network']['branches']) for point in route['world_centerline_m'] for i in range(3)))
 def test_invalid_or_missing_geometry_is_diagnostic(self):
  with self.assertRaisesRegex(ValueError,'requires one main'): validate_viewer_fixture({'schema_version':1,'units':'m','obstacles':[],'network':{}})
  with self.assertRaisesRegex(ValueError,'invalid or empty geometry'): fixture_bounds({'schema_version':1,'units':'m','obstacles':[],'endpoints':[{'world_m':[0,0]}],'network':{'main':{'edge_id':'m','world_centerline_m':[[0,0,0],[1,0,0]]},'branches':[]}})
 def test_viewer_has_visible_error_and_resize_paths(self):
  text=(Path(__file__).resolve().parents[1]/'viewer'/'index.html').read_text(encoding='utf-8')
  for required in ('response.ok','catch(error)','function fit(bounds)','addEventListener(\'resize\'',"requestAnimationFrame(animate)",'camera.up.set(0,0,1)'):
   self.assertIn(required,text)
 def test_viewer_reads_only_exported_route_fields(self):
  text=(Path(__file__).resolve().parents[1]/'viewer'/'index.html').read_text(encoding='utf-8');route=self._fixture()['network']['main']
  self.assertNotIn('length_m??',text)
  for field in ('grid_path_length_m','world_centerline_m','edge_id','system','status'):
   self.assertIn(field,route);self.assertIn(field,text)
 def test_committed_single_system_fixture_is_reproducible(self):
  committed=Path(__file__).resolve().parents[1]/'viewer'/'viewer_fixture.json'
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'fixture.json';export_single_system_fixture(path)
   self.assertEqual(path.read_bytes(),committed.read_bytes())
