import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controlled_mad import api
from controlled_mad.scheduler import Planner
from controlled_mad.runner import run_main

ROOT = Path(__file__).resolve().parents[1]
COMMENT = ('Support A: The supplied observation supports this judgment.\n\n'
           'Conclusion: The supplied claim is supported.\n\n'
           'Limitation: This judgment is limited to the supplied observation.\n\n'
           'Support B: The supplied observation is relevant to this claim.')


class CompleteMatrix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT.parent / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = json.loads((ROOT / 'configs/demo.json').read_text(encoding='utf-8'))
        config.update(complete_matrix=True, material_policy='strict_v10', material_gate=True,
                      material_audit=True, audit_model='mock', repeats=1,
                      interventions=[], diagnostics={'oo': {'enabled': False, 'repeats': 1}},
                      max_generation_attempts=2)
        items = [{'dataset_id': 'pilot-mock', 'item_id': f'pilot-{i:02d}', 'claim': 'Synthetic claim.',
                  'evidence': ['Synthetic evidence.'], 'labels': ['A', 'B', 'C', 'D'], 'gold': 'A'}
                 for i in range(10)]
        data = self.root / 'items.jsonl'
        data.write_text(''.join(json.dumps(row)+'\n' for row in items), encoding='utf-8')
        config['dataset']['source'] = str(data)
        scenario = {'items': {row['item_id']: {'comment': COMMENT, 's0': ['A']*3} for row in items},
                    'overrides': [{'match': {'stage': 'generate', 'operation': op}, 'text': json.dumps({
                        'label': 'A', 'comment': COMMENT.replace('Support A: ', f'Support A: {style} ')})}
                                  for op, style in [('ethos', 'Carefully,'), ('logos', 'Accordingly,'), ('pathos', 'Consider:')]]}
        self.scenario = self.root / 'scenario.json'
        self.scenario.write_text(json.dumps(scenario), encoding='utf-8')
        config['mock_scenario'] = str(self.scenario)
        config['budget']['stage_calls']['audit'] = 1000
        self.config_value = config
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(config), encoding='utf-8')

    def test_ten_items_have_exactly_160_real_terminal_outputs_and_resume(self):
        store = api.create_run(self.config, 'pilot', self.root)
        api.execute_ready(store, max_calls=21, concurrency=3, item_pipeline=True)
        before = set(store.selection())
        starts = []
        api.execute_ready(api.open_run('pilot', self.root), concurrency=3,
                          progress=lambda u: starts.append(u['task_id']) if u['event']=='start' else None)
        self.assertTrue(before.isdisjoint(starts))
        from controlled_mad.matrix import result_matrix, matrix_summary
        graph = Planner(api.open_run('pilot', self.root), persist=False).build()
        rows = result_matrix(store, graph)
        self.assertEqual(len(rows), 160)
        self.assertTrue(all(row['status']=='complete' for row in rows))
        self.assertEqual(len({(r['item_id'], r['variant'], r['protocol']) for r in rows}), 160)
        self.assertEqual({r['round'] for r in rows if r['protocol']=='I'}, {1})
        self.assertEqual({r['round'] for r in rows if r['protocol']=='M'}, {3})
        self.assertTrue(all(len(r['votes'])==3 for r in rows))
        self.assertEqual(matrix_summary(rows)['complete_items'], 10)
        self.assertEqual(api.status(store)['progress']['items']['complete'], 10)
        for item in range(10):
            texts = [COMMENT] + [m['rewritten'] for m in graph.materials if m['item_id']==f'pilot-{item:02d}']
            self.assertEqual(len(set(texts)), 8)
        self.assertFalse(any(t.stage=='annotate' or t.context.get('protocol')=='I' for t in graph.tasks.values()))
        report = api.analyze_and_export(store)
        self.assertIn('result_matrix', report['exports'])
        api.verify_run(store)
        with patch('controlled_mad.backends.mock.MockBackend.generate', side_effect=AssertionError('duplicate')):
            self.assertEqual(run_main(self.config, 'pilot', self.root)['executions'][0]['executed'], 0)

    def test_failed_material_blocks_whole_item_and_is_not_complete(self):
        config = copy.deepcopy(self.config_value)
        config['subset'] = {'item_ids': ['pilot-00']}
        self.config.write_text(json.dumps(config), encoding='utf-8')
        scenario = json.loads(self.scenario.read_text(encoding='utf-8'))
        scenario['overrides'].insert(0, {'match': {'stage': 'generate', 'operation': 'ethos'},
                                        'text': json.dumps({'label':'A', 'comment':COMMENT})})
        self.scenario.write_text(json.dumps(scenario), encoding='utf-8')
        result = run_main(self.config, 'missing', self.root, item_pipeline=True)
        self.assertEqual(result['phase'], 'incomplete_matrix')
        store = api.open_run('missing', self.root)
        self.assertEqual(api.status(store)['progress']['items']['complete'], 0)
        self.assertFalse(any(t.stage.startswith('T') for t in Planner(store, persist=False).build().tasks.values()))
        self.assertFalse((store.path/'inputs'/'material-freeze.json').exists())
        self.assertEqual(len([t for t in store.all('tasks') if t['stage']=='generate' and t['context']['operation']=='ethos']), 2)

    def test_bad_source_structure_fails_before_reordering(self):
        from controlled_mad.matrix import structured_blocks
        from controlled_mad.material_checks import reorder
        with self.assertRaises(ValueError): structured_blocks('No real limitation or conclusion block.')
        variants = {reorder(COMMENT, structured_blocks(COMMENT), op) for op in
                    ('conclusion_front', 'conclusion_back', 'support_first', 'limitation_first')}
        self.assertEqual(len(variants | {COMMENT}), 5)
        from controlled_mad.matrix import reorder_structured
        from collections import Counter
        for operation in ('conclusion_front','conclusion_back','support_first','limitation_first'):
            rewritten = reorder_structured(COMMENT, operation)
            self.assertEqual(Counter(rewritten.split('\n\n')), Counter(COMMENT.split('\n\n')))

    def test_complete_matrix_rejects_incompatible_protocol(self):
        from controlled_mad.config import validate_config
        for change in ({'repeats': 3}, {'operations': ['ethos']}, {'material_audit': False}):
            config = {**self.config_value, **change}
            with self.assertRaises(ValueError): validate_config(config)

    def one_item_scenario(self):
        config = {**self.config_value, 'subset': {'item_ids': ['pilot-00']}}
        self.config.write_text(json.dumps(config), encoding='utf-8')
        return json.loads(self.scenario.read_text(encoding='utf-8'))

    def test_generation_repairs_no_change_and_preserves_serial_resume_identity(self):
        scenario = self.one_item_scenario()
        scenario['overrides'].insert(0, {'match': {'stage': 'generate', 'operation': 'ethos', 'generation': 1},
                                        'text': json.dumps({'label':'A', 'comment':COMMENT})})
        self.scenario.write_text(json.dumps(scenario), encoding='utf-8')
        result = run_main(self.config, 'repair', self.root)
        self.assertEqual(result['phase'], 'completed')
        store = api.open_run('repair', self.root)
        tasks = [t for t in store.all('tasks') if t['stage']=='generate' and t['context']['operation']=='ethos']
        self.assertEqual(len(tasks), 2)
        self.assertIn('R 与 O 完全相同', next(t for t in tasks if t['context']['generation']==2)['context']['previous_failure'])
        resumed = api.create_run(self.config, 'resumed', self.root)
        api.execute_ready(resumed, concurrency=3, max_calls=7)
        api.execute_ready(api.open_run('resumed',self.root), concurrency=3)
        for collection in ('tasks','materials','parses','snapshots','comparisons','messages','deliveries','results'):
            self.assertEqual(store.ids(collection), resumed.ids(collection), collection)

    def test_audit_failure_and_duplicate_are_never_complete(self):
        scenario = self.one_item_scenario()
        scenario['items']['pilot-00']['audits'] = {'conclusion_front':'fail'}
        scenario['overrides'][1]['text'] = scenario['overrides'][0]['text']
        self.scenario.write_text(json.dumps(scenario), encoding='utf-8')
        result = run_main(self.config, 'reject', self.root)
        self.assertEqual(result['phase'], 'incomplete_matrix')
        self.assertEqual(result['matrix']['summary']['complete_outputs'], 0)
        graph = Planner(api.open_run('reject',self.root),persist=False).build()
        bad = {m['operation']:m for m in graph.materials if m['status']!='eligible'}
        self.assertIn('conclusion_front',bad)
        self.assertIn('logos',bad)
        self.assertIn('重复',bad['logos']['reason'])

    def test_unstructured_source_is_invalid_not_an_eligible_fake_block(self):
        scenario = self.one_item_scenario()
        scenario['items']['pilot-00']['comment'] = 'This original has no independent limitation.'
        self.scenario.write_text(json.dumps(scenario), encoding='utf-8')
        result = run_main(self.config, 'source-invalid', self.root)
        self.assertEqual(result['phase'], 'needs_repair')
        self.assertEqual(result['status']['progress']['items']['complete'], 0)
        self.assertEqual(result['status']['invalid'],3)

    def test_replaced_invalid_generation_does_not_block_complete_results(self):
        scenario = self.one_item_scenario()
        scenario['overrides'].insert(0, {'match': {'stage': 'generate', 'operation': 'ethos', 'generation': 1},
                                        'text': 'not valid JSON'})
        self.scenario.write_text(json.dumps(scenario), encoding='utf-8')
        result = run_main(self.config, 'replaced-invalid', self.root)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(result['status']['progress']['items']['complete'], 1)
        self.assertEqual(result['status']['invalid'],0)
        self.assertEqual(result['status']['superseded_material_invalid'],1)
        self.assertEqual(result['status']['progress']['settled'],result['status']['progress']['planned'])
