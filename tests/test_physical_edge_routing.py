from pathlib import Path
import json, unittest
from src.config import load_building_config
from src.demand import generate_benchmark_cases, load_service_definitions
from src.endpoints import benchmark_case_to_routing_problem
from src.topology_planner import TopologyCandidateKind, plan_topology_candidates
from src.spatial_evidence import build_synthetic_spatial_evidence, synthetic_frame_id
from src.clearance_policy import benchmark_v1_policy
from src.junction_placement import propose_junction_placements
from src.physical_edge_routing import PhysicalEdgeRouteStatus, route_physical_edge
R=Path(__file__).resolve().parents[1]
class PhysicalEdgeRoutingTests(unittest.TestCase):
 @classmethod
 def setUpClass(c):
  c.s=load_service_definitions(R/'config/mep_systems.json');c.case=generate_benchmark_cases(R/'experiments/scenarios',R/'experiments/demands',R/'config/mep_systems.json')[0];m=json.loads((R/'experiments/scenarios/manifest.json').read_text());c.cfg=load_building_config(R/'experiments/scenarios'/next(x['config_file'] for x in m['scenarios'] if x['scenario_id']==c.case['scenario_id']));c.p=benchmark_case_to_routing_problem(c.case,c.s);c.e=build_synthetic_spatial_evidence(c.case['scenario_id'],c.cfg,c.s);c.pol=benchmark_v1_policy(c.cfg,c.s);c.f=synthetic_frame_id(c.case['scenario_id'],c.cfg)
 def test_direct_path_is_real_deterministic_and_free(self):
  cand=plan_topology_candidates(self.p)[0];edge=cand.demand.topology.edges[0];a=route_physical_edge(cand,self.e,self.pol,edge.edge_id,endpoint_frame_id=self.f);b=route_physical_edge(cand,self.e,self.pol,edge.edge_id,endpoint_frame_id=self.f)
  self.assertEqual(a,b);self.assertIs(a.status,PhysicalEdgeRouteStatus.ROUTED);self.assertEqual(a.grid_path_length_m,(len(a.grid_cells)-1)*self.e.grid_for(a.system).spec.voxel_size_m);self.assertTrue(all(self.e.grid_for(a.system).cells[self.e.grid_for(a.system).spec.linear_index(*x)]==0 for x in a.grid_cells));self.assertTrue(all(sum(abs(x[i]-y[i]) for i in range(3))==1 for x,y in zip(a.grid_cells,a.grid_cells[1:])))
 def test_shared_main_requires_explicit_proposal_and_limit_is_honest(self):
  cand=next(x for x in plan_topology_candidates(self.p) if x.kind is TopologyCandidateKind.SHARED_MAIN);placed=propose_junction_placements(cand,self.e,endpoint_frame_id=self.f);j=placed.junctions[0];missing=route_physical_edge(cand,self.e,self.pol,j.main_edge_id,endpoint_frame_id=self.f);ok=route_physical_edge(cand,self.e,self.pol,j.main_edge_id,endpoint_frame_id=self.f,junction_proposals=(j.proposals[0],));limited=route_physical_edge(cand,self.e,self.pol,j.main_edge_id,endpoint_frame_id=self.f,junction_proposals=(j.proposals[0],),max_expansions=1)
  self.assertEqual(missing.reason,'JUNCTION_PROPOSAL_MISSING');self.assertIs(ok.status,PhysicalEdgeRouteStatus.ROUTED);self.assertIs(limited.status,PhysicalEdgeRouteStatus.SEARCH_LIMIT_REACHED);self.assertIn('FITTING_GEOMETRY_UNEVALUATED',ok.to_canonical_json())
