import unittest, tempfile
from pathlib import Path
from controlled_mad.store import RunStore, IntegrityError

class Storage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[2]/'.tmp'))
        self.addCleanup(self.temp.cleanup)
        self.store=RunStore.create(Path(self.temp.name),'r',{'x':1})
    def test_exclusive_and_idempotent(self):
        with self.assertRaises(FileExistsError): RunStore.create(Path(self.temp.name),'r',{})
        self.store.put('tasks','a',{'x':1})
        self.store.put('tasks','a',{'x':1})
        with self.assertRaises(IntegrityError): self.store.put('tasks','a',{'x':2})
    def test_corruption(self):
        self.store.put('tasks','a',{'x':1})
        (self.store.path/'tasks/a.json').write_text('{}')
        with self.assertRaises(IntegrityError): self.store.get('tasks','a')
    def test_uncommitted(self):
        (self.store.path/'tasks/a.json').write_text('{}')
        with self.assertRaises(IntegrityError): self.store.get('tasks','a')
    def test_lock(self):
        with self.store.lock():
            with self.assertRaises(IntegrityError):
                with RunStore(self.store.path).lock(): pass
    def test_events(self):
        self.store.event('selected',task_id='a',parse_id='p')
        self.assertEqual(self.store.selection()['a'],'p')
