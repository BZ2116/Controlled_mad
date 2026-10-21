import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from controlled_mad.engine import create_run, execute_ready, status, submit, new_attempt, fork_revision, reparse
from controlled_mad.scheduler import Planner
from controlled_mad.budget import BudgetError
from controlled_mad.identity import canonical_bytes
from controlled_mad.backends.transport import TransportUnknown
from controlled_mad.backends.mock import MockBackend
ROOT=Path(__file__).resolve().parents[1]

class Recovery(unittest.TestCase):
    def setUp(self):
        sleeper=patch('controlled_mad.engine.time.sleep')
        self.sleep=sleeper.start(); self.addCleanup(sleeper.stop)
        self.temp=tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')); self.addCleanup(self.temp.cleanup)
        config=json.loads((ROOT/'configs/demo.json').read_text())
        config['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
        config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        config['operations']=['ethos']; config['diagnostics']['oo']['enabled']=False; config['interventions']=[]
        p=Path(self.temp.name)/'config.json'; p.write_text(json.dumps(config))
        self.run=create_run(p,'r',Path(self.temp.name))
    def first(self): return next(iter(Planner(self.run).build().tasks.values()))
    def test_resume_no_duplicate(self):
        execute_ready(self.run,max_calls=2)
        calls=status(self.run)['budget']['calls']
        execute_ready(self.run,max_calls=1)
        self.assertEqual(status(self.run)['budget']['calls'],calls+1)
    def test_unknown_not_resent(self):
        with patch('controlled_mad.backends.mock.MockBackend.generate',side_effect=TransportUnknown('未知')):
            r=execute_ready(self.run)
        self.assertEqual(r['stop'],'transport_unknown')
        before=status(self.run)['budget']['calls']
        execute_ready(self.run)
        self.assertEqual(status(self.run)['budget']['calls'],before)
    def test_transient_failure_recovers_without_restarting(self):
        original=MockBackend.generate
        calls=[]
        def flaky(backend,task,p):
            calls.append(task.task_id)
            if len(calls)==1: raise TransportUnknown('模拟断连')
            return original(backend,task,p)
        with patch.object(MockBackend,'generate',flaky):
            result=execute_ready(self.run,max_calls=3,auto_retry_unknown=True)
        self.assertEqual(result['executed'],2)
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(status(self.run)['transport_unknown'],0)
        self.sleep.assert_called_once_with(5)
    def test_exhausted_task_does_not_block_other_tasks(self):
        target=self.first().task_id; original=MockBackend.generate
        def broken(backend,task,p):
            if task.task_id==target: raise TransportUnknown('模拟单条失败')
            return original(backend,task,p)
        with patch.object(MockBackend,'generate',broken):
            result=execute_ready(self.run,max_calls=4,auto_retry_unknown=True)
        self.assertEqual(result['executed'],1)
        self.assertEqual(len(self.run.attempts(target)),3)
        summary=status(self.run)
        self.assertEqual(summary['transport_unknown'],1)
        self.assertEqual(summary['budget']['calls'],4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list],[5,10])
    def test_persistent_outage_stops_after_six_calls(self):
        with patch.object(MockBackend,'generate',side_effect=TransportUnknown('模拟持续断网')):
            result=execute_ready(self.run,auto_retry_unknown=True)
        self.assertEqual(result['stop'],'transport_unknown')
        self.assertEqual(status(self.run)['budget']['calls'],6)
        self.assertEqual(result['executed'],0)
    def test_resume_auto_retries_unknown_once(self):
        task=self.first()
        with patch('controlled_mad.backends.mock.MockBackend.generate',side_effect=TransportUnknown('未知')):
            execute_ready(self.run,max_calls=1)
        result=execute_ready(self.run,max_calls=1,auto_retry_unknown=True)
        self.assertEqual(result['executed'],1)
        self.assertEqual(len(self.run.attempts(task.task_id)),2)
        self.assertEqual(self.run.result(task.task_id)['valid'],True)
    def test_preflight_failures_do_not_exhaust_transport_retries(self):
        task=self.first()
        with patch('controlled_mad.backends.mock.MockBackend.generate',side_effect=TransportUnknown('未知')):
            execute_ready(self.run,max_calls=1)
        for number in (2,3):
            aid=f'{task.task_id}-a{number:03d}'
            self.run.event('retry_requested',task_id=task.task_id,attempt_id=aid,reason='旧版自动恢复')
            self.run.event('started',task_id=task.task_id,attempt_id=aid,stage=task.stage,provider='mock',reserved_tokens=100,wire_format='mock')
            self.run.event('transport_unknown',task_id=task.task_id,attempt_id=aid,reason='backend rejected configuration')
        result=execute_ready(self.run,max_calls=1,auto_retry_unknown=True)
        self.assertEqual(result['executed'],1)
        self.assertEqual(self.run.attempts(task.task_id)[-1]['attempt_id'],f'{task.task_id}-a004')
        self.assertTrue(self.run.result(task.task_id)['valid'])
    def test_missing_credential_does_not_start_or_request_retry(self):
        config=json.loads((ROOT/'configs/demo.json').read_text())
        config['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
        config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        config['profiles']['mock']={'provider':'openai','model':'m','base_url':'https://example.test/v1','credential_env':'MISSING_TEST_CREDENTIAL'}
        config['network_enabled']=True
        path=Path(self.temp.name)/'missing-credential.json'; path.write_text(json.dumps(config))
        run=create_run(path,'missing-credential',Path(self.temp.name))
        task=next(iter(Planner(run).build().tasks.values()))
        before=len(run.all('events'))
        with patch.dict('os.environ',{'MISSING_TEST_CREDENTIAL':''}):
            with self.assertRaisesRegex(ValueError,'MISSING_TEST_CREDENTIAL'):
                execute_ready(run,allow_network=True,max_calls=1)
        self.assertEqual(len(run.attempts(task.task_id)),0)
        self.assertEqual(len([e for e in run.all('events')[before:] if e['kind']=='retry_requested']),0)
    def test_committed_raw_recovery(self):
        task=self.first(); aid=task.task_id+'-a001'
        self.run.event('started',task_id=task.task_id,attempt_id=aid,stage='S0',provider='manual',reserved_tokens=100,wire_format='text')
        self.run.write_blob('raw',aid,b'{"label":"A","comment":"recovered"}','.bin')
        execute_ready(self.run,max_calls=0)
        self.assertEqual(self.run.result(task.task_id)['value']['comment'],'recovered')
        self.assertEqual(status(self.run)['budget']['calls'],1)
    def test_reparse_and_idempotent_import(self):
        task=self.first(); aid=task.task_id+'-a001'
        text=b'```json\n{"label":"A","comment":"comment"}\n```'
        self.assertFalse(submit(self.run,task.task_id,text,aid)['valid'])
        self.assertTrue(submit(self.run,task.task_id,text,aid)['idempotent'])
        self.assertTrue(reparse(self.run,task.task_id,'vote-v2')['valid'])
        self.assertEqual(len(self.run.attempts(task.task_id)),1)
    def test_retry_revision(self):
        task=self.first()
        submit(self.run,task.task_id,b'not JSON')
        request=new_attempt(self.run,task.task_id,'explicit retry')
        execute_ready(self.run,max_calls=1)
        self.assertIsNone(self.run.result(task.task_id))
        revised=fork_revision(self.run,task.task_id,request['attempt_id'])
        self.assertIsNotNone(revised.result(task.task_id))
        self.assertIsNone(self.run.result(task.task_id))
    def test_status_readonly(self):
        for task in list(Planner(self.run).build().tasks.values()):
            submit(self.run,task.task_id,canonical_bytes({'label':task.context['labels'][0],'comment':'comment'}))
        before=set(self.run.path.rglob('*'))
        status(self.run)
        self.assertEqual(set(self.run.path.rglob('*')),before)
