"""Server-side assistant configuration and request boundary checks."""
import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from sincro.gemini_client import GeminiError
from sincro.web import Handler, assistant_status, load_environment


class AssistantConfigurationTests(unittest.TestCase):
    def test_dotenv_loads_supported_settings_without_overriding_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.env').write_text(
                '# Local settings\nexport GEMINI_API_KEY="file-key"\n'
                "GEMINI_MODEL='test-model'\nUNRELATED_SETTING=ignored\n")
            with patch('sincro.web.DATA', root / '01_data'), patch.dict(
                    os.environ, {'GEMINI_API_KEY': 'process-key'}, clear=True):
                load_environment()
                self.assertEqual(os.environ['GEMINI_API_KEY'], 'process-key')
                self.assertEqual(os.environ['GEMINI_MODEL'], 'test-model')
                self.assertNotIn('UNRELATED_SETTING', os.environ)
                status = assistant_status()
                self.assertEqual(status, {'configured': True, 'model': 'test-model'})
                self.assertNotIn('process-key', json.dumps(status))

    def test_missing_or_blank_configuration_keeps_scheduler_available(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True), \
                patch('sincro.web.DATA', Path(temporary) / '01_data'):
            load_environment()
            self.assertFalse(assistant_status()['configured'])
            os.environ['GEMINI_API_KEY'] = '  '
            self.assertFalse(assistant_status()['configured'])


class AssistantHttpBoundaryTests(unittest.TestCase):
    def handler(self, body=b'{}', path='/ask'):
        # Exercise the real HTTP handlers without opening a listening socket.
        handler = object.__new__(Handler)
        handler.path = path
        handler.headers = Message()
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: setattr(handler, 'status', status)
        handler.response_headers = {}
        handler.send_header = lambda key, value: handler.response_headers.update({key: value})
        handler.end_headers = lambda: None
        return handler

    def test_status_endpoint_never_returns_credentials(self):
        handler = self.handler(path='/assistant-status')
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'secret-example'}):
            handler.do_GET()
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response_headers['Cache-Control'], 'no-store')
        self.assertTrue(json.loads(handler.wfile.getvalue())['configured'])
        self.assertNotIn(b'secret-example', handler.wfile.getvalue())

    def test_provider_failure_is_json_503_and_retryable(self):
        handler = self.handler(b'{"prompt":"Explain scenario A"}')
        with patch('sincro.web.run_ask_request', side_effect=GeminiError('Gemini timed out. Please try again.')):
            handler.do_POST()
        self.assertEqual(handler.status, 503)
        self.assertIn('timed out', json.loads(handler.wfile.getvalue())['error'])

    def test_bad_payload_is_rejected_before_provider_call(self):
        for body in (b'[]', b'null', b'{', b''):
            with self.subTest(body=body), patch('sincro.web.run_ask_request') as ask:
                handler = self.handler(body)
                handler.do_POST()
                self.assertEqual(handler.status, 400)
                self.assertIn('error', json.loads(handler.wfile.getvalue()))
                ask.assert_not_called()

    def test_ask_route_passes_the_workspace_and_returns_full_response(self):
        payload = {'prompt': 'Explain the plan', 'plans': {}, 'history': [], 'scenario': 'A'}
        response = {'answer': 'Generate a plan first.', 'applied': False, 'links': []}
        handler = self.handler(json.dumps(payload).encode())
        with patch('sincro.web.run_ask_request', return_value=response) as ask:
            handler.do_POST()
        ask.assert_called_once_with(payload)
        self.assertEqual(handler.status, 200)
        self.assertEqual(json.loads(handler.wfile.getvalue()), response)


if __name__ == '__main__':
    unittest.main()
