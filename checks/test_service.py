"""Deployment regressions: run with PYTHONPATH=src python -m unittest discover -s checks."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sincro import web
from sincro.optimal import _search_workers


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.server = web.ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.url + path, data=data,
                                         headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), response.headers

    def test_health_and_busy_reply_while_another_request_is_solving(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(_payload):
            entered.set()
            release.wait(5)
            return {'results': []}, 200

        with patch.object(web, 'run_isolated', side_effect=blocked), ThreadPoolExecutor(1) as pool:
            first = pool.submit(self.request, '/solve', {'scenario': 'A'})
            try:
                self.assertTrue(entered.wait(3))
                status, body, _ = self.request('/healthz')
                self.assertEqual(status, 200)
                self.assertEqual(body['version'], web.SERVICE_VERSION)
                status, body, headers = self.request('/solve', {'scenario': 'B'})
                self.assertEqual(status, 503)
                self.assertIn('already optimising', body['error'])
                self.assertEqual(headers['Retry-After'], '10')
            finally:
                release.set()
            self.assertEqual(first.result()[0], 200)

    def test_unexpected_failure_returns_json_and_releases_capacity(self):
        with patch.object(web, 'run_isolated', side_effect=RuntimeError('test crash')):
            status, body, _ = self.request('/solve', {'scenario': 'A'})
            self.assertEqual(status, 500)
            self.assertIn('service failed', body['error'])
        with patch.object(web, 'run_isolated', return_value=({'results': []}, 200)):
            self.assertEqual(self.request('/solve', {'scenario': 'A'})[0], 200)

    def test_invalid_payload_and_missing_route_return_json(self):
        self.assertEqual(self.request('/solve', [1, 2])[0], 400)
        self.assertEqual(self.request('/missing')[0], 404)

    def test_fresh_process_reports_invalid_scenario(self):
        response, status = web.run_isolated({'scenario': 'invalid'})
        self.assertEqual(status, 400)
        self.assertIn('scenario', response['error'].lower())


class WorkerConfigTests(unittest.TestCase):
    def test_cloud_defaults_to_one_worker(self):
        with patch.dict(os.environ, {'K_SERVICE': 'sincro'}, clear=True), \
                patch('os.cpu_count', return_value=128):
            self.assertEqual(_search_workers(), 1)

    def test_explicit_limit_and_invalid_configuration(self):
        with patch.dict(os.environ, {'SINCRO_SOLVER_WORKERS': '2'}):
            self.assertEqual(_search_workers(), 2)
        for invalid in ('0', '65', 'invalid'):
            with patch.dict(os.environ, {'SINCRO_SOLVER_WORKERS': invalid}):
                with self.assertRaises(ValueError):
                    _search_workers()


if __name__ == '__main__':
    unittest.main()
