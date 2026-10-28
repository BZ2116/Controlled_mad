import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from controlled_mad.config import validate_config
from controlled_mad.visibility import visibility_matrix
from controlled_mad.replication import replication_plan
from controlled_mad.progress import progress_snapshot

ROOT = Path(__file__).resolve().parents[1]


class Extensions(unittest.TestCase):
    def base(self):
        return json.loads((ROOT / 'configs/demo.json').read_text(encoding='utf-8'))

    def test_inference_and_coding_parameters_cannot_silently_misspell(self):
        for settings in ({'resamples': 0, 'seed': 1, 'alpha': .05},
                         {'resamples': 199, 'seed': True, 'alpha': .05},
                         {'resamples': 199, 'seed': 1, 'alpha': float('nan')}):
            config = self.base(); config['inference'] = settings
            with self.assertRaises(ValueError): validate_config(config)
        config = self.base(); config['coding'] = {'size': 0, 'seed': 42}
        with self.assertRaises(ValueError): validate_config(config)
        config = self.base(); config['coding'] = {'size': 2, 'seed': 42}
        config['inference'] = {'resamples': 199, 'seed': 42, 'alpha': .05}
        validate_config(config)

    def test_visibility_suppresses_only_target_edge_and_preserves_t1(self):
        config = self.base()
        matrices = visibility_matrix(config)
        blocked = next(row for row in matrices if row['branch'] == 'A_to_B_T3' and row['round'] == 3 and row['recipient'] == 'B')
        self.assertEqual(blocked['blocked_new_sources'], ['A'])
        self.assertEqual(blocked['new_sources'], ['S'])
        first = next(row for row in matrices if row['branch'] == 'A_to_B_T3' and row['round'] == 1 and row['recipient'] == 'B')
        self.assertEqual(first['new_sources'], ['S', 'A'])
        self.assertTrue(first['retains_previous_history'])
        independent = next(row for row in matrices if row['branch'] == 'I' and row['round'] == 2 and row['recipient'] == 'A')
        self.assertEqual(independent['new_sources'], [])

    def test_replication_matrix_keeps_disabled_slots_out_of_execution(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent / '.tmp') as tmp:
            matrix = {'slots': [
                {'id': 'primary', 'enabled': True, 'config': str(ROOT / 'configs/demo.json'), 'pending': ''},
                {'id': 'second-model', 'enabled': False, 'config': None, 'pending': 'researcher to lock model'}]}
            path = Path(tmp) / 'matrix.json'; path.write_text(json.dumps(matrix), encoding='utf-8')
            result = replication_plan(path)
            self.assertEqual(len(result['enabled']), 1)
            self.assertEqual(result['pending'][0]['id'], 'second-model')
            matrix['slots'][1]['enabled'] = True
            path.write_text(json.dumps(matrix), encoding='utf-8')
            with self.assertRaises(ValueError): replication_plan(path)

    def test_subset_progress_does_not_wait_for_unplanned_interventions(self):
        config = self.base()
        config['intervention_subset'] = {'size': 2, 'seed': 42}
        records = [{'dataset_id': 'd', 'item_id': str(i)} for i in range(6)]
        def get(collection, name):
            if name == 'items': return {'items': records}
            if name == 'intervention-subset': return {'selected_item_ids': ['0', '1']}
            raise AssertionError(name)
        store = SimpleNamespace(config=config, get=get)
        graph = SimpleNamespace(tasks={}, materials=[])
        self.assertEqual(progress_snapshot(store, graph, {})['planned'], 1086)

