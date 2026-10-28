import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import test_complete_matrix as fixture
from controlled_mad import api
from controlled_mad.runner import run_main
from controlled_mad.backends.mock import MockBackend


class MatrixRevision(unittest.TestCase):
    def setUp(self):
        f=fixture.CompleteMatrix(); f.setUp()
        self.addCleanup(f.doCleanups)
        self.f=f; self.root=f.root

    def test_group_first_moves_target_to_front_and_keeps_others_stable(self):
        from controlled_mad.matrix import reorder_structured
        expected={'support_first':['Support A','Support B','Conclusion','Limitation'],
                  'limitation_first':['Limitation','Support A','Conclusion','Support B']}
        for op,headings in expected.items():
            result=reorder_structured(fixture.COMMENT,op,layout='group_first_v2')
            self.assertEqual([p.split(':')[0] for p in result.split('\n\n')],headings)
            self.assertCountEqual(result.split('\n\n'),fixture.COMMENT.split('\n\n'))
        old=reorder_structured(fixture.COMMENT,'support_first')
        self.assertEqual([p.split(':')[0] for p in old.split('\n\n')],['Support A','Conclusion','Support B','Limitation'])

    def configure(self,count=2):
        c=copy.deepcopy(self.f.config_value)
        c['subset']={'item_ids':[f'pilot-{i:02d}' for i in range(count)]}
        self.f.config.write_text(json.dumps(c),encoding='utf-8')
        scenario=json.loads(self.f.scenario.read_text(encoding='utf-8'))
        scenario['items']['pilot-00']['audits']={'support_first':'fail'}
        self.f.scenario.write_text(json.dumps(scenario),encoding='utf-8')
        c['matrix_layout']='group_first_v2'
        path=self.root/'v2.json'; path.write_text(json.dumps(c),encoding='utf-8')
        return path

    def test_revision_reuses_common_results_without_api_and_source_is_unchanged(self):
        from controlled_mad.matrix_revision import prepare_revision
        path=self.configure()
        self.assertEqual(run_main(self.f.config,'source',self.root)['phase'],'incomplete_matrix')
        source=api.open_run('source',self.root)
        old={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.path.rglob('*') if p.is_file()}
        with patch.object(MockBackend,'generate',side_effect=AssertionError('preparation must be offline')):
            prepared=prepare_revision(path,'source','v2',self.root)
        self.assertEqual(prepared['cached_terminal_outputs'],12)
        self.assertEqual(prepared['imported_requests'],76)
        target=api.open_run('v2',self.root)
        before=set(target.selection())
        event_count=len(target.all('events'))
        self.assertEqual(prepare_revision(path,'source','v2',self.root)['imported_requests'],76)
        self.assertEqual(len(api.open_run('v2',self.root).all('events')),event_count)
        generate=MockBackend.generate
        def semantic_audit(backend,task,profile):
            if task.stage=='audit' and task.context['operation'] in ('support_first','limitation_first'):
                payload=json.loads(task.messages[-1]['content'])
                self.assertIn('operation_definition',payload)
                headers=[p.split(':')[0] for p in payload['candidate'].split('\n\n')]
                expected=(['Support A','Support B','Conclusion','Limitation'] if task.context['operation']=='support_first'
                          else ['Limitation','Support A','Conclusion','Support B'])
                decision={'verdict':'pass' if headers==expected else 'fail','reason':'Check exact target-first layout.'}
                scenario={'items':backend.scenario['items'],'overrides':[{'match':{'stage':'audit'},'text':json.dumps(decision)}]}
                backend=MockBackend(scenario,backend.on_raw)
            return generate(backend,task,profile)
        starts=[]
        with patch.object(MockBackend,'generate',new=semantic_audit):
            result=run_main(path,'v2',self.root,concurrency=2,
                            progress=lambda u:starts.append(u['task_id']) if u['event']=='start' else None)
            fresh=run_main(path,'fresh',self.root,concurrency=1)
        self.assertEqual(result['phase'],'completed')
        self.assertEqual(len(starts),94)
        self.assertTrue(before.isdisjoint(starts))
        self.assertEqual(api.matrix_status(api.open_run('v2',self.root))['summary']['complete_outputs'],32)
        target=api.open_run('v2',self.root); serial=api.open_run('fresh',self.root)
        for collection in ('tasks','raw','results','parses','materials','snapshots','messages','deliveries','comparisons'):
            self.assertEqual(target.ids(collection),serial.ids(collection),collection)
        api.verify_run(target)
        self.assertEqual({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (target.path/'raw').glob('*.bin')},
                         {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (serial.path/'raw').glob('*.bin')})
        for p,digest in old.items(): self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),digest,str(p))

    def test_revision_rejects_other_protocol_changes_and_same_run(self):
        from controlled_mad.matrix_revision import prepare_revision
        path=self.configure(1)
        source=api.create_run(self.f.config,'source',self.root)
        api.execute_ready(source,max_calls=1)
        with self.assertRaises(ValueError):prepare_revision(path,'source','source',self.root)
        c=json.loads(path.read_text(encoding='utf-8')); c['selection_seed']+=1
        path.write_text(json.dumps(c),encoding='utf-8')
        with self.assertRaises(ValueError):prepare_revision(path,'source','bad',self.root)
        self.assertFalse((self.root/'bad').exists())

    def test_main_prepare_only_reuses_partial_source_without_network(self):
        from controlled_mad.matrix_revision import prepare_revision
        path=self.configure(1)
        source=api.create_run(self.f.config,'source',self.root)
        api.execute_ready(source,max_calls=1)
        with patch.object(MockBackend,'generate',side_effect=AssertionError('offline preparation')):
            preview=prepare_revision(path,'source','v2',self.root,dry_run=True)
            self.assertEqual(preview['imported_requests'],1)
            self.assertFalse((self.root/'v2').exists())
            result=run_main(path,'v2',self.root,prepare_only=True,reuse_matrix_source='source')
        self.assertEqual(result['phase'],'prepared')
        self.assertEqual(len(api.open_run('v2',self.root).selection()),1)

    def test_interrupted_import_resumes_without_duplicate_budget_or_events(self):
        from controlled_mad.matrix_revision import prepare_revision
        from controlled_mad.store import RunStore
        path=self.configure(1)
        source=api.create_run(self.f.config,'source',self.root)
        api.execute_ready(source,max_calls=1)
        original=RunStore.event
        def interrupted(store,kind,**data):
            result=original(store,kind,**data)
            if store.path.name=='v2' and kind=='started' and data.get('matrix_reuse'):
                raise RuntimeError('simulate interruption after committed event')
            return result
        with patch.object(RunStore,'event',new=interrupted):
            with self.assertRaises(RuntimeError): prepare_revision(path,'source','v2',self.root)
        result=prepare_revision(path,'source','v2',self.root)
        self.assertEqual(result['imported_requests'],1)
        target=api.open_run('v2',self.root)
        self.assertEqual(target.budget_totals()['calls'],1)
        events=[e['matrix_reuse']['event_sequence'] for e in target.all('events') if e.get('matrix_reuse')]
        self.assertEqual(len(events),len(set(events)))
        self.assertEqual(len(target.selection()),1)

    def test_unfinished_import_cannot_overwrite_new_local_execution(self):
        from controlled_mad.matrix_revision import prepare_revision
        from controlled_mad.store import RunStore
        path=self.configure(1)
        source=api.create_run(self.f.config,'source',self.root)
        api.execute_ready(source,max_calls=1)
        original=RunStore.event
        def interrupted(store,kind,**data):
            result=original(store,kind,**data)
            if store.path.name=='v2' and kind=='started' and data.get('matrix_reuse'): raise RuntimeError('stop')
            return result
        with patch.object(RunStore,'event',new=interrupted):
            with self.assertRaises(RuntimeError): prepare_revision(path,'source','v2',self.root)
        api.execute_ready(api.open_run('v2',self.root),max_calls=1)
        with self.assertRaises(ValueError): prepare_revision(path,'source','v2',self.root)
