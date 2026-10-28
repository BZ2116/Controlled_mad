import copy
import json
import unittest
from pathlib import Path

import test_complete_matrix as fixture
from controlled_mad.config import validate_config
from controlled_mad.inference import infer_effects
from controlled_mad.material_checks import check_material
from controlled_mad.runner import run_main

VERSION = 'continued_peer_exchange_v2'


class StudyV2Units(unittest.TestCase):
    def effects(self, values, operation='logos'):
        return [{'dataset_id':'d', 'model_id':'m', 'operation':operation, 'round':3,
                 'item_id':str(i), 'D':{layer:{'A':x,'B':-x} for layer in ('vote','verdict')}}
                for i,x in enumerate(values)]

    def test_degenerate_is_descriptive_and_excluded_from_inference(self):
        settings={'resamples':199,'seed':20261022,'alpha':.05}
        for value in (0,1):
            rows=infer_effects(self.effects([value]*10),settings,version=VERSION)
            self.assertTrue(all(r['p_value'] is None and r['confidence_intervals'] is None for r in rows))
            self.assertEqual(rows[0]['estimate']['A'],value)
            self.assertNotEqual(rows[0]['inference_status'],'ok')
        # Historical behavior is retained only for historical configurations.
        self.assertEqual(infer_effects(self.effects([0]*10),settings)[0]['p_value'],1)

    def test_missing_and_degenerate_tests_remain_in_declared_family(self):
        effects=self.effects([1,0]*10)+self.effects([1]*10,'ethos')
        effects += [{'dataset_id':'d','model_id':'m','operation':'pathos','round':3,'item_id':'missing','D':None}]
        out=infer_effects(effects,{'resamples':199,'seed':20261022,'alpha':.05},version=VERSION)
        primary={r['operation']:r for r in out if r['primary']}
        self.assertEqual(set(primary),{'logos','ethos','pathos'})
        self.assertEqual(primary['pathos']['independent_units'],0)
        self.assertEqual(primary['logos']['holm_family_size'],3)
        self.assertEqual(primary['logos']['holm_estimable_family_size'],1)
        self.assertGreaterEqual(primary['logos']['holm_p_value'],primary['logos']['p_value'])
        self.assertLess(primary['logos']['holm_p_value'],.05)
        self.assertEqual(primary['logos']['estimate']['A'],.5)
        low,high=primary['logos']['confidence_intervals']['A']
        self.assertTrue(0<low<high<1)

    def test_paper_wide_family_is_recomputed_not_per_run(self):
        import importlib.util
        path=Path(__file__).resolve().parents[1]/'tools/combine_study_inference.py'
        spec=importlib.util.spec_from_file_location('combine_study',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def bundle(model,p):
            return {'study_protocol':VERSION,'model_profiles':{'m':{'model':model}},
                    'inference':[{'primary':True,'dataset_id':'d','model_id':'m','operation':'logos','p_value':p}]}
        result=module.combine([bundle('a',.03),bundle('b',None)])
        self.assertEqual(result['family_size'],2)
        self.assertEqual(result['tests'][0]['paper_holm_p_value'],.06)
        self.assertIsNone(result['tests'][1]['paper_holm_p_value'])
        with self.assertRaises(ValueError): module.combine([bundle('a',.03),bundle('a',.03)])

    def test_normalized_equivalents_and_real_new_information(self):
        for original,rewritten in [('1,000 and 3.5','1000 and 3.50'),
                                   ('https://EXAMPLE.com/path.','https://example.com/path'),
                                   ('5 percent','5%')]:
            c=check_material(original,rewritten,'A','A',version=VERSION)
            self.assertEqual(c['new_numbers'],[])
            self.assertEqual(c['new_urls'],[])
        for original,rewritten,key in [('3.5','35','new_numbers'),('5','5%','new_numbers'),
                                      ('no source','www.example.com','new_urls'),
                                      ('no source','example.com/source','new_urls'),
                                      ('https://example.com/Path','https://example.com/path','new_urls')]:
            self.assertTrue(check_material(original,rewritten,'A','A',version=VERSION)[key])
        self.assertTrue(check_material('3.5','3.50','A','A')['new_numbers'])

    def test_new_protocol_requires_explicit_equal_round_layout(self):
        f=fixture.CompleteMatrix(); f.setUp(); self.addCleanup(f.doCleanups)
        c=copy.deepcopy(f.config_value)
        c.update(study_protocol=VERSION,matrix_protocol='equal_rounds_v10',matrix_layout='group_first_v2')
        self.assertEqual(validate_config(c),c)
        for key in ('matrix_protocol','matrix_layout'):
            bad=copy.deepcopy(c); bad.pop(key)
            with self.assertRaises(ValueError): validate_config(bad)

    def test_large_number_coefficients_keep_all_digits(self):
        original='1234567890123456789012345678901'
        rewritten='1234567890123456789012345678902'
        self.assertTrue(check_material(original,rewritten,'A','A',version=VERSION)['new_numbers'])
        huge=check_material('1e1000000000','2e1000000000','A','A',version=VERSION)
        self.assertTrue(huge['new_numbers'])
        self.assertLess(len(huge['new_numbers'][0]),30)

    def test_single_pass_is_rejected_before_creating_run(self):
        f=fixture.CompleteMatrix(); f.setUp(); self.addCleanup(f.doCleanups)
        c=copy.deepcopy(f.config_value); c.update(matrix_protocol='equal_rounds_v10',matrix_layout='group_first_v2')
        f.config.write_text(json.dumps(c),encoding='utf-8')
        with self.assertRaises(ValueError):
            run_main(f.config,'must-not-exist',f.root,prepare_only=True,single_pass=True)
        self.assertFalse((f.root/'must-not-exist').exists())
