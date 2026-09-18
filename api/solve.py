"""Vercel serverless entry point for POST /solve (rewritten to /api/solve)."""
from __future__ import annotations

import csv
import json
from http.server import BaseHTTPRequestHandler

from sincro.web import MAX_UPLOAD_BYTES, run_request


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_UPLOAD_BYTES:
                raise ValueError('Upload must be between 1 byte and 8 MB')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected an object containing scenario and files')
            response, status = run_request(payload), 200
        except (ValueError, KeyError, OSError, csv.Error) as exc:
            response, status = {'error': str(exc)}, 400
        body = json.dumps(response).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
