import unittest
from controlled_mad.domain import Item, ModelProfile
from controlled_mad.identity import canonical_hash, select_sender
from controlled_mad.config import validate_config

class Contracts(unittest.TestCase):
    def test_native_labels(self):
        with self.assertRaises(ValueError):
            Item('d', 'i', 'c', ('e',), ('no_majority',))
        with self.assertRaises(ValueError):
            Item('d', 'i', 'c', ('e',), ('YES', 'yes'))
    def test_identity(self):
        self.assertEqual(canonical_hash({'a':1,'b':2}),canonical_hash({'b':2,'a':1}))
        self.assertEqual(select_sender('d','i',42),select_sender('d','i',42))
        self.assertEqual(len({select_sender('d','i',n) for n in range(100)}),3)
    def test_reject_unknown(self):
        with self.assertRaises(ValueError): validate_config({'unknown':1})
    def test_no_gold_field(self):
        with self.assertRaises(TypeError): Item('d','i','c',('e',),('a','b'),gold='a')
