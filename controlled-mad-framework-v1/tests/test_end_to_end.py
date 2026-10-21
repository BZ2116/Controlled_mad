import json, tempfile, unittest
from pathlib import Path
from controlled_mad.engine import create_run, execute_ready, status
from controlled_mad.scheduler import Planner
from controlled_mad.analysis import analyze
from controlled_mad.reporting import export_analysis, trace
from controlled_mad.interventions import resolve_rule

ROOT=Path(__file__).resolve().parents[1]
class EndToEnd(unittest.TestCase):
    def test_material_audit_can_be_disabled_for_new_run(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temporary:
            config=json.loads((ROOT/'configs/demo.json').read_text())
            config['material_audit']=False
            config['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
            config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
            config_path=Path(temporary)/'config.json'
            config_path.write_text(json.dumps(config))
            run=create_run(config_path,'no-audit',Path(temporary))
            execute_ready(run)
            graph=Planner(run).build()
            self.assertFalse(any(task.stage=='audit' for task in graph.tasks.values()))
            self.assertTrue(graph.materials)
            self.assertTrue(all(material['status']!='pending' for material in graph.materials))
            self.assertTrue(all('audit_verdict' not in material for material in graph.materials))
            self.assertTrue(all(material['status'] in ('eligible','no_change') for material in graph.materials if material['operation'] in ('ethos','logos','pathos')))
            self.assertEqual(status(run)['progress']['stages']['audit']['planned'],0)

    def test_legacy_config_without_audit_switch_keeps_audit(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temporary:
            config=json.loads((ROOT/'configs/demo.json').read_text())
            config.pop('material_audit')
            config['audit_model']='mock'
            config['budget']['stage_calls']['audit']=5000
            config['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
            config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
            config_path=Path(temporary)/'config.json'
            config_path.write_text(json.dumps(config))
            run=create_run(config_path,'legacy-audit',Path(temporary))
            execute_ready(run)
            graph=Planner(run).build()
            self.assertTrue(any(task.stage=='audit' for task in graph.tasks.values()))

    def test_pipeline(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temporary:
            run=create_run(ROOT/'configs/demo.json','test-run',Path(temporary))
            execute_ready(run)
            state=status(run)
            self.assertEqual(state['ready'],0)
            graph=Planner(run).build()
            self.assertTrue(graph.comparisons)
            self.assertTrue(all(c['O']['I']['3'] and c['R']['M']['3'] for c in graph.comparisons))
            for c in graph.comparisons:
                self.assertEqual(c['O']['I']['1']['snapshot_id'],c['O']['M']['1']['snapshot_id'])
            self.assertTrue(any(m['status']=='no_change' for m in graph.materials))
            self.assertFalse(any(m.get('audit_verdict') for m in graph.materials))
            self.assertEqual(state['real_http_calls'],0)
            accepted=sum(m['status']=='eligible' for m in graph.materials)
            # 每臂自然15 + T2干预6 + T3干预3；两批诊断每题30。
            self.assertEqual(sum(t.stage in ('T1','T2','T3') for t in graph.tasks.values()),24*(6+accepted)+30*6)
            shared={}
            for c in graph.comparisons:
                key=(c['context']['item_id'],c['context']['repeat'])
                original=c['O']['M']['3']['snapshot_id']
                self.assertEqual(shared.setdefault(key,original),original)
                rule=resolve_rule(run.config['interventions'][0],c['context']['sender'])
                blocked=next(r['parse_id'] for r in c['R']['M']['1']['replies'] if r['agent']==rule['source'])
                history=c['R']['interventions'][rule['id']]['3']['histories'][rule['target']]['seen']
                self.assertNotIn(blocked,history)
            bundle=analyze(run)
            self.assertTrue(all(all(abs(x)<1e-12 for x in row['D']['vote'].values()) for row in bundle['round_effects'] if row['round']==1))
            process=next(r for r in bundle['round_effects'] if r['item_id']=='toy-001' and r['operation']=='ethos' and r['round']==3)
            self.assertAlmostEqual(process['D']['vote']['A'],-1/3)
            self.assertEqual(process['D']['verdict']['A'],0)
            self.assertEqual(len(bundle['oo_diagnostics']),36)
            self.assertTrue(all(row['complete'] for row in bundle['oo_diagnostics']))
            exported=export_analysis(bundle,run)
            self.assertTrue(Path(exported['report']).exists())
            self.assertTrue(Path(trace(run,'toy-001')).exists())
            self.assertGreater(run.verify()['verified_objects'],0)
