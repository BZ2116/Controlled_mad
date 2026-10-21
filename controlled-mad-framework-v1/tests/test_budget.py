import tempfile, unittest
from pathlib import Path
from controlled_mad.store import RunStore
from controlled_mad.domain import ModelProfile, Task, ModelResult
from controlled_mad.budget import Budget, BudgetError, usage_tokens

class Limits(unittest.TestCase):
    def test_usage(self):
        self.assertEqual(usage_tokens({'input_tokens':2,'output_tokens':3,'cache_read_input_tokens':4,'cache_creation_input_tokens':5}),14)
        self.assertIsNone(usage_tokens({}))
        self.assertIsNone(usage_tokens({'prompt_tokens':-1,'completion_tokens':2}))
    def test_reservation(self):
        with tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[2]/'.tmp')) as tmp:
            store=RunStore.create(Path(tmp),'r',{'budget':{'max_calls':1,'max_tokens':10000,'stage_calls':{'S0':1}}})
            task=Task('t','S0','m',({'role':'user','content':'hello'},),(),{},'h')
            budget=Budget(store); bound=budget.reserve(task,ModelProfile('m'))
            store.event('started',task_id='t',attempt_id='a',stage='S0',reserved_tokens=bound)
            self.assertEqual(budget.totals()['tokens'],bound)
            with self.assertRaises(BudgetError): budget.reserve(task,ModelProfile('m'))
            budget.reconcile('a',ModelResult(b'','',None,None,{'input_tokens':2,'output_tokens':1}))
            self.assertEqual(budget.totals()['tokens'],3)
            self.assertIsNone(budget.totals()['cost'])
