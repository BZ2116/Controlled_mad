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


class ItemPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT.parent / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = json.loads((ROOT / 'configs/demo.json').read_text(encoding='utf-8'))
        config.update(material_policy='strict_v10', material_gate=True, material_audit=True,
                      audit_model='mock', operations=['ethos'], interventions=[],
                      subset={'item_ids': ['toy-001', 'toy-002', 'toy-003', 'toy-004']},
                      diagnostics={'oo': {'enabled': False, 'repeats': 1}})
        config['dataset']['source'] = str(ROOT / 'examples/toy_dataset.jsonl')
        config['mock_scenario'] = str(ROOT / 'examples/mock_scenario.json')
        config['budget']['stage_calls']['audit'] = 100
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(config), encoding='utf-8')

    def test_three_items_reach_final_round_before_fourth_is_admitted(self):
        store = api.create_run(self.config, 'pipeline', self.root)
        seen = []
        def observe(update):
            if update['event'] == 'start':
                seen.append((update['item_id'], update['stage']))
        execute_ready(store, concurrency=3, item_pipeline=True, progress=observe)
        fourth = next(i for i, row in enumerate(seen) if row[0] == 'toy-004')
        self.assertTrue(any(stage == 'T3' for _, stage in seen[:fourth]))
        self.assertEqual({item for item, _ in seen[:fourth]}, {'toy-001', 'toy-002', 'toy-003'})
        self.assertEqual(api.status(store)['progress']['items']['complete'], 4)
        self.assertFalse((store.path / 'inputs/material-freeze.json').exists())
        freezes = [key for key in store.ids('inputs') if key.startswith('item-material-freeze-')]
        self.assertEqual(len(freezes), 4)
        api.freeze_materials(store)
        api.verify_run(store)

    def test_adopting_pipeline_keeps_old_objects_and_resumes_without_flag(self):
        store = api.create_run(self.config, 'resume', self.root)
        execute_ready(store, concurrency=3, max_calls=4)
        old = {p: p.read_bytes() for p in store.path.rglob('*') if p.is_file()
               and p.name not in ('.writer.lock','.resume-checkpoint.json.gz')}
        completed = set(store.selection())
        seen = []
        execute_ready(store, concurrency=3, item_pipeline=True, max_calls=2,
                      progress=lambda u: seen.append(u['task_id']) if u['event'] == 'start' else None)
        self.assertTrue(completed.isdisjoint(seen))
        for path, data in old.items():
            self.assertEqual(path.read_bytes(), data, str(path))
        reopened = RunStore(store.path)
        execute_ready(reopened, concurrency=3)
        self.assertEqual(api.status(reopened)['progress']['items']['complete'], 4)
        self.assertEqual(reopened.get('', 'manifest')['config'], store.config)

    def test_changed_item_eligibility_is_rejected_after_freeze(self):
        store = api.create_run(self.config, 'drift', self.root)
        execute_ready(store, concurrency=1, item_pipeline=True, max_calls=5)
        Planner(store).build()
        from controlled_mad.materials import prepare_materials
        def changed(*args):
            records = prepare_materials(*args)
            for record in records:
                if record['item_id'] == 'toy-001': record['material_id'] = 'changed'
            return records
        with patch('controlled_mad.materials.prepare_materials', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '冻结'):
                Planner(store, persist=False).build()

    def test_existing_materials_do_not_eagerly_persist_all_questions_debates(self):
        store = api.create_run(self.config, 'lazy', self.root)
        execute_ready(store, concurrency=3)
        self.assertFalse(any(t['stage']=='T1' for t in store.all('tasks')))
        execute_ready(store, concurrency=3, item_pipeline=True, max_calls=1)
        debate_items={t['context']['item_id'] for t in store.all('tasks') if t['stage']=='T1'}
        self.assertEqual(debate_items, {'toy-001'})

    def test_main_runner_uses_one_pipeline_and_remembers_mode_on_resume(self):
        config=json.loads(self.config.read_text(encoding='utf-8'))
        config['subset']={'item_ids':['toy-001']}
        self.config.write_text(json.dumps(config),encoding='utf-8')
        report=run_main(self.config,'main',self.root,item_pipeline=True)
        self.assertEqual(report['phase'],'completed')
        self.assertEqual([row['phase'] for row in report['executions']],['item_pipeline'])
        resumed=run_main(self.config,'main',self.root)
        self.assertEqual(resumed['phase'],'completed')
        self.assertEqual(resumed['executions'][0]['executed'],0)

