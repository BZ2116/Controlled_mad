import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import test_complete_matrix as fixture
from controlled_mad import api
from controlled_mad.analysis import analyze
from controlled_mad.coding import FIELDS as REPLY_FIELDS
from controlled_mad.material_validation import FIELDS,STYLES,CODEBOOK,validate,report,validation_id
from controlled_mad.matrix import matrix_summary,reorder_structured
from controlled_mad.scheduler import Planner
from controlled_mad.visibility import audit_visibility

VERSION='continued_peer_exchange_v2'


class MaterialValidationUnits(unittest.TestCase):
    def test_uncertain_counts_are_separate_from_no_and_missing(self):
        material={'material_id':'m1','item_id':'one','model_id':'m','operation':'ethos','original':'Original.','rewritten':'Rewritten.'}
        identifier=validation_id(material)
        rows=[{'validation_id':identifier,'coder_id':c,'codes':{f:'uncertain' for f in FIELDS},
               'style_scores':{v:{s:3 for s in STYLES} for v in ('original','rewritten')}} for c in ('coder-a','coder-b')]
        store=SimpleNamespace(config={'operations':['ethos','logos'],'debate_models':['m']},
                              ids=lambda collection:['material-validation-batch-fixture'],
                              get=lambda collection,key:{'selected_item_ids':['one']} if collection=='inputs' else {'rows':rows})
        graph=SimpleNamespace(materials=[material])
        result=report(store,graph)
        summary={r['operation']:r for r in result['operation_summaries']}
        self.assertEqual(summary['ethos']['semantic_fields']['facts_preserved']['both_uncertain'],1)
        self.assertEqual(summary['ethos']['semantic_fields']['facts_preserved']['both_no'],0)
        self.assertEqual(summary['logos']['planned_pairs'],1)
        self.assertEqual(summary['logos']['available_pairs'],0)

    def test_literal_per_field_evidence_and_scores_are_required(self):
        packet=[{'validation_id':'id','original':'Original sentence.','rewritten':'Rephrased sentence.'}]
        row={'validation_id':'id','coder_id':'coder-a','codes':{f:'uncertain' for f in FIELDS},
             'style_scores':{v:{s:3 for s in STYLES} for v in ('original','rewritten')},
             'rationale':{f:{'source':'original','quote':'Original sentence.','note':'Synthetic fixture only.'} for f in (*FIELDS,*STYLES)}}
        self.assertEqual(validate([row],packet,[]),[row])
        bad=copy.deepcopy(row); bad['rationale']['facts_preserved']['quote']='invented quotation'
        with self.assertRaises(ValueError): validate([bad],packet,[])
        bad=copy.deepcopy(row); bad['style_scores']['original']['ethos']=6
        with self.assertRaises(ValueError): validate([bad],packet,[])
        bad=copy.deepcopy(row); bad['rationale'].pop('pathos')
        with self.assertRaises(ValueError): validate([bad],packet,[])
        with self.assertRaises(ValueError): validate([row,row],packet,[])

    def test_reorder_moves_content_without_creating_extra_separators(self):
        for suffix in ('\n','\r\n'):
            original=fixture.COMMENT+suffix
            result=reorder_structured(original,'conclusion_front','group_first_v2',normalize=True)
            self.assertTrue(result.startswith('Conclusion:'))
            self.assertTrue(result.endswith(suffix))
            self.assertNotIn('\n\n\n',result)
            self.assertEqual(sorted(result.rstrip().split('\n\n')),sorted(original.rstrip().split('\n\n')))


