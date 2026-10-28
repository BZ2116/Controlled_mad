import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controlled_mad import api
from controlled_mad.engine import execute_ready
from controlled_mad.scheduler import Planner
from controlled_mad.store import RunStore
from controlled_mad.runner import run_main

ROOT = Path(__file__).resolve().parents[1]


class SinglePass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT.parent / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = json.loads((ROOT / 'configs/demo.json').read_text(encoding='utf-8'))
        config.update(repeats=3, material_gate=True, material_policy='strict_v10',
                      material_audit=True, audit_model='mock', operations=['ethos'],
                      interventions=[], subset={'item_ids': ['toy-001', 'toy-002', 'toy-003']},
                      diagnostics={'oo': {'enabled': False, 'repeats': 1}},
                      inference={'resamples': 99, 'seed': 42, 'alpha': .05})
        config['dataset']['source'] = str(ROOT / 'examples/toy_dataset.jsonl')
        config['mock_scenario'] = str(ROOT / 'examples/mock_scenario.json')
        config['budget']['stage_calls']['audit'] = 100
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(config), encoding='utf-8')

    def test_fresh_plan_has_one_repeat_and_one_independent_round(self):
        store = api.create_run(self.config, 'fresh', self.root)
        execute_ready(store, concurrency=3, item_pipeline=True, single_pass=True)
        graph = Planner(store, persist=False).build()
        debate = [t for t in graph.tasks.values() if t.stage.startswith('T')]
        self.assertEqual({t.context['repeat'] for t in debate}, {0})
        self.assertFalse(any(t.context['protocol'] == 'I' for t in debate))
        self.assertEqual(len(debate), 3 * 2 * 9)
        self.assertTrue(all(set(c['O']['I']) == {'1'} and set(c['R']['I']) == {'1'}
                            for c in graph.comparisons))
        state = api.status(store)
        self.assertEqual(state['progress']['items']['complete'], 3)
        self.assertEqual(state['progress']['settled'], state['progress']['planned'])
        bundle = api.analyze_and_export(store)
        self.assertEqual(bundle['denominators']['complete_comparison_repeats'], 3)
        api.verify_run(store)
        with patch('controlled_mad.backends.mock.MockBackend.generate', side_effect=AssertionError('must resume')):
            self.assertEqual(execute_ready(RunStore(store.path), concurrency=3)['executed'], 0)

    def test_adoption_preserves_completed_and_partial_results_and_separates_analysis(self):
        store = api.create_run(self.config, 'adopt', self.root)
        # One fully completed question with 3 repeats, then a partly executed question.
        execute_ready(store, concurrency=1, item_pipeline=True, max_calls=95)
        Planner(store).build()
        self.assertEqual(api.status(store)['progress']['items']['complete'], 1)
        execute_ready(store, concurrency=1, max_calls=15)
        old = {p: p.read_bytes() for p in store.path.rglob('*') if p.is_file()
               and p.name not in ('.writer.lock', '.resume-checkpoint.json.gz')}
        selected = set(store.selection())
        starts = []
        execute_ready(store, concurrency=3, single_pass=True,
                      progress=lambda u: starts.append(u['task_id']) if u['event'] == 'start' else None)
        self.assertTrue(selected.isdisjoint(starts))
        for p, data in old.items(): self.assertEqual(p.read_bytes(), data, str(p))
        graph = Planner(store, persist=False).build()
        legacy = [c for c in graph.comparisons if c['context']['item_id'] == 'toy-001']
        reduced = [c for c in graph.comparisons if c['context']['item_id'] != 'toy-001']
        self.assertEqual(len(legacy), 3)
        self.assertEqual(len(reduced), 2)
        self.assertTrue(all(set(c['O']['I']) == {'1', '2', '3'} for c in legacy))
        self.assertTrue(all(set(c['O']['I']) == {'1'} for c in reduced))
        from controlled_mad.analysis import analyze
        bundle = analyze(store)
        self.assertEqual({r['design'] for r in bundle['summary_effects']},
                         {'legacy_three_round', 'single_pass_v1'})
        reduced_i = [r for r in bundle['condition_summaries']
                     if r['design'] == 'single_pass_v1' and r['protocol'] == 'I']
        self.assertTrue(reduced_i)
        self.assertEqual({r['round'] for r in reduced_i}, {1})
        self.assertTrue(all(r['is_final'] for r in reduced_i))
        self.assertEqual(api.status(store)['progress']['items']['complete'], 3)
        self.assertEqual(store.config['repeats'], 3)
        api.verify_run(store)

    def test_main_runner_remembers_amendment_and_resume_has_same_content_as_serial(self):
        serial = run_main(self.config, 'serial', self.root, item_pipeline=True, single_pass=True)
        self.assertEqual(serial['phase'], 'completed')
        resumed = api.create_run(self.config, 'resumed', self.root)
        execute_ready(resumed, concurrency=3, item_pipeline=True, single_pass=True, max_calls=8)
        resumed = RunStore(resumed.path)
        execute_ready(resumed, concurrency=3)
        full = RunStore(self.root / 'serial')
        for collection in ('tasks', 'parses', 'snapshots', 'comparisons', 'materials', 'messages', 'deliveries', 'results'):
            self.assertEqual(full.ids(collection), resumed.ids(collection), collection)
        final = run_main(self.config, 'resumed', self.root)
        self.assertEqual(final['phase'], 'completed')
        self.assertEqual(final['executions'][0]['executed'], 0)
