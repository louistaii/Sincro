"""Local upload, scheduling, validation and download UI (standard library only)."""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import io
import json
import logging
import multiprocessing
import posixpath
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

from .change_control import apply_changes, build_schedule_constraints, parse_append_csv
from .emit import emit
from .instance import load_instance

DATA = Path(__file__).resolve().parents[2] / '01_data'

# Accuracy mode is deliberately uncapped: accepting a timer-limited incumbent
# can silently leave avoidable penalty on a difficult uploaded instance.
EXACT_SECONDS_PER_SCENARIO = None
INPUT_FILES = (
    '01_LINES.csv', '02_STATIONS.csv', '03_SECTORS.csv', '04_LOCATION_SUPPLY.csv',
    '05_BUFFER_LOCATION.csv', '06_PARAMETERS.csv', '07_PROJECT_DETAILS.csv', '08_ACTIVITY_DETAILS.csv',
)
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_WORKBOOK_UNCOMPRESSED = 32 * 1024 * 1024
SERVICE_VERSION = 'solve-isolation-v2-contract-scoring'
SOLVE_SLOT = threading.BoundedSemaphore(1)


def _solve_worker(payload, sender):
    """A fresh process releases native solver memory after each request."""
    try:
        sender.send((run_request(payload), 200))
    except (ValueError, KeyError, OSError, csv.Error) as exc:
        sender.send(({'error': str(exc)}, 400))
    except Exception:
        logging.exception('Scheduling worker failed')
        sender.send(({'error': 'Scheduling failed unexpectedly. Check the service logs.'}, 500))
    finally:
        sender.close()


def run_isolated(payload):
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_solve_worker, args=(payload, sender), daemon=True)
    started = False
    try:
        process.start()
        started = True
        sender.close()
        # Read before join: a large schedule can fill the IPC pipe.
        try:
            return receiver.recv()
        except EOFError:
            logging.error('Scheduling worker exited without a response')
            return {'error': ('The optimisation worker stopped unexpectedly. '
                              'Check Cloud Run memory limits and service logs.')}, 503
    finally:
        sender.close()
        receiver.close()
        if started:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()


def _excel_date(value: str) -> str:
    return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(value)))).isoformat()