class StudyV2Delivery(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CompleteMatrix(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.config=copy.deepcopy(self.f.config_value)
        self.config.update(study_protocol=VERSION,matrix_layout='group_first_v2',matrix_protocol='equal_rounds_v10',
                           subset={'item_ids':['pilot-00','pilot-01','pilot-02']},
                           coding={'size':3,'seed':20261022,'replies_per_item':12},material_validation={'size':3,'seed':20261022},
                           inference={'resamples':99,'seed':20261022,'alpha':.05})
        self.save()

    def save(self):
        self.f.config.write_text(json.dumps(self.config),encoding='utf-8')

    def test_three_question_resume_identity_packets_and_report(self):
        store=api.create_run(self.f.config,'v2-resumed',self.f.root)
        api.execute_ready(store,max_calls=17,concurrency=3,question_batch_size=2)
        before=set(store.selection()); started=[]
        def progress(update):
            if update['event']=='start':
                started.append(update['task_id'])
                if update['item_position']==3: self.assertEqual(update['overall']['items']['complete'],2)
        store=api.open_run('v2-resumed',self.f.root)
        api.execute_ready(store,concurrency=3,progress=progress) # omitted batch size must restore 2
        self.assertTrue(before.isdisjoint(started))
        self.assertEqual([e for e in store.all('events') if e['kind']=='execution_policy'][-1]['question_batch_size'],2)
        status=api.matrix_status(store)
        self.assertEqual(status['summary']['expected_outputs'],48)
        self.assertEqual(status['summary']['complete_outputs'],48)
        truncated=matrix_summary(status['result_matrix'][:-1],store)
        self.assertEqual(truncated['missing_rows'],1)
        self.assertEqual(truncated['complete_items'],2)
        graph=Planner(store,persist=False).build()
        self.assertTrue(audit_visibility(store,graph)['valid'])
        plan=store.get('inputs','visibility-plan')['matrix']
        self.assertEqual(len([r for r in plan if r['round']==1]),3)
        serial=api.create_run(self.f.config,'v2-serial',self.f.root)
        api.execute_ready(serial,concurrency=1)
        for collection in ('tasks','raw','results','parses','materials','snapshots','messages','deliveries','comparisons'):
            self.assertEqual(store.ids(collection),serial.ids(collection),collection)
        materials=api.export_material_validation(store)
        self.assertEqual(materials['pairs'],21)
        self.assertEqual(api.material_validation_status(store)['double_coded'],0)
        replies=api.export_coding(store)
        self.assertEqual(replies['replies'],36)
        self.assertEqual(api.coding_status(store)['double_coded'],0)
        reply=json.loads(Path(replies['path']).read_text(encoding='utf-8').splitlines()[0])
        annotation={'coding_id':reply['coding_id'],'coder_id':'coder-a','codes':{f:'uncertain' for f in REPLY_FIELDS},
                    'rationale':{f:{'source':'reply','quote':reply['reply']['comment'],'note':'Synthetic fixture only.'} for f in REPLY_FIELDS}}
        reply_path=self.f.root/'reply-annotations.jsonl';reply_path.write_text(json.dumps(annotation)+'\n',encoding='utf-8')
        api.import_coding(store,reply_path)
        self.assertEqual(api.coding_status(store)['missing_by_coder']['coder-a'],35)
        # Import two explicitly synthetic fixture annotations; no real human judgments are fabricated.
        p=json.loads(Path(materials['path']).read_text(encoding='utf-8').splitlines()[0])
        annotations=[]
        for coder in ('coder-a','coder-b'):
            annotations.append({'validation_id':p['validation_id'],'coder_id':coder,'codes':{f:'yes' for f in FIELDS},
                                'style_scores':{v:{s:3 for s in STYLES} for v in ('original','rewritten')},
                                'rationale':{f:{'source':'original','quote':p['original'],'note':'Synthetic test fixture only.'} for f in (*FIELDS,*STYLES)}})
        path=self.f.root/'annotations.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in annotations),encoding='utf-8')
        api.import_material_validation(store,path)
        self.assertEqual(api.material_validation_status(store)['double_coded'],1)
        result=analyze(store)
        self.assertTrue(all(r['p_value'] is None for r in result['inference']))
        self.assertTrue(result['expression_effects'])
        self.assertTrue(result['initial_outcome_summaries'])
        exported=api.analyze_and_export(store)
        text=Path(exported['report']).read_text(encoding='utf-8')
        for token in ('R−O','D 点估计','初态分层','人工验证','家族','constant_vectors'): self.assertIn(token,text)
        self.assertEqual(api.execute_ready(store)['executed'],0)
        self.assertEqual(api.status(store)['real_http_calls'],0)

    def test_reply_sampling_frozen_before_outputs_and_missing_not_replaced(self):
        self.config['coding']['replies_per_item']=12;self.save()
        store=api.create_run(self.f.config,'preselected',self.f.root)
        plan=store.get('inputs','coding-plan')
        self.assertEqual(len(plan['planned_reply_units']),36)
        status=api.coding_status(store)
        self.assertEqual(status['planned_replies'],36)
        self.assertEqual(status['unavailable_planned_replies'],36)
        self.assertEqual(status['double_coded'],0)
        self.assertTrue(Path(status['report_path']).exists())
        self.assertEqual(api.material_validation_status(store)['available_pairs'],0)

    def test_neutral_and_oo_are_separate_valid_controls(self):
        self.config['complete_matrix']=False
        self.config.pop('matrix_layout');self.config.pop('matrix_protocol')
        self.config.pop('coding');self.config.pop('material_validation')
        self.config['coding']={'size':1,'seed':20261022,'replies_per_item':12}
        self.config.update(operations=['neutral'],subset={'item_ids':['pilot-00']},diagnostics={'oo':{'enabled':True,'repeats':1}})
        self.save();store=api.create_run(self.f.config,'neutral',self.f.root)
        executed=api.execute_ready(store,item_pipeline=True)
        self.assertEqual(executed['executed'],65)
        result=analyze(store)
        self.assertEqual(result['denominators']['complete_comparison_repeats'],1)
        self.assertEqual(len(result['oo_diagnostics']),6)
        self.assertTrue(all(r['complete'] for r in result['oo_diagnostics']))
        self.assertTrue(result['visibility_audit']['valid'])
        self.assertEqual(result['coding_agreement']['packet_replies'],12)
        self.assertEqual(result['coding_agreement']['unavailable_planned_replies'],0)

    def test_directed_message_control_is_audited(self):
        self.config['complete_matrix']=False
        self.config.pop('matrix_layout');self.config.pop('matrix_protocol')
        self.config.pop('coding');self.config.pop('material_validation')
        self.config.update(operations=['logos'],subset={'item_ids':['pilot-00']},
                           interventions=[{'rule_id':'S_to_A_T2','source_agent':'S','target_agent':'A','delivery_round':2}])
        self.save();store=api.create_run(self.f.config,'message-control',self.f.root)
        result=api.execute_ready(store,item_pipeline=True)
        self.assertEqual(result['executed'],47)
        bundle=analyze(store)
        self.assertEqual(len(bundle['intervention_effects']),3)
        self.assertTrue(bundle['visibility_audit']['valid'])
