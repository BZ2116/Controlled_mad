import contextlib, copy, io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from controlled_mad.engine import create_run, execute_ready, status
from controlled_mad.cli import ProgressReporter
from controlled_mad.backends.transport import TransportUnknown
from controlled_mad.api import plan

ROOT=Path(__file__).resolve().parents[1]

class Progress(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp'))
        self.addCleanup(self.temp.cleanup)
        config=json.loads((ROOT/'configs/demo.json').read_text())
        config['dataset']['source']=str(ROOT/'examples/toy_dataset.jsonl')
        config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        self.config=Path(self.temp.name)/'config.json'
        self.config.write_text(json.dumps(config))
        self.store=create_run(self.config,'progress',Path(self.temp.name))

    def check_partition(self,p):
        for row in (p,*p['stages'].values()):
            self.assertEqual(row['planned'],sum(row[k] for k in ('succeeded','ready','invalid','transport_unknown','cancelled','waiting','running')))
            self.assertTrue(all(value>=0 for value in row.values() if isinstance(value,int)))

    def test_fixed_total_resume_and_final_cancelled(self):
        expected=plan(self.config)['total_tasks_upper']
        before=status(self.store)['progress']
        self.assertEqual(before['planned'],expected)
        updates=[]
        execute_ready(self.store,max_calls=4,progress=lambda u:updates.append(copy.deepcopy(u)))
        first=status(self.store)['progress']
        self.assertEqual(first['succeeded'],4)
        execute_ready(self.store,max_calls=2)
        second=status(self.store)['progress']
        self.assertEqual(second['succeeded'],6)
        execute_ready(self.store)
        final=status(self.store)['progress']
        self.assertEqual(final['planned'],expected)
        self.assertGreater(final['cancelled'],0)
        self.assertEqual(final['settled'],expected)
        self.assertEqual(final['waiting'],0)
        for p in (before,first,second,final): self.check_partition(p)
        for u in updates:
            if u.get('overall'):
                self.assertEqual(u['overall']['planned'],expected)
                self.check_partition(u['overall'])

    def test_unknown_does_not_advance_progress(self):
        with patch('controlled_mad.backends.mock.MockBackend.generate',side_effect=TransportUnknown('未知')):
            execute_ready(self.store,max_calls=1)
        p=status(self.store)['progress']
        self.assertEqual(p['settled'],0)
        self.assertEqual(p['transport_unknown'],1)
        execute_ready(self.store,max_calls=1,auto_retry_unknown=True)
        p=status(self.store)['progress']
        self.assertEqual(p['succeeded'],1)
        self.assertEqual(p['transport_unknown'],0)
        self.assertEqual(status(self.store)['budget']['calls'],2)
        self.check_partition(p)

    def test_terminal_refreshes_one_line(self):
        output=io.StringIO()
        with contextlib.redirect_stderr(output):
            reporter=ProgressReporter(); reporter.tty=True
            p=status(self.store)['progress']
            reporter({'event':'batch','overall':p,'completed':0})
            reporter({'event':'batch','overall':p,'completed':0})
            reporter.close()
        text=output.getvalue()
        self.assertEqual(text.count('\r'),2)
        self.assertNotIn('\033',text)
        self.assertEqual(text.count('\n'),1)

    def test_question_number_is_visible_in_narrow_terminal(self):
        output=io.StringIO()
        with contextlib.redirect_stderr(output):
            reporter=ProgressReporter(); reporter.tty=True
            p=status(self.store)['progress']
            p['items'].update(total=500,current='averitec_0016',position=17,
                              active=['averitec_0016','averitec_0017','averitec_0018'],active_positions=[17,18,19])
            with patch('controlled_mad.cli.shutil.get_terminal_size',return_value=__import__('os').terminal_size((80,24))):
                reporter({'event':'start','overall':p,'completed':0,'stage':'T1',
                          'item_id':'averitec_0016','item_position':17,'agent':'agent_1'})
            reporter.close()
        self.assertIn('第17/500题',output.getvalue())
