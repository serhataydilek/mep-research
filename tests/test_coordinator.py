from pathlib import Path
import unittest
from src.coordinator import build_pcore_initial_layout, load_coordinator_config, route_non_drainage_with_vertical_candidates, run_pcore_seed_benchmark
from src.demand import generate_benchmark_cases
from src.voxel import build_occupancy_grids

ROOT=Path(__file__).resolve().parents[1]; SCENARIOS=ROOT/'experiments/scenarios'; DEMANDS=ROOT/'experiments/demands'; SYSTEMS=ROOT/'config/mep_systems.json'; CONSTRAINTS=ROOT/'config/constructability.json'; CONFIG=ROOT/'config/coordinator.json'
class CoordinatorTests(unittest.TestCase):
 def test_config_and_seed(self):
  self.assertEqual('P-CORE',load_coordinator_config(CONFIG)['method_id'])
  result=run_pcore_seed_benchmark(SCENARIOS,DEMANDS,SYSTEMS,CONSTRAINTS,CONFIG)
  self.assertEqual(292,sum(c['successful_route_count'] for c in result['cases']))
  self.assertEqual(12,result['c0_summary']['drainage_gravity_compliant_case_count'])
 def test_seed_sources_and_base_immutability(self):
  grids=build_occupancy_grids(SCENARIOS,SYSTEMS); before={k:bytes(v.cells) for k,v in grids.items()}
  seed=build_pcore_initial_layout(SCENARIOS,DEMANDS,SYSTEMS,CONSTRAINTS,grids=grids)
  self.assertEqual(before,{k:bytes(v.cells) for k,v in grids.items()})
  for route in [r for c in seed['cases'] for r in c['routes']]: self.assertEqual('GRAVITY_AWARE_DRAINAGE' if route['system']=='drainage' else 'B0',route['initial_source'])
 def test_non_drainage_candidate_router(self):
  grids=build_occupancy_grids(SCENARIOS,SYSTEMS); case=generate_benchmark_cases(SCENARIOS,DEMANDS,SYSTEMS)[0]; request=next(r for r in case['connection_requests'] if r['system']=='hvac'); anchors={a['anchor_id']:a for a in [*case['egress_anchors'],*case['terminal_anchors']]}
  selected,reason=route_non_drainage_with_vertical_candidates(grids[(case['scenario_id'],'hvac')],anchors[request['start_anchor']],anchors[request['end_anchor']])
  self.assertIsNone(reason); self.assertIsNotNone(selected)
