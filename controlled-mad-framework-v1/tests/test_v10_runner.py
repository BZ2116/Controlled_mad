import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controlled_mad.runner import load_credentials, run_main


class Runner(unittest.TestCase):
    def test_explicit_commented_file_maps_key_without_changing_provider(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / '.tmp') as tmp:
            path = Path(tmp) / 'credentials.txt'
            path.write_text('# OPENAI_BASE_URL=https://provider.test/v1\n# MODEL_NAME=locked\n# MODEL_API_KEY=testing-only-value\n', encoding='utf-8')
            profile = {'base_url': 'https://provider.test/v1', 'model': 'locked', 'credential_env': 'TASK_TEST_KEY'}
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(load_credentials(path, profile))
                self.assertEqual(os.environ['TASK_TEST_KEY'], 'testing-only-value')
            profile['model'] = 'different'
            with self.assertRaises(ValueError): load_credentials(path, profile)

    def test_missing_placeholder_and_ambiguous_credentials_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / '.tmp') as tmp:
            path = Path(tmp) / 'credentials.txt'
            profile = {'base_url': 'https://provider.test/v1', 'model': 'locked', 'credential_env': 'TASK_TEST_KEY'}
            for text in ('# only comments\n', 'MODEL_API_KEY=your-key-here\n', 'MODEL_API_KEY=one\nMODEL_API_KEY=two\n'):
                path.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError): load_credentials(path, profile)

    def test_resuming_empty_eligibility_does_not_claim_experiment_completed(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root.parent / '.tmp') as tmp:
            config = json.loads((root / 'configs/demo.json').read_text(encoding='utf-8'))
            config.update(material_policy='strict_v10', material_gate=True, material_audit=True, audit_model='mock',
                          operations=['ethos'], interventions=[], subset={'item_ids': ['toy-001']},
                          diagnostics={'oo': {'enabled': False, 'repeats': 1}})
            config['budget']['stage_calls']['audit'] = 20
            config['dataset']['source'] = str(root / 'examples/toy_dataset.jsonl')
            scenario = json.loads((root / 'examples/mock_scenario.json').read_text(encoding='utf-8'))
            scenario['overrides'] = [{'match': {'stage': 'generate'},
                                      'text': json.dumps({'label': 'A', 'comment': 'New fact 987654321.'})}]
            scenario_path = Path(tmp) / 'scenario.json'; scenario_path.write_text(json.dumps(scenario), encoding='utf-8')
            config['mock_scenario'] = str(scenario_path)
            path = Path(tmp) / 'config.json'; path.write_text(json.dumps(config), encoding='utf-8')
            self.assertEqual(run_main(path, 'empty', tmp)['phase'], 'no_eligible_materials')
            self.assertEqual(run_main(path, 'empty', tmp)['phase'], 'no_eligible_materials')

