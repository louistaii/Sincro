import base64
import csv
import io
import unittest
import zipfile

from sincro.web import DATA, INPUT_FILES, run_request
from sincro.validate import SCHEMAS


class UploadTests(unittest.TestCase):
    def test_upload_uses_changed_demand_and_downloads_only_three_csvs(self):
        files = {name: (DATA/name).read_text() for name in INPUT_FILES}
        for name in ('07_PROJECT_DETAILS.csv', '08_ACTIVITY_DETAILS.csv'):
            reader = csv.DictReader(io.StringIO(files[name]))
            row = next(reader)
            if name == '08_ACTIVITY_DETAILS.csv':
                row['total_accesses'] = '4'
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerow(row)
            files[name] = output.getvalue()
        response = run_request({'files': files, 'scenario': 'all'})
        self.assertEqual(response['activity_count'], 1)
        self.assertEqual(len(response['results']), 3)
        for result in response['results']:
            self.assertNotIn('error', result)
            self.assertTrue(result['report']['feasible'])
            self.assertEqual(result['activities'][0]['workload'], 4)
            self.assertEqual(response['engine'], 'heuristic')
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['download']))) as archive:
                self.assertEqual(set(archive.namelist()), set(SCHEMAS))
                access = list(csv.DictReader(io.StringIO(archive.read('SCHEDULE_ACCESS.csv').decode())))
                self.assertGreaterEqual(sum(1.5 if row['eclo'] == '1' else 1 for row in access), 4)
            calendar = base64.b64decode(result['calendar']).decode()
            summary = list(csv.DictReader(io.StringIO(base64.b64decode(result['summary']).decode())))
            self.assertTrue(calendar.startswith('BEGIN:VCALENDAR\r\n'))
            self.assertEqual(calendar.count('BEGIN:VEVENT'), len(access))
            self.assertEqual(len(summary), len(access))

    def test_reject_missing_files_and_unexpected_paths(self):
        for files in ({}, {'../08_ACTIVITY_DETAILS.csv': 'bad'}, []):
            with self.assertRaisesRegex(ValueError, 'eight named'):
                run_request({'files': files})
        with self.assertRaises(ValueError):
            run_request({'scenario': 'D'})
        with self.assertRaisesRegex(ValueError, 'optimal'):
            run_request({'optimal': 'yes'})

    def test_accepts_single_sheet_xlsx_uploads(self):
        files = {name: self._xlsx((DATA / name).read_text()) for name in INPUT_FILES}
        response = run_request({'files': files, 'scenario': 'A'})
        self.assertEqual(len(response['results']), 1)
        self.assertNotIn('error', response['results'][0])
        self.assertEqual(response['activity_count'], 54)

    @staticmethod
    def _xlsx(csv_text):
        rows = list(csv.reader(io.StringIO(csv_text)))
        sheet_rows = []
        for row_number, row in enumerate(rows, 1):
            cells = []
            for column, value in enumerate(row):
                number = column + 1
                letters = ''
                while number:
                    number, remainder = divmod(number - 1, 26)
                    letters = chr(65 + remainder) + letters
                escaped = (value.replace('&', '&amp;').replace('<', '&lt;')
                           .replace('>', '&gt;').replace('"', '&quot;'))
                cells.append(f'<c r="{letters}{row_number}" t="inlineStr"><is><t>{escaped}</t></is></c>')
            sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            archive.writestr('xl/workbook.xml',
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr('xl/_rels/workbook.xml.rels',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr('xl/worksheets/sheet1.xml',
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
        return {'format': 'xlsx', 'data': base64.b64encode(content.getvalue()).decode()}


if __name__ == '__main__':
    unittest.main()
