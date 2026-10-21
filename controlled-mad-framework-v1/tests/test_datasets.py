import json, unittest
from pathlib import Path
from controlled_mad.datasets.jsonl import from_rows
from controlled_mad.datasets.averitec import convert
from controlled_mad.identity import plain
from controlled_mad.parsing import parse_vote

class Data(unittest.TestCase):
    def test_gold_isolation(self):
        row={'dataset_id':'d','item_id':'a','claim':'c','evidence':['e'],'labels':['yes','no'],'gold':'yes','justification':'SECRET'}
        b=from_rows([row])
        self.assertNotIn('SECRET',json.dumps(plain(b.items)))
        self.assertNotIn('gold',plain(b.items[0]))
        self.assertEqual(b.gold[0].label,'yes')
        with self.assertRaises(ValueError): from_rows([row,row])
    def test_empty_evidence_rejected_with_denominator(self):
        b=from_rows([{'item_id':'a','claim':'c','evidence':[],'labels':['y','n']}])
        self.assertEqual(len(b.rejected),1)
    def test_averitec(self):
        b=convert([{'claim':'c','label':'Supported','justification':'SECRET','questions':[{'question':'q','answers':[{'answer':'a','source_url':'https://e.test'}]}]}])
        self.assertEqual(b.items[0].item_id,'averitec_0000')
        self.assertEqual(len(b.items[0].labels),4)
        self.assertNotIn('SECRET',json.dumps(plain(b.items)))
    def test_parser(self):
        self.assertTrue(parse_vote('{"label":"YES","comment":"理由"}',('yes','no'),'vote-v1')['valid'])
        for raw in ('Not enough evidence','{"label":"Unsupported","comment":"否定"}','{"label":"yes"}','{"label":"yes","comment":""}'):
            self.assertFalse(parse_vote(raw,('yes','no'),'vote-v1')['valid'])
