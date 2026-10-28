import copy,json,unittest
from unittest.mock import patch

import test_complete_matrix as fixture
from controlled_mad import api
from controlled_mad.backends.mock import MockBackend
from controlled_mad.domain import ModelResult
from controlled_mad.runner import run_main
from controlled_mad.store import RunStore


class ResilientExecution(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CompleteMatrix(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root=self.f.root
        c=copy.deepcopy(self.f.config_value)
        c.update(matrix_layout='group_first_v2',subset={'item_ids':['pilot-00','pilot-01']})
        self.f.config.write_text(json.dumps(c),encoding='utf-8')

    def test_invalid_reply_is_retried_and_valid_repair_selected_without_manual_reparse(self):
        generate=MockBackend.generate; failures=[]
        def reply(backend,task,profile):
            if task.context['item_id']=='pilot-00' and task.stage=='T2' and not failures:
                failures.append(task.task_id)
                return ModelResult(b'bad',json.dumps({'label':'A','comment':'x','extra':'bad'}),'mock','stop',{})
            return generate(backend,task,profile)
        with patch.object(MockBackend,'generate',new=reply):
            result=run_main(self.f.config,'repair',self.root,concurrency=2,question_batch_size=1,continue_on_error=True)
        self.assertEqual(result['phase'],'completed')
        s=api.open_run('repair',self.root)
        self.assertEqual(api.matrix_status(s)['summary']['complete_outputs'],32)
        self.assertEqual(len(s.attempts(failures[0])),2)
        self.assertTrue(s.result(failures[0])['valid'])

    def test_exhausted_item_is_recorded_skipped_and_resume_does_not_repeat_it(self):
        generate=MockBackend.generate; starts=[]
        def reply(backend,task,profile):
            if task.context['item_id']=='pilot-00' and task.stage=='S0' and task.context['agent']=='agent_1':
                starts.append(task.task_id); return ModelResult(b'bad','bad','mock','stop',{})
            return generate(backend,task,profile)
        with patch.object(MockBackend,'generate',new=reply):
            result=run_main(self.f.config,'skip',self.root,concurrency=3,question_batch_size=1,continue_on_error=True)
        self.assertEqual(result['phase'],'completed_with_failures')
        self.assertEqual(len(starts),3)
        s=api.open_run('skip',self.root); matrix=api.matrix_status(s)['summary']
        self.assertEqual(matrix['complete_items'],1); self.assertEqual(matrix['complete_outputs'],16)
        self.assertEqual(result['status']['progress']['items']['skipped'],1)
        self.assertEqual(result['status']['progress']['items']['processed'],2)
        skips=[e for e in s.all('events') if e['kind']=='item_skipped']
        self.assertEqual(len(skips),1); self.assertEqual(skips[0]['item_id'],'pilot-00')
        self.assertEqual(len(skips[0]['missing_outputs']),16)
        with patch.object(MockBackend,'generate',side_effect=AssertionError('resume must not repeat')):
            resumed=run_main(self.f.config,'skip',self.root,question_batch_size=1)
        self.assertEqual(resumed['phase'],'completed_with_failures')
        self.assertEqual(resumed['executions'][0]['executed'],0)
        self.assertEqual(len([e for e in api.open_run('skip',self.root).all('events') if e['kind']=='item_skipped']),1)

    def test_existing_valid_unselected_retry_is_adopted_without_new_request(self):
        s=api.create_run(self.f.config,'existing',self.root)
        task=next(iter(__import__('controlled_mad.scheduler',fromlist=['Planner']).Planner(s,persist=False).build().tasks.values()))
        generate=MockBackend.generate
        def bad(*args): return ModelResult(b'bad','bad','mock','stop',{})
        with patch.object(MockBackend,'generate',new=bad): api.execute_ready(s,max_calls=1)
        api.new_attempt(s,task.task_id,'explicit retry')
        api.execute_ready(s,max_calls=1)
        self.assertFalse(s.get('parses',s.selection()[task.task_id])['valid'])
        before=len(s.attempts(task.task_id))
        result=run_main(self.f.config,'existing',self.root,concurrency=2,continue_on_error=True,question_batch_size=1)
        self.assertEqual(result['phase'],'completed')
        self.assertEqual(len(api.open_run('existing',self.root).attempts(task.task_id)),before)

    def test_material_failure_is_skipped_and_all_failed_run_exports_honest_matrix(self):
        c=copy.deepcopy(self.f.config_value); c.update(matrix_layout='group_first_v2',subset={'item_ids':['pilot-00']})
        self.f.config.write_text(json.dumps(c),encoding='utf-8')
        scenario=json.loads(self.f.scenario.read_text(encoding='utf-8'))
        scenario['items']['pilot-00']['audits']={'support_first':'fail'}
        self.f.scenario.write_text(json.dumps(scenario),encoding='utf-8')
        r=run_main(self.f.config,'material',self.root,continue_on_error=True,question_batch_size=1)
        self.assertEqual(r['phase'],'completed_with_failures')
        self.assertEqual(r['status']['progress']['items']['complete'],0)
        self.assertEqual(r['status']['progress']['items']['skipped'],1)
        self.assertEqual(api.matrix_status(api.open_run('material',self.root))['summary']['complete_outputs'],0)
        self.assertIn('failures',r['analysis']['exports'])

    def test_interrupted_auto_repair_is_adopted_on_resume(self):
        s=api.create_run(self.f.config,'interrupted',self.root)
        generate=MockBackend.generate; calls=[]
        def reply(backend,task,profile):
            if not calls:
                calls.append(task.task_id); return ModelResult(b'bad','bad','mock','stop',{})
            return generate(backend,task,profile)
        with patch.object(MockBackend,'generate',new=reply):
            api.execute_ready(s,max_calls=1,continue_on_error=True,question_batch_size=1)
        api.execute_ready(RunStore(s.path),max_calls=2,continue_on_error=True,question_batch_size=1)
        result=run_main(self.f.config,'interrupted',self.root,question_batch_size=1)
        self.assertEqual(result['phase'],'completed')
        self.assertEqual(len(api.open_run('interrupted',self.root).attempts(calls[0])),2)

    def test_timeout_exhaustion_skips_only_failed_item(self):
        generate=MockBackend.generate
        def reply(backend,task,profile):
            if task.context['item_id']=='pilot-00' and task.stage=='S0' and task.context['agent']=='agent_1':
                raise TimeoutError('synthetic timeout')
            return generate(backend,task,profile)
        with patch.object(MockBackend,'generate',new=reply),patch('controlled_mad.engine.time.sleep'):
            r=run_main(self.f.config,'timeout',self.root,concurrency=2,continue_on_error=True,question_batch_size=1)
        self.assertEqual(r['phase'],'completed_with_failures')
        self.assertEqual(r['status']['progress']['items']['skipped'],1)
        self.assertEqual(r['status']['progress']['items']['complete'],1)

    def test_global_rate_limit_pauses_without_marking_dataset_failed(self):
        from controlled_mad.backends.transport import RetryableHTTP
        def limited(backend,task,profile):
            backend.on_raw(b'limited',429,{'Retry-After':'0'})
            raise RetryableHTTP('HTTP 429')
        with patch.object(MockBackend,'generate',new=limited),patch('controlled_mad.engine.time.sleep'):
            r=run_main(self.f.config,'limited',self.root,concurrency=1,continue_on_error=True,question_batch_size=1)
        self.assertEqual(r['phase'],'paused_service')
        self.assertEqual(r['status']['progress']['items']['skipped'],0)
        self.assertEqual(r['status']['budget']['calls'],6)

    def test_long_retry_after_saves_checkpoint_instead_of_waiting_hours(self):
        from controlled_mad.backends.transport import RetryableHTTP
        def limited(backend,task,profile):
            backend.on_raw(b'limited',429,{'Retry-After':'3600'})
            raise RetryableHTTP('HTTP 429')
        with patch.object(MockBackend,'generate',new=limited),patch('controlled_mad.engine.time.sleep',side_effect=AssertionError('must pause')):
            r=run_main(self.f.config,'long-limit',self.root,continue_on_error=True,question_batch_size=1)
        self.assertEqual(r['phase'],'paused_service')
        self.assertEqual(r['status']['budget']['calls'],1)
        self.assertEqual(r['status']['progress']['items']['skipped'],0)

    def test_auth_and_payment_errors_pause_without_skipping_questions(self):
        for status in (401,402,403):
            def unavailable(*args): return ModelResult(b'error','','mock',None,{},f'HTTP {status}; Retry-After=')
            with patch.object(MockBackend,'generate',new=unavailable):
                r=run_main(self.f.config,f'http-{status}',self.root,continue_on_error=True,question_batch_size=1)
            self.assertEqual(r['phase'],'paused_service')
            self.assertEqual(r['status']['budget']['calls'],1)
            self.assertEqual(r['status']['progress']['items']['skipped'],0)
            with patch.object(MockBackend,'generate',new=unavailable):
                for _ in range(2):
                    r=run_main(self.f.config,f'http-{status}',self.root,question_batch_size=1)
                    self.assertEqual(r['phase'],'paused_service')
            with patch.object(MockBackend,'generate',side_effect=AssertionError('exhausted global failures must pause')):
                r=run_main(self.f.config,f'http-{status}',self.root,question_batch_size=1)
            self.assertEqual(r['phase'],'paused_service')
            self.assertEqual(r['status']['progress']['items']['skipped'],0)

    def test_serial_concurrent_and_resumed_repair_have_same_content(self):
        generate=MockBackend.generate; stores=[]
        for name,concurrency,partial in [('serial',1,False),('parallel',3,False),('resumed',3,True)]:
            failed=set()
            def reply(backend,task,profile):
                if task.stage=='T2' and task.context['agent']=='agent_1' and task.task_id not in failed:
                    failed.add(task.task_id)
                    return ModelResult(b'bad','bad','mock','stop',{})
                return generate(backend,task,profile)
            s=api.create_run(self.f.config,name,self.root)
            with patch.object(MockBackend,'generate',new=reply):
                if partial: api.execute_ready(s,max_calls=15,concurrency=concurrency,continue_on_error=True,question_batch_size=1)
                api.execute_ready(RunStore(s.path),concurrency=concurrency,continue_on_error=True,question_batch_size=1)
            stores.append(api.open_run(name,self.root))
        for collection in ('tasks','raw','results','parses','materials','snapshots','messages','deliveries','comparisons'):
            self.assertEqual(stores[0].ids(collection),stores[1].ids(collection),collection)
            self.assertEqual(stores[0].ids(collection),stores[2].ids(collection),collection)
