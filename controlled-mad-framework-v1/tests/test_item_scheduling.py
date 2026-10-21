import json, tempfile, unittest
from pathlib import Path
from controlled_mad.engine import create_run, execute_ready, status, submit
from controlled_mad.scheduler import Planner

ROOT=Path(__file__).resolve().parents[1]

class ItemScheduling(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp'))
        self.addCleanup(self.temp.cleanup)
        config=json.loads((ROOT/'configs/demo.json').read_text())
        config['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
        config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        config['operations']=['ethos']; config['interventions']=[]
        config['diagnostics']['oo']['enabled']=False
        path=Path(self.temp.name)/'config.json'; path.write_text(json.dumps(config))
        self.run=create_run(path,'item-test',Path(self.temp.name))

    def test_first_item_finishes_before_second_starts(self):
        seen=[]
        execute_ready(self.run,max_calls=34,progress=lambda u:seen.append(u['item_id']) if u['event']=='start' else None)
        self.assertEqual(set(seen),{'toy-001'})
        graph=Planner(self.run,persist=False).build()
        first=[c for c in graph.comparisons if c['context']['item_id']=='toy-001']
        self.assertEqual(len(first),1)
        self.assertTrue(all(first[0][arm][protocol]['3'] for arm in ('O','R') for protocol in ('I','M')))
        item_progress=status(self.run)['progress']['items']
        self.assertEqual(item_progress['complete'],1)
        self.assertEqual(item_progress['total'],6)
        execute_ready(self.run,max_calls=1,progress=lambda u:seen.append(u['item_id']) if u['event']=='start' else None)
        self.assertEqual(seen[-1],'toy-002')

    def test_started_item_precedes_untouched_item(self):
        graph=Planner(self.run).build()
        task=next(t for t in graph.tasks.values() if t.context['item_id']=='toy-002')
        submit(self.run,task.task_id,b'{"label":"A","comment":"existing reply"}')
        seen=[]
        execute_ready(self.run,max_calls=1,progress=lambda u:seen.append(u['item_id']) if u['event']=='start' else None)
        self.assertEqual(seen,['toy-002'])
        self.assertEqual(len(self.run.attempts(task.task_id)),1)

    def test_blocked_item_does_not_hold_next_item(self):
        task=next(iter(Planner(self.run).build().tasks.values()))
        submit(self.run,task.task_id,b'invalid')
        seen=[]
        execute_ready(self.run,max_calls=3,progress=lambda u:seen.append(u['item_id']) if u['event']=='start' else None)
        self.assertEqual(seen,['toy-001','toy-001','toy-002'])

    def test_incremental_view_matches_full_rebuild(self):
        graph=Planner(self.run,persist=False).build()
        for calls in (3,2,6,12,12):
            execute_ready(self.run,max_calls=calls)
            first=self.run.get('inputs','items')['items'][0]
            graph.refresh_item((first['dataset_id'],first['item_id']))
            full=Planner(self.run,persist=False).build()
            self.assertEqual(graph.tasks,full.tasks)
            for name in ('materials','comparisons','series','blocked'):
                normalize=lambda rows:sorted(json.dumps(row,sort_keys=True) for row in rows)
                self.assertEqual(normalize(getattr(graph,name)),normalize(getattr(full,name)))
