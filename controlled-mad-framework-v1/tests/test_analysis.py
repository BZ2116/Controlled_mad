import unittest
from controlled_mad.analysis import aggregate, four_cell_difference

class Analysis(unittest.TestCase):
    def test_majority_and_missing(self):
        self.assertEqual(aggregate(['a','b','c'],('a','b','c'))['verdict'],'no_majority')
        self.assertEqual(aggregate(['a','b'],('a','b','c'))['status'],'missing')
        self.assertEqual(aggregate(['a','a','b'],('a','b'))['verdict'],'a')
    def test_exact_difference(self):
        m_r=aggregate(['a']*3,('a','b'))
        other=aggregate(['b']*3,('a','b'))
        d=four_cell_difference(m_r,other,other,other)
        self.assertEqual(d['vote'],{'a':1.0,'b':-1.0})
        self.assertEqual(d['verdict'],{'a':1.0,'b':-1.0,'no_majority':0.0})
    def test_zero(self):
        x=aggregate(['a','b','c'],('a','b','c'))
        self.assertTrue(all(v==0 for v in four_cell_difference(x,x,x,x)['verdict'].values()))
