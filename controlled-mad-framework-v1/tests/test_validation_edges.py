import copy,json,unittest
from pathlib import Path
from controlled_mad.config import validate_config
from controlled_mad.datasets.jsonl import from_rows
ROOT=Path(__file__).resolve().parents[1]
class ValidationEdges(unittest.TestCase):
    def test_evidence_not_string(self):
        b=from_rows([{'claim':'claim','item_id':'i','evidence':'abc','labels':['a','b']}])
        self.assertEqual(len(b.items),0)
    def test_nan_and_unknown_nested_fields(self):
        base=json.loads((ROOT/'configs/demo.json').read_text())
        bad=copy.deepcopy(base); bad['profiles']['mock']['temperature']=float('nan')
        with self.assertRaises(ValueError): validate_config(bad)
        bad=copy.deepcopy(base); bad['budget']['max_calls']=True
        with self.assertRaises(ValueError): validate_config(bad)
    def test_real_endpoint_has_no_embedded_secret(self):
        bad=json.loads((ROOT/'configs/demo.json').read_text())
        bad['profiles']['mock']={'provider':'openai','model':'m','base_url':'https://name:password@example.test/v1?api_key=secret','credential_env':'TEST'}
        with self.assertRaises(ValueError): validate_config(bad)
    def test_reasoning_effort_is_openai_only_and_bounded(self):
        good=json.loads((ROOT/'configs/demo.json').read_text())
        good['profiles']['mock']={'provider':'openai','model':'m','base_url':'https://example.test/v1','credential_env':'TEST','reasoning_effort':'max'}
        validate_config(good)
        bad=copy.deepcopy(good); bad['profiles']['mock']['reasoning_effort']='extreme'
        with self.assertRaises(ValueError): validate_config(bad)
        bad=copy.deepcopy(good); bad['profiles']['mock']['provider']='anthropic'
        with self.assertRaises(ValueError): validate_config(bad)