def _xlsx_to_csv(encoded: str) -> str:
    """Convert the first worksheet of a small XLSX workbook to CSV."""
    try:
        raw = base64.b64decode(encoded, validate=True)
        workbook = zipfile.ZipFile(io.BytesIO(raw))
    except (ValueError, zipfile.BadZipFile) as exc:
        raise ValueError('Invalid Excel workbook') from exc
    with workbook:
        members = workbook.infolist()
        if sum(item.file_size for item in members) > MAX_WORKBOOK_UNCOMPRESSED:
            raise ValueError('Excel workbook expands beyond the 32 MB safety limit')
        try:
            book = ET.fromstring(workbook.read('xl/workbook.xml'))
            rels = ET.fromstring(workbook.read('xl/_rels/workbook.xml.rels'))
        except (KeyError, ET.ParseError) as exc:
            raise ValueError('Excel workbook is missing its worksheet metadata') from exc
        relationships = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels}
        sheet = next((item for item in book.iter() if item.tag.endswith('sheet')), None)
        if sheet is None:
            raise ValueError('Excel workbook has no worksheets')
        relationship_id = next((value for key, value in sheet.attrib.items()
                                if key.endswith('}id')), None)
        target = relationships.get(relationship_id or '')
        if not target:
            raise ValueError('Excel worksheet relationship is invalid')
        sheet_path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(f'xl/{target}')

        shared = []
        try:
            strings = ET.fromstring(workbook.read('xl/sharedStrings.xml'))
            for item in strings:
                shared.append(''.join(node.text or '' for node in item.iter()
                                      if node.tag.endswith('}t')))
        except KeyError:
            pass

        date_styles: set[int] = set()
        try:
            styles = ET.fromstring(workbook.read('xl/styles.xml'))
            custom_formats = {
                int(item.attrib['numFmtId']): item.attrib.get('formatCode', '')
                for item in styles.iter() if item.tag.endswith('numFmt')
            }
            cell_xfs = next((item for item in styles.iter() if item.tag.endswith('cellXfs')), None)
            if cell_xfs is not None:
                built_in_dates = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
                for index, xf in enumerate(cell_xfs):
                    number_format = int(xf.attrib.get('numFmtId', '0'))
                    code = custom_formats.get(number_format, '').lower()
                    if number_format in built_in_dates or any(token in code for token in ('yy', 'dd', 'mm')):
                        date_styles.add(index)
        except (KeyError, ET.ParseError, ValueError):
            pass

        try:
            root = ET.fromstring(workbook.read(sheet_path))
        except (KeyError, ET.ParseError) as exc:
            raise ValueError('Excel worksheet content is invalid') from exc
        rows: list[list[str]] = []
        for row in (item for item in root.iter() if item.tag.endswith('row')):
            values: dict[int, str] = {}
            for cell in (item for item in row if item.tag.endswith('c')):
                reference = cell.attrib.get('r', 'A1')
                letters = ''.join(ch for ch in reference if ch.isalpha()).upper()
                column = 0
                for letter in letters:
                    column = column * 26 + ord(letter) - 64
                cell_type = cell.attrib.get('t')
                value_node = next((item for item in cell if item.tag.endswith('v')), None)
                if cell_type == 'inlineStr':
                    value = ''.join(item.text or '' for item in cell.iter() if item.tag.endswith('}t'))
                else:
                    value = value_node.text if value_node is not None and value_node.text is not None else ''
                if cell_type == 's' and value:
                    value = shared[int(value)]
                elif value and int(cell.attrib.get('s', '0')) in date_styles:
                    value = _excel_date(value)
                elif cell_type not in ('str', 'inlineStr', 's') and value:
                    number = float(value)
                    value = str(int(number)) if number.is_integer() else str(number)
                values[max(0, column - 1)] = value
            width = max(values, default=-1) + 1
            rows.append([values.get(index, '') for index in range(width)])
        if not rows:
            raise ValueError('Excel worksheet is empty')
        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerows(rows)
        return output.getvalue()


def _input_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get('format') == 'xlsx' and isinstance(value.get('data'), str):
        return _xlsx_to_csv(value['data'])
    raise ValueError('Each input must be CSV text or an XLSX workbook')


def _ics_escape(value: object) -> str:
    return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def _calendar_exports(inst, scenario: str, access: list[dict]) -> tuple[str, str]:
    summary = io.StringIO()
    fields = ['date', 'week', 'activity_id', 'contract_number', 'access_seq', 'access_night',
              'access_type', 'nature', 'eclo', 'locations']
    writer = csv.DictWriter(summary, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Sincro//Rail Access Planner//EN',
             'CALSCALE:GREGORIAN', f'X-WR-CALNAME:Sincro Scenario {scenario}']
    for row in sorted(access, key=lambda item: (int(item['week']), item['activity_id'])):
        activity = inst.activities[row['activity_id']]
        contract = inst.contracts[activity.contract_number]
        date = inst.week_start(int(row['week']))
        locations = ' / '.join(inst.span_locations(activity))
        title = f"{activity.activity_id} - {activity.contract_number}"
        if row['eclo'] == '1':
            title += ' - ECLO'
        description = (f"Scenario {scenario}; week {row['week']}; access {row['access_seq']}; "
                       f"local night {row['access_night']}; {contract.access_type}; "
                       f"{contract.nature_of_activity}; {locations}")
        lines.extend([
            'BEGIN:VEVENT',
            f"UID:{scenario}-{activity.activity_id}-{row['access_seq']}@sincro",
            f"DTSTART;VALUE=DATE:{date:%Y%m%d}",
            f"DTEND;VALUE=DATE:{date + dt.timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_escape(title)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            f"CATEGORIES:Sincro,{_ics_escape(contract.access_type)},{'ECLO' if row['eclo'] == '1' else 'Standard'}",
            'END:VEVENT',
        ])
        writer.writerow({'date': date.isoformat(), 'week': row['week'], 'activity_id': activity.activity_id,
                         'contract_number': activity.contract_number, 'access_seq': row['access_seq'],
                         'access_night': row['access_night'], 'access_type': contract.access_type,
                         'nature': contract.nature_of_activity, 'eclo': row['eclo'],
                         'locations': ' | '.join(inst.span_locations(activity))})
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines) + '\r\n', summary.getvalue()


