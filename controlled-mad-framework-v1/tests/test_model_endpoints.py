"""Endpoint profiles for local inference and hosted providers, without network calls."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest
from unittest.mock import patch

from controlled_mad.config import validate_config, profile
from controlled_mad.backends.openai_compatible import OpenAIBackend
from controlled_mad.domain import Task

ROOT = Path(__file__).resolve().parents[1]


class ModelEndpoints(unittest.TestCase):
    def config(self, url):
        config = json.loads((ROOT / 'configs/demo.json').read_text(encoding='utf-8'))
        config['profiles']['mock'] = {
            'provider': 'openai', 'model': 'local-model',
            'base_url': url, 'credential_env': 'LOCAL_MODEL_API_KEY',
        }
        return config

    def test_loopback_http_supports_local_inference(self):
        for host in ('localhost', '127.0.0.1', '127.0.0.2', '[::1]'):
            with self.subTest(host=host):
                config = self.config(f'http://{host}:8000/v1')
                self.assertEqual(validate_config(config), config)

    def test_remote_requires_https_and_url_never_embeds_credentials(self):
        for url in (
            'http://example.test/v1', 'http://192.168.1.2:8000/v1',
            'http://127.0.0.1.example.test/v1', 'http://localhost.example.test/v1',
            'http://[::ffff:192.168.1.2]/v1',
            'http://user:secret@127.0.0.1:8000/v1',
            'http://127.0.0.1:8000/v1?api_key=secret',
            'http://127.0.0.1:8000/v1#secret',
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_config(self.config(url))
        config = self.config('https://provider.test/v1')
        self.assertEqual(validate_config(config), config)

    def test_local_http_transport_and_response_contract(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length']))), self.headers.get('Authorization')))
                data = json.dumps({
                    'model': 'local-model',
                    'choices': [{'message': {'content': '{"label":"Supported","comment":"evidence"}'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 4, 'completion_tokens': 6},
                }).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = validate_config(self.config(f'http://127.0.0.1:{server.server_port}/v1'))
                task = Task('t', 'S0', 'mock', ({'role': 'user', 'content': 'judge'},), (), {}, 'h')
                with patch.dict('os.environ', {'LOCAL_MODEL_API_KEY': 'local-test-token'}):
                    result = OpenAIBackend().generate(task, profile(config, 'mock'))
                self.assertIsNone(result.error)
                self.assertEqual(result.returned_model, 'local-model')
                self.assertEqual(json.loads(result.text)['label'], 'Supported')
            finally:
                server.shutdown()
                thread.join(timeout=5)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], '/v1/chat/completions')
        self.assertEqual(requests[0][1]['model'], 'local-model')
        self.assertEqual(requests[0][2], 'Bearer local-test-token')


if __name__ == '__main__':
    unittest.main()
