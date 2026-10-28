import copy
import json
import tempfile
import unittest
from pathlib import Path

from controlled_mad import api
from controlled_mad.config import validate_config
from controlled_mad.scheduler import Planner

ROOT = Path(__file__).resolve().parents[1]


def strict_config():
    config = json.loads((ROOT / 'configs/demo.json').read_text(encoding='utf-8'))
    config.update(material_policy='strict_v10', material_audit=True, audit_model='mock',
                  material_gate=True, operations=['ethos'], interventions=[],
                  diagnostics={'oo': {'enabled': False, 'repeats': 1}})
    config['coding'] = {'size': 2, 'seed': 42}
    config['inference'] = {'resamples': 99, 'seed': 42, 'alpha': .05}
    config['dataset']['source'] = str(ROOT / 'examples/toy_dataset.jsonl')
    config['mock_scenario'] = str(ROOT / 'examples/mock_scenario.json')
    config['budget']['stage_calls']['audit'] = 1000
    return config


class V10Materials(unittest.TestCase):
    def test_gate_freezes_eligibility_before_any_peer_reply(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent / '.tmp') as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps(strict_config()), encoding='utf-8')
            store = api.create_run(path, 'strict', tmp)
            with self.assertRaises(ValueError):
                api.freeze_materials(store)
            api.execute_ready(store)
            graph = Planner(store).build()
            self.assertEqual(len(graph.materials), 6)
            self.assertTrue(all(m['status'] == 'eligible' for m in graph.materials))
            self.assertFalse(any(t.stage == 'T1' for t in graph.tasks.values()))
            frozen = api.freeze_materials(store)
            self.assertEqual(frozen['counts'], {'eligible': 6})
            self.assertEqual(api.freeze_materials(store), frozen)
            api.execute_ready(store)
            self.assertEqual(len(Planner(store).build().comparisons), 6)
            self.assertTrue(all(c['R']['M']['3'] for c in Planner(store).build().comparisons))
            packet = api.export_coding(store)
            self.assertGreater(packet['replies'], 0)
            self.assertTrue(Path(packet['path']).is_file())
            self.assertEqual(api.coding_status(store)['double_coded'], 0)
            report = api.analyze_and_export(store)
            self.assertIn('condition_summaries', report['exports'])
            self.assertIn('inference', report['exports'])
            api.verify_run(store)

    def test_strict_requires_audit_and_allows_it_in_five_round(self):
        config = strict_config()
        validate_config(config)
        bad = copy.deepcopy(config)
        bad['material_audit'] = False
        with self.assertRaises(ValueError):
            validate_config(bad)
        config.update(experiment='rq2_five_round', subset={'size': 2, 'seed': 42})
        config['budget']['stage_calls'].update(T4=1000, T5=1000)
        validate_config(config)

    def test_new_numbers_rejected_even_if_audit_would_pass(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent / '.tmp') as tmp:
            config = strict_config()
            scenario = json.loads(Path(config['mock_scenario']).read_text(encoding='utf-8'))
            scenario['overrides'] = [{'match': {'stage': 'generate', 'item_id': 'toy-001'},
                                      'text': json.dumps({'label': scenario['items']['toy-001']['s0'][0],
                                                          'comment': 'New source https://invented.test and 987654321 observations.'})}]
            scenario_path = Path(tmp) / 'scenario.json'
            scenario_path.write_text(json.dumps(scenario), encoding='utf-8')
            config['mock_scenario'] = str(scenario_path)
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            store = api.create_run(path, 'reject', tmp)
            api.execute_ready(store)
            material = next(m for m in Planner(store).build().materials if m['item_id'] == 'toy-001')
            self.assertEqual(material['status'], 'ineligible')
            self.assertIn('新增', material['reason'])
            self.assertTrue(material['checks']['new_urls'])

