"""HTTP 重试须贯穿真实后端、执行器和重启恢复，而不是手工构造失败事件。"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from controlled_mad.backends.openai_compatible import OpenAIBackend
from controlled_mad.engine import create_run, execute_ready, chargeable_attempts, task_state, new_attempt
from controlled_mad.scheduler import Planner
from controlled_mad.store import RunStore

ROOT=Path(__file__).resolve().parents[1]


class HTTPRetryRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp'))
        self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)
        data=folder/'one.jsonl'
        data.write_text((ROOT/'examples/toy_dataset.jsonl').read_text(encoding='utf-8').splitlines()[0]+'\n',encoding='utf-8')
        config=json.loads((ROOT/'configs/demo.json').read_text(encoding='utf-8'))
        config['dataset']['source']=str(data)
        config.pop('mock_scenario')
        config['profiles']['mock']={'provider':'openai','model':'m','base_url':'https://example.test/v1','credential_env':'TEST_HTTP_KEY'}
        config['network_enabled']=True
        config['operations']=['ethos']; config['interventions']=[]
        config['diagnostics']['oo']['enabled']=False
        path=folder/'config.json'; path.write_text(json.dumps(config),encoding='utf-8')
        self.store=create_run(path,'r',folder)
        self.task=next(iter(Planner(self.store).build().tasks.values()))
        credential=patch.dict(os.environ,{'TEST_HTTP_KEY':'test-http-secret'})
        credential.start(); self.addCleanup(credential.stop)

    def backend(self,responses):
        responses=iter(responses)
        def factory(store,task,on_raw=None,scenario=None,retry_http=False):
            return OpenAIBackend(lambda *args:next(responses),on_raw,retry_http)
        return patch('controlled_mad.engine.backend_for',side_effect=factory)

    def success(self):
        text=json.dumps({'label':'A','comment':'Recovered answer.'})
        raw=json.dumps({'model':'m','choices':[{'message':{'content':text},'finish_reason':'stop'}],
                        'usage':{'prompt_tokens':10,'completion_tokens':5}}).encode()
        return 200,{},raw

    def test_three_rate_limits_do_not_exhaust_task_retry_quota(self):
        responses=[(429,{},b'limited')]*3+[self.success()]
        with self.backend(responses),patch('controlled_mad.engine.time.sleep'):
            execute_ready(self.store,allow_network=True,max_calls=4,auto_retry_unknown=True,retry_http=True)
        self.assertEqual(task_state(self.store,self.task.task_id),'succeeded')
        self.assertEqual(chargeable_attempts(self.store,self.task.task_id),1)
        self.assertEqual(self.store.budget_totals()['calls'],4)
        self.assertEqual(self.store.budget_totals()['unknown_usage'],3)
        self.assertGreater(self.store.budget_totals()['tokens'],15)

    def test_resume_after_rate_limit_selects_success_not_http_error(self):
        with self.backend([(429,{},b'limited')]):
            execute_ready(self.store,allow_network=True,max_calls=1,retry_http=True)
        reopened=RunStore(self.store.path)
        with self.backend([self.success()]),patch('controlled_mad.engine.time.sleep'):
            execute_ready(reopened,allow_network=True,max_calls=1,auto_retry_unknown=True,retry_http=True)
        self.assertEqual(task_state(reopened,self.task.task_id),'succeeded')
        self.assertEqual(reopened.result(self.task.task_id)['value']['comment'],'Recovered answer.')
        self.assertEqual(len(reopened.attempts(self.task.task_id)),2)

    def test_receipt_survives_crash_before_retry_event(self):
        with self.backend([(503,{},b'unavailable')]),patch.object(OpenAIBackend,'decode_received',side_effect=RuntimeError('crash after raw')):
            with self.assertRaises(RuntimeError):
                execute_ready(self.store,allow_network=True,max_calls=1,retry_http=True)
        reopened=RunStore(self.store.path)
        with self.backend([self.success()]),patch('controlled_mad.engine.time.sleep'):
            execute_ready(reopened,allow_network=True,max_calls=1,auto_retry_unknown=True,retry_http=True)
        self.assertEqual(task_state(reopened,self.task.task_id),'succeeded')
        self.assertEqual(chargeable_attempts(reopened,self.task.task_id),1)

    def test_retry_after_seconds_and_http_date_are_honored(self):
        now=1800000000.0
        date=format_datetime(datetime.fromtimestamp(now+120,timezone.utc),usegmt=True)
        for header in ('120',date):
            with self.subTest(header=header):
                waits=[]
                # 独立请求依次使用三个 S0 槽位中的前两个，不复用已成功任务。
                with self.backend([(429,{'Retry-After':header},b'limited'),self.success()]), \
                     patch('time.time',return_value=now),patch('controlled_mad.engine.time.sleep',side_effect=waits.append):
                    execute_ready(self.store,allow_network=True,max_calls=2,auto_retry_unknown=True,retry_http=True)
                self.assertTrue(waits)
                self.assertGreaterEqual(waits[0],120)

    def test_retry_deadline_is_preserved_across_resume(self):
        with self.backend([(429,{'Retry-After':'120'},b'limited')]),patch('time.time',return_value=1800000000.0):
            execute_ready(self.store,allow_network=True,max_calls=1,retry_http=True)
        reopened=RunStore(self.store.path); waits=[]
        with self.backend([self.success()]),patch('time.time',return_value=1800000030.0), \
             patch('controlled_mad.engine.time.sleep',side_effect=waits.append):
            execute_ready(reopened,allow_network=True,max_calls=1,auto_retry_unknown=True,retry_http=True)
        self.assertTrue(waits)
        self.assertGreaterEqual(waits[0],90)
        self.assertEqual(task_state(reopened,self.task.task_id),'succeeded')

    def test_receipt_without_raw_preserves_http_failure_and_wait(self):
        original=self.store.write_blob
        def crash_before_raw(collection,key,data,suffix='.json'):
            if collection=='raw': raise RuntimeError('crash before raw')
            return original(collection,key,data,suffix)
        with self.backend([(429,{'Retry-After':'120'},b'limited')]), \
             patch('time.time',return_value=1800000000.0),patch.object(self.store,'write_blob',side_effect=crash_before_raw):
            with self.assertRaises(RuntimeError):
                execute_ready(self.store,allow_network=True,max_calls=1,retry_http=True)
        reopened=RunStore(self.store.path); waits=[]
        with self.backend([self.success()]),patch('time.time',return_value=1800000030.0), \
             patch('controlled_mad.engine.time.sleep',side_effect=waits.append):
            execute_ready(reopened,allow_network=True,max_calls=1,auto_retry_unknown=True,retry_http=True)
        self.assertTrue(waits)
        self.assertGreaterEqual(waits[0],90)
        self.assertEqual(chargeable_attempts(reopened,self.task.task_id),1)
        self.assertEqual(task_state(reopened,self.task.task_id),'succeeded')

    def test_explicit_retry_obeys_existing_http_deadline(self):
        with self.backend([(429,{'Retry-After':'120'},b'limited')]),patch('time.time',return_value=1800000000.0):
            execute_ready(self.store,allow_network=True,max_calls=1,retry_http=True)
        new_attempt(self.store,self.task.task_id,'Resume after rate limit')
        reopened=RunStore(self.store.path); waits=[]
        with self.backend([self.success()]),patch('time.time',return_value=1800000030.0), \
             patch('controlled_mad.engine.time.sleep',side_effect=waits.append):
            execute_ready(reopened,allow_network=True,max_calls=1,auto_retry_unknown=True,retry_http=True)
        self.assertTrue(waits)
        self.assertGreaterEqual(waits[0],90)
        self.assertEqual(task_state(reopened,self.task.task_id),'succeeded')

    def test_default_http_error_remains_invalid_after_resume(self):
        with self.backend([(429,{},b'limited')]):
            result=execute_ready(self.store,allow_network=True,max_calls=1)
        self.assertEqual(result['stop'],'backend_error')
        reopened=RunStore(self.store.path)
        execute_ready(reopened,max_calls=0,retry_http=True)
        self.assertEqual(task_state(reopened,self.task.task_id),'invalid')
