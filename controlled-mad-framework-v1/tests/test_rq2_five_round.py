import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from controlled_mad import api, platform_io
from controlled_mad.analysis_rq2 import analyze_rq2, difference
from controlled_mad.analysis import aggregate
from controlled_mad.cli import main
from controlled_mad.config import validate_config
from controlled_mad.engine import create_run, execute_ready, status
from controlled_mad.identity import safe_id
from controlled_mad.scheduler import Planner

ROOT=Path(__file__).resolve().parents[1]

class FiveRound(unittest.TestCase):
    def test_subset_and_protocol(self):
        config_path=ROOT/'configs/rq2-five-round-demo.json'
        first=api.validate(config_path)
        self.assertEqual(first['subset'],api.validate(config_path)['subset'])
        self.assertEqual(first['items'],2)
        with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temporary:
            store=create_run(config_path,'five',temporary)
            planned=api.plan(config_path)['total_tasks_upper']
            self.assertEqual(status(store)['progress']['planned'],planned)
            execute_ready(store,max_calls=9)
            self.assertEqual(status(store)['succeeded'],9)
            execute_ready(store)
            self.assertEqual(status(store)['budget']['calls'],planned)
            self.assertEqual(status(store)['progress']['settled'],planned)
            graph=Planner(store,persist=False).build()
            self.assertTrue(all(set(c['O'])=={'M'} and set(c['R'])=={'M'} for c in graph.comparisons))
            self.assertTrue(all(c['O']['M']['5'] and c['R']['M']['5'] for c in graph.comparisons))
            self.assertEqual({t.stage for t in graph.tasks.values()},{'S0','generate','T1','T2','T3','T4','T5'})
            for comparison in graph.comparisons:
                for variant in ('O','R'):
                    arm=comparison[variant]['M']
                    for t in range(2,6):
                        self.assertEqual(arm[str(t)]['parent'],arm[str(t-1)]['snapshot_id'])
                        for history in arm[str(t)]['histories'].values():
                            self.assertGreater(len(history['messages']),len(arm[str(t-1)]['histories']['agent_1']['messages']))
            result=analyze_rq2(store)
            self.assertEqual(result['denominators']['complete_five_round_pairs'],6)
            self.assertEqual(len(result['late_changes']),6)
            self.assertTrue(all(row['delta'] is not None for row in result['round_effects']))
            self.assertFalse(any('D' in row for row in result['round_effects']))
            self.assertEqual(api.verify_run(store)['verified_tasks'],planned)
            self.assertEqual(len(api.export_trace(store,'toy-001'))>0,True)

    def test_config_guards_and_resume_rejects_change(self):
        c=json.loads((ROOT/'configs/rq2-five-round-demo.json').read_text(encoding='utf-8'))
        c['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
        c['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        for subset in ({'size':99,'seed':1},{'item_ids':['missing']},{'item_ids':['toy-001','toy-001']}):
            bad=copy.deepcopy(c); bad['subset']=subset
            if subset.get('size')==99:
                with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temporary:
                    path=Path(temporary)/'c.json'; path.write_text(json.dumps(bad),encoding='utf-8')
                    with self.assertRaises(ValueError): api.plan(path)
            elif subset['item_ids']==['missing']:
                with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temporary:
                    path=Path(temporary)/'c.json'; path.write_text(json.dumps(bad),encoding='utf-8')
                    with self.assertRaises(ValueError): api.plan(path)
            else:
                with self.assertRaises(ValueError): validate_config(bad)
        with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temporary:
            config_path=ROOT/'configs/rq2-five-round-demo.json'
            args=['rq2','--config',str(config_path),'--run-id','auto','--runs-root',temporary,'--max-calls','3']
            self.assertEqual(main(args),0)
            self.assertEqual(main(args),0)
            changed=copy.deepcopy(c); changed['selection_seed']=100
            path=Path(temporary)/'changed.json'
            changed['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
            changed['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
            path.write_text(json.dumps(changed),encoding='utf-8')
            self.assertEqual(main(['rq2','--config',str(path),'--run-id','auto','--runs-root',temporary,'--max-calls','0']),2)
            self.assertEqual(status(api.open_run('auto',temporary))['budget']['calls'],6)

    def test_no_majority_and_missing_effects(self):
        labels=('A','B','C')
        no_majority=aggregate(labels,labels)
        majority=aggregate(('A','A','B'),labels)
        self.assertEqual(no_majority['verdict'],'no_majority')
        self.assertEqual(difference(no_majority,majority)['verdict']['no_majority'],1)
        self.assertIsNone(difference(aggregate([],labels),majority))

    def test_windows_names_and_lock_branch(self):
        for value in ('CON','com1.txt','LPT9','name.'):
            with self.assertRaises(ValueError): safe_id(value)
        with tempfile.TemporaryFile('w+b') as stream:
            native=Mock()
            with patch.object(platform_io.os,'name','nt'),patch.object(platform_io,'msvcrt',native,create=True):
                with platform_io.exclusive_lock(stream): pass
            self.assertEqual(native.locking.call_count,2)
            native.locking.side_effect=PermissionError(13,'locked')
            with patch.object(platform_io.os,'name','nt'),patch.object(platform_io,'msvcrt',native,create=True):
                with self.assertRaises(platform_io.LockBusy):
                    with platform_io.exclusive_lock(stream): pass
            native.locking.side_effect=OSError(5,'I/O failure')
            with patch.object(platform_io.os,'name','nt'),patch.object(platform_io,'msvcrt',native,create=True):
                with self.assertRaises(OSError):
                    with platform_io.exclusive_lock(stream): pass