def run_request(payload: dict) -> dict:
    scenario = payload.get('scenario', 'all')
    if scenario not in ('A', 'B', 'C', 'all'):
        raise ValueError('Choose scenario A, B, C or all')
    files = payload.get('files')
    if files is not None and (not isinstance(files, dict) or set(files) != set(INPUT_FILES)
                              or any(not isinstance(value, (str, dict)) for value in files.values())):
        raise ValueError('Upload exactly the eight named instance CSV or XLSX files')
    optimal = payload.get('optimal', False)
    if not isinstance(optimal, bool):
        raise ValueError('optimal must be true or false')
    changes = payload.get('changes') or []
    schedule_changes = payload.get('schedule_changes')
    baseline = payload.get('baseline')
    change_metadata = None
    change_notices: list[str] = []
    if changes:
        if scenario == 'all':
            raise ValueError('Apply changes to one scenario at a time')
        if not isinstance(changes, list):
            raise ValueError('changes must be a list')
        expanded = []
        for change in changes:
            if isinstance(change, dict) and change.get('kind') == 'append_csv':
                expanded.append({'kind': 'append', 'activities': parse_append_csv(str(change.get('csv', '')))})
            else:
                expanded.append(change)
        changes = expanded
        if schedule_changes is None:
            schedule_changes = changes
        if not isinstance(schedule_changes, list):
            raise ValueError('schedule_changes must be a list')
        try:
            as_of_date = dt.date.fromisoformat(str(payload.get('as_of_date', dt.date.today().isoformat())))
        except ValueError as exc:
            raise ValueError('Change received date must be a valid date') from exc
        optimal = True
    with tempfile.TemporaryDirectory(prefix='sincro-web-') as tmp:
        root = Path(tmp)
        data = root / 'instance'
        data.mkdir()
        for name in INPUT_FILES:
            content = _input_text(files[name]) if files is not None else (DATA / name).read_text()
            (data / name).write_text(content, encoding='utf-8')
        if changes:
            change_notices = apply_changes(data, changes)
        inst = load_instance(data)
        schedule_constraints = None
        if changes:
            schedule_constraints, change_metadata = build_schedule_constraints(
                inst, baseline, schedule_changes, as_of_date)
        results = []
        for choice in ('A', 'B', 'C') if scenario == 'all' else (scenario,):
            output = root / choice
            try:
                report = emit(
                    str(data), str(output), choice, optimal=optimal,
                    primary_seconds=EXACT_SECONDS_PER_SCENARIO if optimal else None,
                    schedule_constraints=schedule_constraints,
                    require_proof=optimal,
                )
            except (ImportError, RuntimeError, ValueError) as exc:
                results.append({'scenario': choice, 'error': str(exc)})
                continue
            with (output / 'SCHEDULE_ACCESS.csv').open(newline='', encoding='utf-8-sig') as f:
                access = list(csv.DictReader(f))
            activities = []
            for aid, a in sorted(inst.activities.items()):
                c = inst.contracts[a.contract_number]
                nights = [{'week': int(r['week']), 'eclo': int(r['eclo']),
                           'access_seq': int(r['access_seq']), 'access_night': int(r['access_night']),
                           'date': inst.week_start(int(r['week'])).isoformat()}
                          for r in access if r['activity_id'] == aid]
                finish = max(r['week'] for r in nights)
                activities.append({'id': aid, 'contract': a.contract_number, 'priority': c.contract_priority,
                    'activity_priority': a.activity_priority, 'activity_type': a.activity_type,
                    'type': c.access_type, 'nature': c.nature_of_activity,
                    'start_location': a.start_location_id, 'end_location': a.end_location_id,
                    'start_date': a.planned_start_date.isoformat(), 'locations': inst.span_locations(a),
                    'predecessor': a.predecessor_activity_id, 'nights': nights, 'workload': a.total_accesses,
                    'overrun_days': max(0, (inst.week_end(finish) - c.planned_completion_date).days)})
            contracts = []
            for number, contract in sorted(inst.contracts.items()):
                members = [item for item in activities if item['contract'] == number]
                finish_week = max((night['week'] for item in members for night in item['nights']), default=None)
                completion = inst.week_end(finish_week).isoformat() if finish_week is not None else None
                contracts.append({'id': number, 'description': contract.description,
                                  'priority': contract.contract_priority, 'access_type': contract.access_type,
                                  'nature': contract.nature_of_activity,
                                  'due_date': contract.planned_completion_date.isoformat(),
                                  'completion_date': completion,
                                  'activities': len(members),
                                  'accesses': sum(len(item['nights']) for item in members),
                                  'overrun_days': max((item['overrun_days'] for item in members), default=0)})
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name in ('SCHEDULE_ACCESS.csv', 'SCHEDULE_OCCUPANCY.csv', 'RESULTS.csv'):
                    archive.write(output / name, name)
            calendar, summary = _calendar_exports(inst, choice, access)
            revision = None
            if change_metadata is not None:
                before = {(str(row['activity_id']), int(row['week']), int(row['eclo']))
                          for row in baseline}
                after = {(row['activity_id'], int(row['week']), int(row['eclo'])) for row in access}
                revision = {**change_metadata, 'notices': change_notices,
                            'changed_accesses': len(before.symmetric_difference(after))}
            results.append({'scenario': choice, 'report': report, 'activities': activities,
                            'contracts': contracts,
                            'revision': revision,
                            'download': base64.b64encode(buffer.getvalue()).decode('ascii'),
                            'calendar': base64.b64encode(calendar.encode()).decode('ascii'),
                            'summary': base64.b64encode(summary.encode()).decode('ascii')})
        return {'results': results, 'horizon_start': inst.horizon_start.isoformat(), 'horizon_weeks': inst.horizon_weeks,
                'activity_count': len(inst.activities), 'contract_count': len(inst.contracts),
                'locations': sorted(inst.locations),
                'engine': 'exact' if optimal else 'heuristic'}


