import unittest
from controlled_mad.domain import Vote, HistoryState, Message, Intervention
from controlled_mad.s0 import freeze_s0
from controlled_mad.router import route
from controlled_mad.material_checks import reorder, check_material

class Protocol(unittest.TestCase):
    def test_s0(self):
        for labels in (('a','a','a'),('a','a','b'),('a','b','c')):
            votes=tuple(Vote(label,'理由',f't{i}',parse_id=f'p{i}') for i,label in enumerate(labels))
            state=freeze_s0(votes,{'dataset_id':'d','item_id':'i','seed':42})
            self.assertIn(state.sender,('agent_1','agent_2','agent_3'))
        with self.assertRaises(ValueError): freeze_s0(votes[:2],{})
    def test_route(self):
        peers=(Message('a','agent_1',1,'x','secret','p'),Message('b','agent_2',1,'y','public','q'))
        h=HistoryState((),())
        self.assertEqual(len(route(h,2,'I','agent_3',None,peers).delivered),0)
        delivery=route(h,2,'M','agent_3',{'source':'agent_1','target':'agent_3','round':2},peers)
        self.assertEqual([m.message_id for m in delivery.delivered],['b'])
        self.assertEqual(delivery.blocked,('a',))
    def test_reorder(self):
        original='支持。限制。结论。'
        blocks=[{'text':'支持。','kind':'support'},{'text':'限制。','kind':'limitation'},{'text':'结论。','kind':'conclusion'}]
        self.assertEqual(reorder(original,blocks,'conclusion_front'),'结论。支持。限制。')
        self.assertEqual(reorder(original,blocks,'limitation_first'),'限制。支持。结论。')
        with self.assertRaises(ValueError): reorder(original,blocks[:2],'conclusion_front')
        with self.assertRaises(ValueError): reorder('只有支持。',[{'text':'只有支持。','kind':'support'}],'limitation_first')
    def test_label_preserved(self):
        self.assertFalse(check_material('text','changed','x','y')['valid'])
    def test_self_edge(self):
        with self.assertRaises(ValueError): Intervention('r','S','S',2)
