import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from test_audit import row


class CLITests(unittest.TestCase):
    def run_cli(self, *args, input=None):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-m', 'relay_audit', *args], input=input, text=True, capture_output=True, env=env)

    def with_file(self, content, *args):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / 'input.jsonl'
            path.write_text(content, encoding='utf-8')
            return self.run_cli('audit', str(path), *args)

    def test_exit_codes(self):
        for tokens, code in ((100, 0), (110, 1), (125, 2)):
            result = self.with_file(json.dumps(row(tokens)), '--json')
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(json.loads(result.stdout)['exit_code'], code)

    def test_malformed_lines(self):
        result = self.with_file(json.dumps(row()) + '\nnot json\n\n' + json.dumps(row()), '--json')
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report['parse_error_count'], 2)
        self.assertEqual([e['line'] for e in report['parse_errors']], [2, 3])
        self.assertEqual(len(report['transactions']), 2)

    def test_invalid_schema_line_number(self):
        report = json.loads(self.with_file(json.dumps(row()) + '\n{}', '--json').stdout)
        self.assertEqual(report['parse_errors'][0]['line'], 2)

    def test_empty_file(self):
        result = self.with_file('', '--json')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['totals']['billed']['total_tokens'], 0)

    def test_sample_end_to_end(self):
        result = self.run_cli('audit', str(ROOT / 'examples/sample.jsonl'), '--json')
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual([t['verdict'] for t in report['transactions']], ['PASS', 'FAIL', 'FAIL', 'FAIL'])
        self.assertEqual(report['transactions'][1]['ratio'], 2.85)
        self.assertEqual(report['parse_error_count'], 0)

    def test_check_stdin(self):
        result = self.run_cli('check', '-', '--json', input=json.dumps(row()))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['verdict'], 'PASS')

    def test_check_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / 'single.json'
            path.write_text(json.dumps(row(110)))
            result = self.run_cli('check', str(path), '--json')
        self.assertEqual(result.returncode, 1)

    def test_check_malformed(self):
        result = self.run_cli('check', '-', '--json', input='bad')
        self.assertEqual(json.loads(result.stdout)['parse_error_count'], 1)
        self.assertEqual(result.returncode, 2)

    def test_allowlist(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / 'allow.json'
            path.write_text(json.dumps({'synthetic-model': ['synthetic-alias']}))
            item = dict(row(), returned_model='synthetic-alias')
            result = self.with_file(json.dumps(item), '--json', '--allow-model-map', str(path))
            self.assertEqual(result.returncode, 0)

    def test_invalid_allowlist(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / 'allow.json'
            path.write_text('{"model":"alias"}')
            result = self.with_file(json.dumps(row()), '--json', '--allow-model-map', str(path))
            self.assertEqual(result.returncode, 2)

    def test_missing_file(self):
        result = self.run_cli('audit', str(ROOT / 'nonexistent.jsonl'), '--json')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['parse_error_count'], 1)

    def test_human_output(self):
        result = self.with_file(json.dumps(row()))
        self.assertIn('PASS', result.stdout)
        self.assertIn('exact', result.stdout)
        self.assertIn('Totals:', result.stdout)

    def test_version_help(self):
        self.assertEqual(self.run_cli('--version').stdout.strip(), '0.1.0')
        self.assertEqual(self.run_cli('--help').returncode, 0)

    def test_custom_thresholds(self):
        result = self.with_file(json.dumps(row(130)), '--fail-ratio', '1.4', '--warn-ratio', '1.3')
        self.assertEqual(result.returncode, 1)

    def test_invalid_thresholds(self):
        for arguments in (('--fail-ratio', 'nan'), ('--warn-ratio', '-1'), ('--warn-ratio', '2'), ('--fail-ratio', '1.1')):
            self.assertEqual(self.with_file('', *arguments).returncode, 2)

    def test_nonfinite_json_rejected(self):
        result = self.with_file('{"duration_ms":NaN}', '--json')
        self.assertEqual(json.loads(result.stdout)['parse_error_count'], 1)

    def test_unbounded_ratio_json(self):
        item = row()
        item['ground_truth']['prompt_tokens'] = 0
        report = json.loads(self.with_file(json.dumps(item), '--json').stdout)
        self.assertIsNone(report['transactions'][0]['ratio'])
