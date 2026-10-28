import copy
import json
import unittest

import test_complete_matrix as fixture
from controlled_mad import api
from controlled_mad.protocol import item_settings
from controlled_mad.scheduler import Planner


class EqualRoundMatrix(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CompleteMatrix(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config=copy.deepcopy(self.f.config_value)
        self.config.update(matrix_layout='group_first_v2',matrix_protocol='equal_rounds_v10',
                           subset={'item_ids':['pilot-00','pilot-01','pilot-02']})
        self.f.config.write_text(json.dumps(self.config),encoding='utf-8')

    def test_explicit_protocol_and_old_default(self):
        from controlled_mad.config import load_config
        config=load_config(self.f.config)
        self.assertEqual(item_settings(config,None,'pilot-mock','pilot-00')['independent_rounds'],3)
        self.assertEqual(item_settings(config,None,'pilot-mock','pilot-00')['design'],'equal_rounds_v10')
        old=dict(config); old.pop('matrix_protocol')
        self.assertEqual(item_settings(old,None,'pilot-mock','pilot-00')['independent_rounds'],1)
        plan=api.plan(self.f.config)
        self.assertEqual(plan['expected_terminal_outputs'],48)
        self.assertEqual(plan['physical_tasks_upper']['natural_replies'],360)
        self.assertIn('I-T3',plan['matrix_completion_rule'])

    def test_three_questions_real_terminal_matrix_and_routing(self):
        from controlled_mad.analysis import analyze
        from controlled_mad.matrix import result_matrix, matrix_summary
        store=api.create_run(self.f.config,'equal-three',self.f.root)
        api.execute_ready(store,concurrency=2,max_calls=31,question_batch_size=2)
        before=set(store.selection()); starts=[]
        api.execute_ready(api.open_run('equal-three',self.f.root),concurrency=2,question_batch_size=2,
                          progress=lambda u:starts.append(u['task_id']) if u['event']=='start' else None)
        self.assertTrue(before.isdisjoint(starts))
        store=api.open_run('equal-three',self.f.root)
        graph=Planner(store,persist=False).build(); rows=result_matrix(store,graph)
        self.assertEqual(matrix_summary(rows)['complete_outputs'],48)
        self.assertEqual(matrix_summary(rows)['complete_items'],3)
        self.assertEqual({r['round'] for r in rows},{3})
        self.assertEqual({r['design'] for r in rows},{'equal_rounds_v10'})
        self.assertEqual(len({(r['item_id'],r['variant'],r['protocol']) for r in rows}),48)
        self.assertTrue(all(len(r['votes'])==3 and r['status']=='complete' for r in rows))
        for s in graph.series:
            self.assertEqual(s['states']['I']['1']['snapshot_id'],s['states']['M']['1']['snapshot_id'])
            self.assertEqual(set(s['states']['I']),{'1','2','3'})
            self.assertEqual(set(s['states']['M']),{'1','2','3'})
        debate=[t for t in graph.tasks.values() if t.stage in ('T1','T2','T3')]
        self.assertEqual(len(debate),360)
        for t in debate:
            if t.stage in ('T2','T3'):
                self.assertEqual(len(t.dependencies),3)
                self.assertEqual(len(t.context['delivered']),0 if t.context['protocol']=='I' else 2)
        visibility=store.get('inputs','visibility-plan')['matrix']
        self.assertEqual({r['round'] for r in visibility if r['branch']=='I'},{1,2,3})
        self.assertTrue(all(not r['new_sources'] for r in visibility if r['branch']=='I' and r['round']>1))
        analysis=analyze(store)
        self.assertTrue(all(r['independent_actual_round']==r['round'] for r in analysis['round_effects']))
        self.assertTrue(all(r['design']=='equal_rounds_v10' for r in analysis['summary_effects']))
        self.assertEqual(api.execute_ready(api.open_run('equal-three',self.f.root),question_batch_size=2)['executed'],0)
