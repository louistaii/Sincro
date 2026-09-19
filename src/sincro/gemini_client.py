"""Bounded, standard-library client for the public Gemini GenerateContent API.

Set ``GEMINI_API_KEY`` on the server, or pass ``api_key`` explicitly. Set
``GEMINI_MODEL`` to override the default model without changing application code.
Requests are never automatically retried: a caller may be planning a change.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import urllib.error
import urllib.request

DEFAULT_MODEL = 'gemini-2.5-flash'
API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'
REQUEST_TIMEOUT_SECONDS = 30
MAX_OUTPUT_TOKENS = 8192
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_HISTORY_MESSAGES = 40


class GeminiError(RuntimeError):
    """A safe, user-facing description of a Gemini request failure."""


def call_gemini(prompt: str, *, api_key: str | None = None, model: str | None = None,
                system_instruction: str | None = None, temperature: float = 0.2,
                timeout: float = REQUEST_TIMEOUT_SECONDS,
                history: list[dict] | None = None) -> str:
    """Return text grounded in optional system instructions and conversation history.

    ``history`` contains earlier messages as ``{role: 'user' | 'assistant',
    content: str}``; ``prompt`` is appended as the current user message.
    ``model`` overrides ``GEMINI_MODEL`` and then :data:`DEFAULT_MODEL`.
    """
    payload = _generate_content(
        prompt, api_key=api_key, model=model, system_instruction=system_instruction,
        temperature=temperature, timeout=timeout, history=history,
    )
    result = _parse_response(payload)
    if result.get('function_calls'):
        raise GeminiError('Gemini requested an unexpected tool. Please rephrase your question.')
    return result['text']


def call_gemini_with_tools(prompt: str, tools: list[dict], *, api_key: str | None = None,
                           model: str | None = None, system_instruction: str | None = None,
                           temperature: float = 0.2, timeout: float = REQUEST_TIMEOUT_SECONDS,
                           history: list[dict] | None = None) -> dict:
    """Return answer text and/or all requested function calls in their original order.

    ``tools`` contains Gemini function declarations (name, description, parameters).
    A direct answer returns ``{'text': str}``. Tool requests return
    ``{'function_calls': [{'name': str, 'args': dict}, ...]}``, with optional
    ``text``. Exactly one call also includes ``function_call`` for existing callers.
    No tools are executed by this transport.
    """
    payload = _generate_content(
        prompt, api_key=api_key, model=model, system_instruction=system_instruction,
        temperature=temperature, timeout=timeout, tools=tools, history=history,
    )
    return _parse_response(payload)


def _parse_response(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise GeminiError('Gemini returned an invalid response. Please try again.')
    if 'error' in payload:
        raise GeminiError('Gemini rejected the request. Check the server API key and model configuration.')
    feedback = payload.get('promptFeedback', {})
    if not isinstance(feedback, dict):
        raise GeminiError('Gemini returned an invalid response. Please try again.')
    if feedback.get('blockReason'):
        raise GeminiError('Gemini blocked this request. Please rephrase your scheduling question.')
    candidates = payload.get('candidates')
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise GeminiError('Gemini returned no answer. Please try again.')
    candidate = candidates[0]
    finish_reason = candidate.get('finishReason')
    if finish_reason == 'MAX_TOKENS':
        raise GeminiError('Gemini reached its response limit. Please ask a more focused question.')
    if finish_reason not in (None, 'STOP'):
        raise GeminiError('Gemini could not complete this response. Please rephrase your scheduling question.')
    content = candidate.get('content')
    if not isinstance(content, dict):
        raise GeminiError('Gemini returned an invalid response. Please try again.')
    parts = content.get('parts')
    if not isinstance(parts, list):
        raise GeminiError('Gemini returned an invalid response. Please try again.')
    texts, calls = [], []
    for part in parts:
        if not isinstance(part, dict):
            raise GeminiError('Gemini returned an invalid response. Please try again.')
        # Thinking models can include internal thought parts alongside the answer.
        if part.get('thought') is True:
            continue
        if 'text' in part:
            if not isinstance(part['text'], str):
                raise GeminiError('Gemini returned invalid answer text. Please try again.')
            texts.append(part['text'])
        if 'functionCall' in part:
            call = part['functionCall']
            if (not isinstance(call, dict) or not isinstance(call.get('name'), str)
                    or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', call['name'])
                    or not isinstance(call.get('args', {}), dict)):
                raise GeminiError('Gemini returned an invalid tool request. Please rephrase your request.')
            calls.append({'name': call['name'], 'args': call.get('args', {})})
    text = ''.join(texts).strip()
    if not text and not calls:
        raise GeminiError('Gemini returned no answer. Please try again.')
    result = {'text': text} if text else {}
    if calls:
        result['function_calls'] = calls
        if len(calls) == 1:
            result['function_call'] = calls[0]
    return result


def _generate_content(prompt: str, *, api_key: str | None, model: str | None,
                      system_instruction: str | None, temperature: float, timeout: float,
                      tools: list[dict] | None = None, history: list[dict] | None = None) -> dict:
    key = api_key or os.environ.get('GEMINI_API_KEY')
    if not isinstance(key, str) or not key.strip():
        raise GeminiError('Missing Gemini API key. Set GEMINI_API_KEY on the server.')
    key = key.strip()
    if any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise GeminiError('Invalid Gemini API key. Check GEMINI_API_KEY on the server.')
    model = model if model is not None else os.environ.get('GEMINI_MODEL', DEFAULT_MODEL)
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', model):
        raise GeminiError('Invalid Gemini model name. Check GEMINI_MODEL on the server.')
    if not isinstance(prompt, str) or not prompt.strip():
        raise GeminiError('prompt must be a non-empty string')
    if system_instruction is not None and not isinstance(system_instruction, str):
        raise GeminiError('system_instruction must be a string')
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 120):
        raise GeminiError('Gemini timeout must be between 0 and 120 seconds')
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or not 0 <= temperature <= 2):
        raise GeminiError('Gemini temperature must be between 0 and 2')
    contents = []
    if history is not None:
        if not isinstance(history, list) or len(history) > MAX_HISTORY_MESSAGES:
            raise GeminiError(f'history must contain at most {MAX_HISTORY_MESSAGES} messages')
        for message in history:
            if (not isinstance(message, dict) or message.get('role') not in ('user', 'assistant')
                    or not isinstance(message.get('content'), str) or not message['content'].strip()):
                raise GeminiError('history messages require a user or assistant role and non-empty content')
            contents.append({'role': 'model' if message['role'] == 'assistant' else 'user',
                             'parts': [{'text': message['content']}]})
    contents.append({'role': 'user', 'parts': [{'text': prompt}]})
    body: dict = {
        'contents': contents,
        'generationConfig': {'temperature': temperature, 'maxOutputTokens': MAX_OUTPUT_TOKENS},
    }
    if system_instruction:
        body['systemInstruction'] = {'parts': [{'text': system_instruction}]}
    if tools is not None:
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise GeminiError('tools must be a list of function declarations')
        if tools:
            body['tools'] = [{'functionDeclarations': tools}]
    try:
        encoded = json.dumps(body, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        raise GeminiError('Gemini request contains invalid data.') from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise GeminiError('The scheduling context is too large. Please use a smaller dataset or conversation.')
    request = urllib.request.Request(
        f'{API_BASE}/{model}:generateContent', data=encoded,
        headers={'Content-Type': 'application/json', 'x-goog-api-key': key}, method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GeminiError('Gemini returned too much data. Please ask a more focused question.')
        return json.loads(raw.decode('utf-8'), parse_constant=_reject_json_constant,
                          parse_float=_parse_json_float)
    except urllib.error.HTTPError as exc:
        # Neither upstream bodies nor exception reasons are safe to expose: they
        # may contain credentials, uploaded data, or untrusted response text.
        exc.close()
        if exc.code in (401, 403):
            message = 'Gemini authentication failed. Check GEMINI_API_KEY and model access on the server.'
        elif exc.code == 429:
            message = 'Gemini is rate limited or its quota is exhausted. Check API quota and try again later.'
        elif exc.code == 404:
            message = 'Gemini model is unavailable. Check GEMINI_MODEL on the server.'
        elif exc.code >= 500:
            message = 'Gemini is temporarily unavailable. Please try again later.'
        else:
            message = 'Gemini rejected the request. Check the model configuration and request data.'
        raise GeminiError(message) from None
    except TimeoutError:
        raise GeminiError('Gemini timed out. Please try again.') from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise GeminiError('Gemini timed out. Please try again.') from None
        raise GeminiError('Could not reach Gemini. Check the server network connection and try again.') from None
    except (OSError, http.client.HTTPException):
        raise GeminiError('The connection to Gemini failed. Please try again.') from None
    except (UnicodeError, ValueError, RecursionError):
        raise GeminiError('Gemini returned invalid JSON. Please try again.') from None


def _reject_json_constant(value: str) -> None:
    raise ValueError('Non-finite JSON numbers are not allowed')


def _parse_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON numbers are not allowed')
    return number
