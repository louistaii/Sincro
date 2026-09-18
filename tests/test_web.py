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
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['download']))) as archive:
                self.assertEqual(set(archive.namelist()), set(SCHEMAS))
                access = list(csv.DictReader(io.StringIO(archive.read('SCHEDULE_ACCESS.csv').decode())))
                self.assertGreaterEqual(sum(1.5 if row['eclo'] == '1' else 1 for row in access), 4)

    def test_reject_missing_files_and_unexpected_paths(self):
        for files in ({}, {'../08_ACTIVITY_DETAILS.csv': 'bad'}, []):
            with self.assertRaisesRegex(ValueError, 'eight named'):
                run_request({'files': files})
        with self.assertRaises(ValueError):
            run_request({'scenario': 'D'})


if __name__ == '__main__':
    unittest.main()
