from tests.test_physical_edge_routing import PhysicalEdgeRoutingTests
from src.topology_planner import TopologyCandidateKind,plan_topology_candidates
from src.junction_placement import propose_junction_placements
from src.physical_network_routing import route_shared_main_group
class PhysicalNetworkRoutingTests(PhysicalEdgeRoutingTests):
 def test_complete_shared_group(self):
  c=next(x for x in plan_topology_candidates(self.p) if x.kind is TopologyCandidateKind.SHARED_MAIN);pl=propose_junction_placements(c,self.e,endpoint_frame_id=self.f);j=pl.junctions[0];r=route_shared_main_group(c,self.e,self.pol,pl,junction_node_id=j.junction_node_id,endpoint_frame_id=self.f)
  self.assertTrue(r.complete);self.assertEqual(len(r.branches),len(j.branch_edge_ids));self.assertEqual({x[0] for x in r.connection_paths},set(j.connection_ids));self.assertIn('collision_free_claimed',r.to_canonical_json())
