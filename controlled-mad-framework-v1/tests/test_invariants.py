import json, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from controlled_mad.engine import create_run, execute_ready, status, reparse, fork_revision
from controlled_mad.scheduler import Planner
from controlled_mad.analysis import analyze
from controlled_mad.budget import BudgetError
from controlled_mad.store import RunStore
from controlled_mad.domain import ModelResult
from controlled_mad.backends.mock import MockBackend
ROOT=Path(__file__).resolve().parents[1]

class Invariants(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.config=json.loads((ROOT/'configs/demo.json').read_text())
        rows=(ROOT/'examples/toy_dataset.jsonl').read_text().splitlines()
        (self.path/'one.jsonl').write_text(rows[0]+'\n')
        self.config['dataset']['source']=str(self.path/'one.jsonl')
        self.config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        self.config['operations']=['ethos']; self.config['interventions']=[]; self.config['diagnostics']['oo']['enabled']=False
    def create(self):
        (self.path/'config.json').write_text(json.dumps(self.config))
        return create_run(self.path/'config.json','r',self.path)
    def test_network_gate(self):
        self.config['profiles']['mock']={'provider':'openai','model':'frozen-test','base_url':'https://example.test/v1','credential_env':'UNREAD_CREDENTIAL'}
        run=self.create()
        with self.assertRaises(BudgetError): execute_ready(run)
        self.assertEqual(status(run)['budget']['calls'],0)
    def test_crash_after_raw_commit(self):
        run=self.create(); task=next(iter(Planner(run).build().tasks.values())); aid=task.task_id+'-a001'
        script="""import os,sys
from controlled_mad.store import RunStore
s=RunStore(sys.argv[1])
s.event('started',task_id=sys.argv[2],attempt_id=sys.argv[3],stage='S0',provider='manual',reserved_tokens=999,wire_format='text')
s.write_blob('raw',sys.argv[3],b'{"label":"A","comment":"from child"}','.bin')
os._exit(23)
"""
        result=subprocess.run([sys.executable,'-c',script,str(run.path),task.task_id,aid],env={**__import__('os').environ,'PYTHONPATH':str(ROOT/'src')},capture_output=True)
        self.assertEqual(result.returncode,23)
        reopened=RunStore(run.path)
        execute_ready(reopened,max_calls=0)
        self.assertEqual(reopened.result(task.task_id)['value']['comment'],'from child')
    def test_reparse_used_parent_requires_revision(self):
        run=self.create(); execute_ready(run)
        oldgraph=Planner(run,persist=False).build()
        s0=next(t for t in oldgraph.tasks.values() if t.stage=='S0')
        selected=run.selection()[s0.task_id]
        candidate=reparse(run,s0.task_id,'vote-v2')
        self.assertTrue(candidate['requires_revision']); self.assertEqual(run.selection()[s0.task_id],selected)
        revised=fork_revision(run,s0.task_id,candidate['attempt_id'],parse_id=candidate['parse_id'])
        self.assertGreater(status(revised)['ready'],0)
        self.assertEqual(len(analyze(revised)['pairs']),0)
        self.assertEqual(len(analyze(run)['pairs']),1)
        execute_ready(revised)
        self.assertTrue(analyze(revised)['pairs'][0]['complete_case_pair'])
    def test_repeat_denominators(self):
        self.config['repeats']=2
        scenario=json.loads((ROOT/'examples/mock_scenario.json').read_text())
        scenario['overrides']=[{'match':{'stage':'T3','variant':'ethos','protocol':'M','repeat':1,'agent':'agent_1'},'text':'invalid JSON'}]
        (self.path/'scenario.json').write_text(json.dumps(scenario)); self.config['mock_scenario']=str(self.path/'scenario.json')
        run=self.create(); execute_ready(run)
        result=analyze(run)
        self.assertEqual(result['denominators']['complete_comparison_repeats'],1)
        self.assertEqual({r['complete_repeats'] for r in result['item_effects']},{1})
        self.assertEqual(len([r for r in result['round_effects'] if r['D'] is None]),3)
    def test_http_failure_is_not_material_quality_retry(self):
        self.config['max_generation_attempts']=2
        run=self.create(); original=MockBackend.generate
        def backend(self,task,profile):
            if task.stage=='generate': return ModelResult(b'{"error":"rate limit"}','',None,None,None,'HTTP 429')
            return original(self,task,profile)
        with patch.object(MockBackend,'generate',backend):
            self.assertEqual(execute_ready(run)['stop'],'backend_error')
            before=status(run)['budget']['calls']
            execute_ready(run)
        self.assertEqual(status(run)['budget']['calls'],before)