class Handler(BaseHTTPRequestHandler):
    def _json(self, response, status=200):
        body = json.dumps(response).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if status == 503:
            self.send_header('Retry-After', '10')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logging.warning('Client disconnected before the response was delivered')

    def do_GET(self):
        if self.path == '/healthz':
            self._json({'status': 'ok', 'version': SERVICE_VERSION})
            return
        if self.path != '/':
            self._json({'error': 'Not found'}, 404)
            return
        body = Path(__file__).with_name('web.html').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/solve':
            self._json({'error': 'Not found'}, 404)
            return
        # Admit before reading uploads: concurrent 8 MB bodies must not crowd
        # the optimisation worker out of a small container's memory budget.
        if not SOLVE_SLOT.acquire(blocking=False):
            self._json({'error': 'This instance is already optimising a schedule. Please retry shortly.'}, 503)
            return
        started = time.monotonic()
        scenario_label = 'invalid'
        try:
            self.connection.settimeout(30)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_UPLOAD_BYTES:
                raise ValueError('Upload must be between 1 byte and 8 MB')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected an object containing scenario and files')
            scenario_label = str(payload.get('scenario', 'all'))[:16]
            response, status = run_isolated(payload)
        except (ValueError, KeyError, OSError, csv.Error) as exc:
            response, status = {'error': str(exc)}, 400
        except Exception:
            logging.exception('Scheduling request failed')
            response, status = {'error': 'Scheduling service failed. Check the service logs.'}, 500
        finally:
            SOLVE_SLOT.release()
            logging.warning('Solve completed: scenario=%s elapsed_seconds=%.2f',
                            scenario_label, time.monotonic() - started)
        self._json(response, status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    try:
        with ThreadingHTTPServer((args.host, args.port), Handler) as server:
            print(f'Sincro is available at http://{args.host}:{args.port}', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
