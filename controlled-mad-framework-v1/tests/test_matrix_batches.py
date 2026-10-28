import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import test_complete_matrix as fixture
from controlled_mad import api
from controlled_mad.backends.mock import MockBackend
from controlled_mad.matrix_revision import prepare_revision
from controlled_mad.runner import run_main


class MatrixBatches(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CompleteMatrix(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root=self.f.root
        self.config=copy.deepcopy(self.f.config_value)
        self.config.update(matrix_layout='group_first_v2', subset={'item_ids':['pilot-00','pilot-01','pilot-02']})
        self.f.config.write_text(json.dumps(self.config),encoding='utf-8')

    def test_batch_barrier_resume_and_content_identity(self):
        store=api.create_run(self.f.config,'batched',self.root)
        api.execute_ready(store,max_calls=7,concurrency=3,question_batch_size=2)
        self.assertEqual({t['context']['item_id'] for t in store.all('tasks') if t['task_id'] in store.selection()}, {'pilot-00','pilot-01'})
        before=set(store.selection()); observed=[]
        def progress(u):
            if u['event']=='start':
                observed.append(u['task_id'])
                if u['item_position']==3:
                    self.assertEqual(u['overall']['items']['complete'],2)
                    self.assertEqual(u['overall']['items']['batch']['index'],2)
        result=api.execute_ready(store,concurrency=3,question_batch_size=2,progress=progress)
        self.assertEqual(result['stop'],'no_ready_tasks')
        self.assertTrue(before.isdisjoint(observed))
        self.assertEqual(api.matrix_status(store)['summary']['complete_outputs'],48)
        serial=api.create_run(self.f.config,'serial',self.root)
        api.execute_ready(serial,concurrency=1)
        for collection in ('tasks','raw','results','parses','materials','snapshots','messages','deliveries','comparisons'):
            self.assertEqual(store.ids(collection),serial.ids(collection),collection)

    def test_failed_batch_does_not_start_later_questions(self):
        scenario=json.loads(self.f.scenario.read_text(encoding='utf-8'))
        scenario['items']['pilot-00']['audits']={'support_first':'fail'}
        self.f.scenario.write_text(json.dumps(scenario),encoding='utf-8')
        result=run_main(self.f.config,'blocked',self.root,concurrency=3,question_batch_size=2)
        self.assertEqual(result['phase'],'incomplete_matrix')
        self.assertEqual(result['executions'][0]['stop'],'incomplete_batch')
        store=api.open_run('blocked',self.root)
        self.assertFalse(any(store.get('tasks',e['task_id'])['context']['item_id']=='pilot-02'
                             for e in store.all('events') if e['kind']=='started'))

    def test_expand_inherits_nested_provenance_and_all_sixteen_results(self):
        self.config['subset']={'item_ids':['pilot-00']}
        self.f.config.write_text(json.dumps(self.config),encoding='utf-8')
        source=api.create_run(self.f.config,'pilot',self.root)
        api.execute_ready(source)
        # A v2 pilot may itself contain replies imported from its preceding pilot.
        tid=next(iter(source.selection()))
        source.event('selected',task_id=tid,parse_id=source.selection()[tid],
                     matrix_reuse={'source_run_id':'upstream','event_sequence':1})
        hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.path.rglob('*') if p.is_file()}
        expanded=copy.deepcopy(self.config); expanded['subset']={'item_ids':['pilot-00','pilot-01']}
        expanded['budget']['max_calls']+=100
        path=self.root/'expanded.json'; path.write_text(json.dumps(expanded),encoding='utf-8')
        with patch.object(MockBackend,'generate',side_effect=AssertionError('offline import')):
            result=prepare_revision(path,'pilot','expanded',self.root)
        self.assertEqual(result['cached_terminal_outputs'],16)
        target=api.open_run('expanded',self.root)
        self.assertEqual(api.matrix_status(target)['summary']['complete_outputs'],16)
        started=[]
        api.execute_ready(target,question_batch_size=1,concurrency=3,
                          progress=lambda u:started.append(u['task_id']) if u['event']=='start' else None)
        self.assertTrue(set(source.selection()).isdisjoint(started))
        self.assertEqual(api.matrix_status(target)['summary']['complete_outputs'],32)
        count=len(target.all('events')); prepare_revision(path,'pilot','expanded',self.root)
        self.assertEqual(len(target.all('events')),count)
        for p,digest in hashes.items(): self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),digest)
        expanded['selection_seed']+=1; path.write_text(json.dumps(expanded),encoding='utf-8')
        with self.assertRaises(ValueError): prepare_revision(path,'pilot','bad',self.root)
