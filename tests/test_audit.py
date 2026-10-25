import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from relay_audit.audit import audit_transactions


def row(billed=100, exact=True):
    item = dict(requested_model='synthetic-model', prompt_text='a' * 400,
                usage=dict(prompt_tokens=billed, completion_tokens=0))
    if exact:
        item['ground_truth'] = dict(prompt_tokens=100, completion_tokens=0)
    return item


def kinds(report):
    return [f['type'] for tx in report['transactions'] for f in tx['findings']]


class AuditTests(unittest.TestCase):
    def test_exact_thresholds(self):
        for billed, code in ((109, 0), (110, 1), (124, 1), (125, 2), (285, 2)):
            with self.subTest(billed=billed):
                self.assertEqual(audit_transactions([row(billed)])['exit_code'], code)

    def test_approximate_thresholds(self):
        for billed, code in ((119, 0), (120, 1), (149, 1), (150, 2)):
            with self.subTest(billed=billed):
                report = audit_transactions([row(billed, False)])
                self.assertEqual(report['exit_code'], code)
                self.assertEqual(report['transactions'][0]['findings'][0]['evidence'], 'approximate')

    def test_overrides(self):
        self.assertEqual(audit_transactions([row(130)], fail_ratio=1.4, warn_ratio=1.3)['exit_code'], 1)

    def test_billed_default(self):
        tx = audit_transactions([row()])['transactions'][0]
        self.assertEqual(tx['billed'], tx['reported'])

    def test_billed_independent(self):
        item = row()
        item['billed'] = dict(prompt_tokens=285, completion_tokens=0)
        self.assertEqual(audit_transactions([item])['transactions'][0]['ratio'], 2.85)

    def test_reported_cache_impossible(self):
        item = row()
        item['usage']['cached_tokens'] = 101
        self.assertIn('cache_impossible', kinds(audit_transactions([item])))

    def test_billed_cache_impossible(self):
        item = row()
        item['billed'] = dict(prompt_tokens=100, completion_tokens=0, cached_tokens=101)
        self.assertEqual(audit_transactions([item])['exit_code'], 2)

    def test_cache_subset_not_double_counted(self):
        item = row()
        item['usage']['cached_tokens'] = 100
        report = audit_transactions([item])
        self.assertEqual(report['exit_code'], 0)
        self.assertEqual(report['totals']['billed']['total_tokens'], 100)

    def test_model_mismatch(self):
        item = row()
        item['returned_model'] = 'synthetic-alias'
        self.assertIn('model_substitution', kinds(audit_transactions([item])))

    def test_allowlist_directed(self):
        item = row()
        item['returned_model'] = 'synthetic-alias'
        self.assertEqual(audit_transactions([item], {'synthetic-model': ['synthetic-alias']})['exit_code'], 0)
        self.assertEqual(audit_transactions([item], {'synthetic-alias': ['synthetic-model']})['exit_code'], 2)

    def test_missing_model(self):
        self.assertNotIn('model_substitution', kinds(audit_transactions([row()])))

    def duration_rows(self, durations):
        rows = []
        for duration in durations:
            item = row()
            item['duration_ms'] = duration
            item['ground_truth']['completion_tokens'] = 100
            rows.append(item)
        return rows

    def test_duration_median(self):
        report = audit_transactions(self.duration_rows([10, 10, 10, 10, 100]))
        self.assertEqual(report['duration_median_ms_per_1k_output_tokens'], 100)
        self.assertEqual(kinds(report).count('duration_outlier'), 1)
        self.assertEqual(report['exit_code'], 1)

    def test_duration_minimum(self):
        self.assertNotIn('duration_outlier', kinds(audit_transactions(self.duration_rows([1, 1, 1, 100]))))

    def test_duration_strict_threshold(self):
        self.assertNotIn('duration_outlier', kinds(audit_transactions(self.duration_rows([10, 10, 10, 10, 30]))))

    def test_zero_output_duration_excluded(self):
        items = [dict(row(), duration_ms=100) for _ in range(5)]
        self.assertIsNone(audit_transactions(items)['duration_median_ms_per_1k_output_tokens'])

    def test_aggregate_classes(self):
        report = audit_transactions([row(125), row(150, False), row(90)])
        self.assertEqual(report['totals']['reported']['total_tokens'], 365)
        self.assertEqual(report['totals']['billed']['total_tokens'], 365)
        self.assertEqual(report['totals']['actual_exact']['total_tokens'], 200)
        self.assertEqual(report['totals']['actual_estimated']['total_tokens'], 100)
        self.assertEqual(report['overcharge_tokens'], {'exact': 25, 'approximate': 50})

    def test_messages(self):
        item = row(exact=False)
        del item['prompt_text']
        item['messages'] = [dict(role='user', content='hello'), dict(role='assistant', content='你好')]
        self.assertEqual(audit_transactions([item])['transactions'][0]['actual']['prompt_tokens'], 4)

    def test_zero_actual_positive_bill(self):
        item = row()
        item['ground_truth']['prompt_tokens'] = 0
        report = audit_transactions([item])
        self.assertEqual(report['exit_code'], 2)
        self.assertIsNone(report['transactions'][0]['ratio'])

    def test_zero_actual_zero_bill(self):
        item = row(0)
        item['ground_truth']['prompt_tokens'] = 0
        self.assertEqual(audit_transactions([item])['exit_code'], 0)

    def test_invalid_schemas(self):
        for invalid in ([], {}, dict(row(), usage=None), dict(row(), duration_ms=float('nan')), dict(row(), messages=[{}])):
            with self.subTest(invalid=invalid):
                report = audit_transactions([invalid])
                self.assertEqual(report['parse_error_count'], 1)
                self.assertEqual(report['exit_code'], 2)

    def test_invalid_token_numbers(self):
        for value in (-1, True, 1.5, '100'):
            item = row()
            item['usage']['prompt_tokens'] = value
            self.assertEqual(audit_transactions([item])['parse_error_count'], 1)

    def test_no_input_mutation(self):
        items = [row()]
        original = copy.deepcopy(items)
        audit_transactions(items)
        self.assertEqual(items, original)
