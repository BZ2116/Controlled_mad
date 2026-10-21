import contextlib, io, tempfile, unittest
from pathlib import Path
from controlled_mad.cli import main
ROOT=Path(__file__).resolve().parents[1]
class CLI(unittest.TestCase):
    def test_validate_plan_init_status(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')) as temp:
            for args in (["validate","--config",str(ROOT/'configs/demo.json')],["plan","--config",str(ROOT/'configs/demo.json')],["init","--config",str(ROOT/'configs/demo.json'),"--run-id","cli","--runs-root",temp],["status","--run-id","cli","--runs-root",temp]):
                with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(main(args),0)
    def test_bad_config(self):
        with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(main(['validate','--config','/missing/config.json']),2)
