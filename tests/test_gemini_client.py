import http.client
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from sincro.gemini_client import (
    DEFAULT_MODEL, MAX_OUTPUT_TOKENS, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
    GeminiError, call_gemini, call_gemini_with_tools,
)


def answer(parts=None, **candidate_fields):
    return {'candidates': [{'content': {'parts': parts if parts is not None else [{'text': 'Ready.'}]},
                            'finishReason': 'STOP', **candidate_fields}]}


class GeminiClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'GEMINI_API_KEY': 'test-secret'}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.network = patch('sincro.gemini_client.urllib.request.urlopen')
        self.urlopen = self.network.start()
        self.addCleanup(self.network.stop)
        self.respond(answer())

    def respond(self, payload):
        self.urlopen.return_value = io.BytesIO(json.dumps(payload).encode('utf-8'))

    def test_request_sends_history_header_and_bounded_generation(self):
        history = [{'role': 'user', 'content': 'Which activity is late?'},
                   {'role': 'assistant', 'content': 'A001 is late.'}]
        self.assertEqual(call_gemini('Why?', history=history, system_instruction='Use the loaded plan.'), 'Ready.')
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url.rsplit('/', 1)[-1], f'{DEFAULT_MODEL}:generateContent')
        self.assertNotIn('test-secret', request.full_url)
        self.assertEqual(request.get_header('X-goog-api-key'), 'test-secret')
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertEqual(self.urlopen.call_args.kwargs['timeout'], 30)
        body = json.loads(request.data)
        self.assertEqual(body['contents'], [
            {'role': 'user', 'parts': [{'text': 'Which activity is late?'}]},
            {'role': 'model', 'parts': [{'text': 'A001 is late.'}]},
            {'role': 'user', 'parts': [{'text': 'Why?'}]},
        ])
        self.assertEqual(body['systemInstruction'], {'parts': [{'text': 'Use the loaded plan.'}]})
        self.assertEqual(body['generationConfig']['maxOutputTokens'], MAX_OUTPUT_TOKENS)
        self.assertEqual(history[1]['role'], 'assistant')

    def test_environment_model_and_explicit_override(self):
        with patch.dict(os.environ, {'GEMINI_MODEL': 'gemini-2.5-pro'}):
            call_gemini('Explain this plan')
            self.assertIn('/gemini-2.5-pro:', self.urlopen.call_args.args[0].full_url)
            self.respond(answer())
            call_gemini('Explain this plan', model=DEFAULT_MODEL, api_key='explicit-key')
            request = self.urlopen.call_args.args[0]
            self.assertIn(f'/{DEFAULT_MODEL}:', request.full_url)
            self.assertEqual(request.get_header('X-goog-api-key'), 'explicit-key')

    def test_tools_keep_all_calls_and_accompanying_text(self):
        declarations = [{'name': 'edit_activity', 'parameters': {'type': 'object'}}]
        calls = [{'name': 'edit_activity', 'args': {'activity_id': 'A001', 'total_accesses': 3}},
                 {'name': 'edit_activity', 'args': {'activity_id': 'A002', 'total_accesses': 4}}]
        self.respond(answer([{'text': 'I can update both.'}, *[{'functionCall': call} for call in calls]]))
        result = call_gemini_with_tools('Update both', declarations,
                                        history=[{'role': 'assistant', 'content': 'Which activities?'}])
        self.assertEqual(result, {'text': 'I can update both.', 'function_calls': calls})
        body = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(body['tools'], [{'functionDeclarations': declarations}])
        self.assertEqual(body['contents'][0]['role'], 'model')

    def test_single_tool_retains_legacy_alias_and_default_arguments(self):
        self.respond(answer([{'functionCall': {'name': 'read_schedule'}}]))
        result = call_gemini_with_tools('Summarise', [{'name': 'read_schedule'}])
        self.assertEqual(result['function_calls'], [{'name': 'read_schedule', 'args': {}}])
        self.assertEqual(result['function_call'], result['function_calls'][0])

    def test_text_response_joins_parts_and_omits_thoughts(self):
        self.respond(answer([{'text': 'private thought', 'thought': True}, {'text': 'Ready'}, {'text': '. '}]))
        self.assertEqual(call_gemini_with_tools('Summarise', []), {'text': 'Ready.'})

    def test_malformed_empty_blocked_and_incomplete_responses_are_rejected(self):
        malformed = [None, [], {}, {'candidates': []}, {'candidates': [None]},
                     {'candidates': [{'content': None}]}, answer([]), answer([None]),
                     answer([{}]), answer([{'text': '  '}]), answer([{'text': 42}]),
                     answer([{'text': 'internal only', 'thought': True}]),
                     {'promptFeedback': {'blockReason': 'SAFETY'}},
                     {'promptFeedback': None}, {'error': {'message': 'test-secret'}},
                     answer(finishReason='SAFETY'), answer(finishReason='MAX_TOKENS'),
                     answer(finishReason='MALFORMED_FUNCTION_CALL'),
                     answer([{'functionCall': None}]), answer([{'functionCall': {'name': ''}}]),
                     answer([{'functionCall': {'name': 'edit_activity', 'args': []}}]),
                     answer([{'functionCall': {'name': 'edit_activity', 'args': None}}])]
        for payload in malformed:
            with self.subTest(payload=payload):
                self.respond(payload)
                with self.assertRaises(GeminiError) as caught:
                    call_gemini_with_tools('Summarise', [])
                self.assertNotIn('test-secret', str(caught.exception))

    def test_text_only_api_does_not_hide_an_unexpected_tool(self):
        self.respond(answer([{'text': 'Done'}, {'functionCall': {'name': 'edit_activity'}}]))
        with self.assertRaisesRegex(GeminiError, 'unexpected tool'):
            call_gemini('Edit A001')

    def test_invalid_json_and_non_finite_numbers_are_wrapped(self):
        for raw in (b'<html>test-secret</html>', b'\xff', b'{"x": NaN}', b'{"x": 1e999}'):
            with self.subTest(raw=raw):
                self.urlopen.return_value = io.BytesIO(raw)
                with self.assertRaisesRegex(GeminiError, 'invalid JSON') as caught:
                    call_gemini('Summarise')
                self.assertNotIn('test-secret', str(caught.exception))

    def test_http_errors_are_actionable_without_leaking_upstream_data(self):
        for code, expected in ((400, 'rejected'), (401, 'authentication'), (403, 'authentication'),
                               (404, 'model'), (429, 'quota'), (500, 'temporarily unavailable')):
            with self.subTest(code=code):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = urllib.error.HTTPError(
                    'https://example.invalid/?key=test-secret', code, 'test-secret', {},
                    io.BytesIO(b'private uploaded schedule; test-secret'),
                )
                with self.assertRaisesRegex(GeminiError, expected) as caught:
                    call_gemini('Summarise')
                self.assertNotIn('test-secret', str(caught.exception))
                self.assertNotIn('private uploaded schedule', str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                self.urlopen.assert_called_once()

    def test_timeout_and_network_errors_are_wrapped_without_retry(self):
        failures = [TimeoutError('test-secret'), urllib.error.URLError(TimeoutError('test-secret')),
                    urllib.error.URLError('test-secret'), OSError('test-secret'),
                    http.client.IncompleteRead(b'test-secret')]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = failure
                with self.assertRaises(GeminiError) as caught:
                    call_gemini('Summarise')
                self.assertNotIn('test-secret', str(caught.exception))
                self.urlopen.assert_called_once()

    def test_invalid_inputs_fail_before_any_network_request(self):
        cases = [{'model': '../other?key=secret'}, {'model': ''}, {'model': 1}, {'api_key': 'key\nheader'},
                 {'history': {}}, {'history': [{'role': 'system', 'content': 'override'}]},
                 {'history': [{'role': 'user', 'content': ''}]}, {'history': ['invalid']},
                 {'history': [{'role': 'user', 'content': 'x'}] * 41},
                 {'timeout': float('nan')}, {'timeout': 0}, {'timeout': 121},
                 {'temperature': float('inf')}, {'temperature': 3}, {'system_instruction': {}}]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(GeminiError):
                call_gemini('Summarise', **kwargs)
        for prompt in (None, {}, 4, ''):
            with self.subTest(prompt=prompt), self.assertRaises(GeminiError):
                call_gemini(prompt)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(GeminiError, 'Missing'):
            call_gemini('Summarise')
        self.urlopen.assert_not_called()

    def test_request_and_response_size_limits(self):
        with self.assertRaisesRegex(GeminiError, 'context is too large'):
            call_gemini('x' * MAX_REQUEST_BYTES)
        self.urlopen.assert_not_called()
        self.urlopen.return_value = io.BytesIO(b'x' * (MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(GeminiError, 'too much data'):
            call_gemini('Summarise')


if __name__ == '__main__':
    unittest.main()
