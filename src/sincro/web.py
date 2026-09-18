"""Local upload, scheduling, validation and download UI (standard library only)."""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import tempfile
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .emit import emit
from .instance import load_instance

DATA = Path(__file__).resolve().parents[2] / '01_data'

# An upload must answer promptly. Give CP-SAT this long per scenario to prove
# the optimum; past it, emit() falls back to the heuristic rather than hang.
EXACT_SECONDS_PER_SCENARIO = 90.0
INPUT_FILES = (
    '01_LINES.csv', '02_STATIONS.csv', '03_SECTORS.csv', '04_LOCATION_SUPPLY.csv',
    '05_BUFFER_LOCATION.csv', '06_PARAMETERS.csv', '07_PROJECT_DETAILS.csv', '08_ACTIVITY_DETAILS.csv',
)
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def run_request(payload: dict) -> dict:
    scenario = payload.get('scenario', 'all')
    if scenario not in ('A', 'B', 'C', 'all'):
        raise ValueError('Choose scenario A, B, C or all')
    files = payload.get('files')
    if files is not None and (not isinstance(files, dict) or set(files) != set(INPUT_FILES)
                              or any(not isinstance(value, str) for value in files.values())):
        raise ValueError('Upload exactly the eight named instance CSV files')
    with tempfile.TemporaryDirectory(prefix='sincro-web-') as tmp:
        root = Path(tmp)
        data = root / 'instance'
        data.mkdir()
        for name in INPUT_FILES:
            (data / name).write_text(files[name] if files is not None else (DATA / name).read_text(), encoding='utf-8')
        inst = load_instance(data)
        results = []
        for choice in ('A', 'B', 'C') if scenario == 'all' else (scenario,):
            output = root / choice
            try:
                report = emit(str(data), str(output), choice, optimal=True,
                              primary_seconds=EXACT_SECONDS_PER_SCENARIO)
            except ValueError as exc:
                results.append({'scenario': choice, 'error': str(exc)})
                continue
            with (output / 'SCHEDULE_ACCESS.csv').open(newline='') as f:
                access = list(csv.DictReader(f))
            activities = []
            for aid, a in sorted(inst.activities.items()):
                c = inst.contracts[a.contract_number]
                nights = [{'week': int(r['week']), 'eclo': int(r['eclo'])} for r in access if r['activity_id'] == aid]
                finish = max(r['week'] for r in nights)
                activities.append({'id': aid, 'contract': a.contract_number, 'priority': c.contract_priority,
                    'type': c.access_type, 'nature': c.nature_of_activity, 'locations': inst.span_locations(a),
                    'predecessor': a.predecessor_activity_id, 'nights': nights, 'workload': a.total_accesses,
                    'overrun_days': max(0, (inst.week_end(finish) - c.planned_completion_date).days)})
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name in ('SCHEDULE_ACCESS.csv', 'SCHEDULE_OCCUPANCY.csv', 'RESULTS.csv'):
                    archive.write(output / name, name)
            results.append({'scenario': choice, 'report': report, 'activities': activities,
                            'download': base64.b64encode(buffer.getvalue()).decode('ascii')})
        return {'results': results, 'horizon_start': inst.horizon_start.isoformat(), 'horizon_weeks': inst.horizon_weeks,
                'activity_count': len(inst.activities), 'contract_count': len(inst.contracts)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/':
            self.send_error(404)
            return
        body = Path(__file__).with_name('web.html').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/solve':
            self.send_error(404)
            return
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    try:
        with HTTPServer((args.host, args.port), Handler) as server:
            print(f'Sincro is available at http://{args.host}:{args.port}', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
