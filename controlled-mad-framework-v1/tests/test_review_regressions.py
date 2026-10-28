import json, unittest, tempfile
from pathlib import Path
from unittest.mock import patch
from controlled_mad.domain import Task, ModelProfile, ModelResult
from controlled_mad.engine import parse_result, task_state, create_run, execute_ready, recover, submit, new_attempt, fork_revision
from controlled_mad.scheduler import Planner
from controlled_mad.identity import canonical_bytes
from controlled_mad.material_checks import reorder
from controlled_mad.backends.openai_compatible import OpenAIBackend
from controlled_mad.store import RunStore
from controlled_mad.budget import Budget

class ReviewRegressions(unittest.TestCase):
    def make_run(self,real=False):
        root=Path(__file__).resolve().parents[1]
        temp=tempfile.TemporaryDirectory(dir=str(root.parent/'.tmp')); self.addCleanup(temp.cleanup)
        c=json.loads((root/'configs/demo.json').read_text())
        c['dataset']['source']=str(root/'examples/toy_dataset.jsonl'); c['mock_scenario']=str(root/'examples/mock_scenario.json')
        c['operations']=['conclusion_front']; c['diagnostics']['oo']['enabled']=False; c['interventions']=[]
        if real:
            c['profiles']['mock']={'provider':'openai','model':'m','base_url':'https://example.test/v1','credential_env':'TEST_KEY'}
            c['network_enabled']=True
        config=Path(temp.name)/'config.json'; config.write_text(json.dumps(c))
        return create_run(config,'r',temp.name)
    def test_malformed_annotations_are_invalid(self):
        t=Task('t','annotate','p',(),(),{},'h')
        for blocks in ([None],[7],[{'text':'x','kind':[]}],[{'text':4,'kind':'support'}]):
            with self.subTest(blocks=blocks):
                r=ModelResult(b'',json.dumps({'blocks':blocks}),None,None,None)
                self.assertFalse(parse_result(t,r,'vote-v1')['valid'])
                with self.assertRaises(ValueError): reorder('x',blocks,'support_first')
    def test_raw_receipt_before_decode(self):
        received=[]
        class BrokenDecoder(OpenAIBackend):
            def decode(self,raw,p):
                self.assert_raw=received[:]
                raise RuntimeError('decoder crash')
        raw=b'{"model":"m","choices":[]}'
        b=BrokenDecoder(lambda *args:(200,{},raw),on_raw=lambda *args:received.append(args))
        t=Task('t','S0','p',(),(),{},'h')
        with patch.dict('os.environ',{'TEST_KEY':'dummy'}):
            with self.assertRaises(RuntimeError): b.generate(t,ModelProfile('p','openai','m','https://example.test/v1','TEST_KEY'))
        self.assertEqual(b.assert_raw,[(raw,200,{})])
    def test_annotation_retry_unblocks_planning(self):
        # 逐题调度下，首题三份 S0 完成后停在注释请求之前。
        s=self.make_run(); execute_ready(s,max_calls=3)
        t=next(t for t in Planner(s).build().tasks.values() if t.stage=='annotate')
        self.assertEqual(task_state(s,t.task_id),'ready')
        self.assertFalse(submit(s,t.task_id,b'{"blocks":[null]}')['valid'])
        Planner(s).build()
        request=new_attempt(s,t.task_id,'修复注释结构')
        item=s.get('inputs','mock')['items'][t.context['item_id']]
        self.assertTrue(submit(s,t.task_id,canonical_bytes({'blocks':item['blocks']}),request['attempt_id'])['valid'])
        revised=fork_revision(s,t.task_id,request['attempt_id'])
        self.assertTrue(revised.result(t.task_id)['valid'])
        Planner(revised).build()
        self.assertIsNone(s.result(t.task_id))
    def test_http_decode_crash_recovers_without_transport(self):
        s=self.make_run(real=True); calls=[]
        def transport(*args):
            calls.append(1)
            return 200,{},canonical_bytes({'model':'m','choices':[{'message':{'content':'{"label":"A","comment":"recovered"}'},'finish_reason':'stop'}]})
        def factory(store,task,on_raw=None,**kwargs): return OpenAIBackend(transport,on_raw)
        with patch.dict('os.environ',{'TEST_KEY':'dummy'}), patch('controlled_mad.engine.backend_for',side_effect=factory):
            with patch.object(OpenAIBackend,'decode',side_effect=RuntimeError('解码中断')):
                with self.assertRaises(RuntimeError): execute_ready(s,allow_network=True,max_calls=1)
            self.assertEqual(len(s.ids('attempts')),1)
            with patch('controlled_mad.backends.transport.http_post',side_effect=AssertionError('不得重发')):
                with s.lock(): self.assertEqual(len(recover(s)),1)
        self.assertEqual(len(calls),1)
        t=next(iter(Planner(s).build().tasks.values()))
        self.assertEqual(s.result(t.task_id)['value']['comment'],'recovered')
    def test_index_queries_do_not_rescan_event_list(self):
        with tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[2]/'.tmp')) as tmp:
            s=RunStore.create(tmp,'r',{})
            s.event('started',task_id='t',attempt_id='a',stage='S0',reserved_tokens=100)
            s.event('usage',attempt_id='a',tokens=3)
            s.event('finished',task_id='t',attempt_id='a')
            s.event('selected',task_id='t',parse_id='p')
            s.put('parses','p',{'valid':True})
            with patch.object(s,'all',side_effect=AssertionError('不得逐查询扫描日志')):
                self.assertEqual(task_state(s,'t'),'succeeded')
                self.assertEqual(len(s.attempts('t')),1)
                self.assertEqual(Budget(s).totals()['tokens'],3)
            reloaded=RunStore(s.path)
            self.assertEqual(reloaded.selection(),s.selection())
            self.assertEqual(Budget(reloaded).totals(),Budget(s).totals())
