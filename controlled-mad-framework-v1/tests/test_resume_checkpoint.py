import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controlled_mad import api
from controlled_mad.engine import execute_ready
from controlled_mad.runner import run_main
from controlled_mad.store import RunStore, IntegrityError

ROOT=Path(__file__).resolve().parents[1]


class ResumeCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT.parent/'.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def make_store(self):
        store=RunStore.create(self.root,'ledger',{'schema_version':1})
        with store.lock():
            store.event('started',task_id='task',attempt_id='attempt',stage='S0',reserved_tokens=100)
            store.event('selected',task_id='task',parse_id='parse')
        return store

    def test_checkpoint_replays_only_new_tail_and_preserves_budget_and_selection(self):
        store=self.make_store()
        self.assertTrue((store.path/'.resume-checkpoint.json.gz').exists())
        # Simulate a committed tail newer than the last checkpoint, as after a crash.
        tail=RunStore(store.path)
        tail.event('usage',attempt_id='attempt',tokens=40)
        tail.event('finished',attempt_id='attempt')
        reopened=RunStore(store.path)
        original=reopened.get
        reads=[]
        def reading(collection,key):
            if collection=='events':
                self.assertGreater(int(key),2,'历史前缀不应重新打开')
                reads.append(key)
            return original(collection,key)
        with patch.object(reopened,'get',side_effect=reading):
            self.assertEqual(reopened.selection(),{'task':'parse'})
            self.assertEqual(reopened.budget_totals()['tokens'],40)
            self.assertEqual(reopened.budget_totals()['calls'],1)
            self.assertTrue(reopened.task_events('task')[2])
        self.assertEqual(reads,['000000003','000000004'])

    def test_corrupt_cache_falls_back_to_ledger(self):
        store=self.make_store()
        checkpoint=store.path/'.resume-checkpoint.json.gz'
        invalid_block=bytearray(checkpoint.read_bytes())
        invalid_block[10]=(invalid_block[10]&~6)|6  # reserved DEFLATE block type
        for data in (b'broken cache',bytes(invalid_block)):
            with self.subTest(data=data[:12]):
                checkpoint.write_bytes(data)
                reopened=RunStore(store.path)
                self.assertEqual(reopened.selection(),{'task':'parse'})
                self.assertEqual(reopened.budget_totals()['tokens'],100)

    def test_failed_atomic_cache_replace_keeps_old_checkpoint_and_new_tail(self):
        store=self.make_store()
        checkpoint=store.path/'.resume-checkpoint.json.gz'
        old=checkpoint.read_bytes()
        store.event('usage',attempt_id='attempt',tokens=40)
        with patch('controlled_mad.checkpoint.os.replace',side_effect=OSError('simulated crash')):
            store.save_checkpoint()
        self.assertEqual(checkpoint.read_bytes(),old)
        self.assertEqual(list(store.path.glob('.resume-checkpoint-*.tmp')),[])
        self.assertEqual(RunStore(store.path).budget_totals()['tokens'],40)

    def test_uncommitted_tail_is_not_silently_accepted(self):
        store=self.make_store()
        (store.path/'events/000000003.json').write_text('{"sequence":3}',encoding='utf-8')
        with self.assertRaises(IntegrityError): RunStore(store.path).selection()

    def test_missing_cached_event_is_not_treated_as_a_new_empty_run(self):
        store=self.make_store()
        (store.path/'events/000000001.json').unlink()
        with self.assertRaises(IntegrityError): RunStore(store.path).selection()

    def test_legacy_without_checkpoint_loads_then_saves_on_exit(self):
        store=self.make_store()
        checkpoint=store.path/'.resume-checkpoint.json.gz'
        checkpoint.unlink()
        with RunStore(store.path).lock() as reopened:
            self.assertEqual(reopened.selection(),{'task':'parse'})
        payload=json.loads(gzip.decompress(checkpoint.read_bytes()))
        self.assertEqual(payload['last_sequence'],2)

    def test_explicit_verify_still_detects_old_corruption(self):
        store=self.make_store()
        (store.path/'events/000000001.json').write_text('{}',encoding='utf-8')
        reopened=RunStore(store.path)
        self.assertEqual(reopened.selection(),{'task':'parse'})
        with self.assertRaises(IntegrityError): reopened.verify()

    def config(self,strict=False):
        config=json.loads((ROOT/'configs/demo.json').read_text(encoding='utf-8'))
        config['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
        config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        config.update(operations=['ethos'],interventions=[],diagnostics={'oo':{'enabled':False,'repeats':1}})
        if strict:
            config.update(material_policy='strict_v10',material_gate=True,material_audit=True,
                          audit_model='mock',subset={'item_ids':['toy-001']})
            config['budget']['stage_calls']['audit']=20
        path=self.root/'config.json'
        path.write_text(json.dumps(config),encoding='utf-8')
        return path

    def test_execute_and_resume_do_not_call_full_verify(self):
        store=api.create_run(self.config(),'execute',self.root)
        with patch.object(RunStore,'verify',side_effect=AssertionError('不应全量核验')):
            execute_ready(store,max_calls=1)
            execute_ready(RunStore(store.path),max_calls=1)
        self.assertEqual(store.budget_totals()['calls'],1)
        self.assertEqual(RunStore(store.path).budget_totals()['calls'],2)

    def test_main_run_does_not_full_verify_in_any_automatic_phase(self):
        with patch.object(RunStore,'verify',side_effect=AssertionError('不应自动全量核验')):
            result=run_main(self.config(strict=True),'main',self.root,item_pipeline=True)
            resumed=run_main(self.root/'config.json','main',self.root)
        self.assertEqual(result['phase'],'completed')
        self.assertEqual(resumed['executions'][0]['executed'],0)
        self.assertFalse(result['verification']['full_scan'])
