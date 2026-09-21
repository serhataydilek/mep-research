from pathlib import Path
import unittest
from src.gravity_routing import GRAVITY_OFFSETS, run_gravity_benchmark

ROOT=Path(__file__).resolve().parents[1]
class GravityTests(unittest.TestCase):
 def test_no_uphill_and_benchmark(self):
  self.assertNotIn((0,0,1),GRAVITY_OFFSETS)
  r=run_gravity_benchmark(ROOT/'experiments/scenarios',ROOT/'experiments/demands',ROOT/'config/mep_systems.json',ROOT/'config/constructability.json')
  routes=[x for c in r['cases'] for x in c['routes']]
  self.assertEqual(len(routes),48)
  for route in routes:
   if route['path_found']:
    self.assertTrue(route['gravity']['drainage_gravity_pass'])
    self.assertEqual(route['gravity']['uphill_step_count'],0)
