"""Minimal client for calling the Gemini API (standard library only).

Required parameter
-------------------
GEMINI_API_KEY
    A Google AI Studio API key with access to the Gemini models. Get one at
    https://aistudio.google.com/apikey. Provide it either as:
      - the environment variable ``GEMINI_API_KEY``, or
      - the ``api_key`` argument to :func:`call_gemini` / :func:`ask_gemini`.

No other credentials or project IDs are needed for the public Generative
Language API (this is not Vertex AI).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = 'gemini-3.5-flash'
API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'
REQUEST_TIMEOUT_SECONDS = 30


class GeminiError(RuntimeError):
    """Raised when the Gemini API cannot be reached or returns an error."""


def call_gemini(prompt: str, *, api_key: str | None = None, model: str = DEFAULT_MODEL,
                 system_instruction: str | None = None, temperature: float = 0.2,
                 timeout: float = REQUEST_TIMEOUT_SECONDS) -> str:
    """Send ``prompt`` to Gemini and return the model's text response.

    Parameters
    ----------
    prompt:
        The user's natural-language question or instruction.
    api_key:
        Gemini API key. Falls back to the ``GEMINI_API_KEY`` environment
        variable when not supplied.
    model:
        Gemini model name, e.g. ``gemini-2.5-flash`` (default) or
        ``gemini-2.5-pro``.
    system_instruction:
        Optional system prompt providing grounding context (e.g. the current
        schedule/report data) so answers stay specific to this instance.
    temperature:
        Sampling temperature; kept low by default for consistent, factual
        answers about a concrete schedule.
    timeout:
        Socket timeout in seconds for the HTTP request.
    """
    payload = _generate_content(
        prompt, api_key=api_key, model=model, system_instruction=system_instruction,
        temperature=temperature, timeout=timeout,
    )
    try:
        parts = payload['candidates'][0]['content']['parts']
        return ''.join(part.get('text', '') for part in parts).strip()
    except (KeyError, IndexError) as exc:
        raise GeminiError(f'Unexpected Gemini API response shape: {payload}') from exc


def call_gemini_with_tools(prompt: str, tools: list[dict], *, api_key: str | None = None,
                            model: str = DEFAULT_MODEL, system_instruction: str | None = None,
                            temperature: float = 0.2, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
    """Send ``prompt`` to Gemini with function-calling ``tools`` declared.

    ``tools`` should be a list of function declarations in the shape produced
    by :data:`sincro.gemini_tools.TOOL_DECLARATIONS` (``name``,
    ``description``, ``parameters`` as a JSON schema object).

    Returns a dict with either:
      - ``{'text': str}`` when Gemini answered directly, or
      - ``{'function_call': {'name': str, 'args': dict}}`` when Gemini wants
        a tool invoked. Route the latter through
        :func:`sincro.gemini_tools.dispatch_tool_call`.
    """
    payload = _generate_content(
        prompt, api_key=api_key, model=model, system_instruction=system_instruction,
        temperature=temperature, timeout=timeout, tools=tools,
    )
    try:
        parts = payload['candidates'][0]['content']['parts']
    except (KeyError, IndexError) as exc:
        raise GeminiError(f'Unexpected Gemini API response shape: {payload}') from exc

    for part in parts:
        call = part.get('functionCall')
        if call:
            return {'function_call': {'name': call['name'], 'args': call.get('args', {})}}
    return {'text': ''.join(part.get('text', '') for part in parts).strip()}


def _generate_content(prompt: str, *, api_key: str | None, model: str, system_instruction: str | None,
                       temperature: float, timeout: float, tools: list[dict] | None = None) -> dict:
    key = api_key or os.environ.get('GEMINI_API_KEY')
    if not key:
        raise GeminiError(
            'Missing Gemini API key. Set the GEMINI_API_KEY environment variable '
            'or pass api_key explicitly.'
        )
    if not prompt or not prompt.strip():
        raise GeminiError('prompt must be a non-empty string')

    url = f'{API_BASE}/{model}:generateContent?key={key}'
    body: dict = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': temperature},
    }
    if system_instruction:
        body['systemInstruction'] = {'parts': [{'text': system_instruction}]}
    if tools:
        body['tools'] = [{'functionDeclarations': tools}]

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise GeminiError(f'Gemini API returned {exc.code}: {detail}') from exc
    except urllib.error.URLError as exc:
        raise GeminiError(f'Could not reach Gemini API: {exc.reason}') from exc
