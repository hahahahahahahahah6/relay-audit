"""Validation, per-transaction findings and file-wide aggregation."""
import math
import statistics
from .estimator import estimate_tokens


def counts(value, name):
    if not isinstance(value, dict):
        raise ValueError(name + ' must be an object')
    result = {}
    for key in ('prompt_tokens', 'completion_tokens', 'cached_tokens'):
        if key not in value and key != 'cached_tokens':
            raise ValueError(name + '.' + key + ' is required')
        number = value.get(key, 0)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ValueError(name + '.' + key + ' must be a nonnegative integer')
        result[key] = number
    return result


def validate(row):
    if not isinstance(row, dict):
        raise ValueError('transaction must be an object')
    if not isinstance(row.get('requested_model'), str):
        raise ValueError('requested_model must be a string')
    for key in ('returned_model', 'prompt_text', 'completion_text'):
        if key in row and not isinstance(row[key], str):
            raise ValueError(key + ' must be a string')
    if 'messages' in row:
        if not isinstance(row['messages'], list):
            raise ValueError('messages must be an array')
        for msg in row['messages']:
            if not isinstance(msg, dict) or not all(isinstance(msg.get(k), str) for k in ('role', 'content')):
                raise ValueError('messages require string role and content')
    elif 'prompt_text' not in row:
        raise ValueError('messages or prompt_text is required')
    usage = counts(row.get('usage'), 'usage')
    billed = counts(row.get('billed', row.get('usage')), 'billed')
    exact = 'ground_truth' in row
    if exact:
        actual = counts(row['ground_truth'], 'ground_truth')
    else:
        prompt = (sum(estimate_tokens(m['content']) for m in row['messages'])
                  if 'messages' in row else estimate_tokens(row['prompt_text']))
        actual = dict(prompt_tokens=prompt, completion_tokens=estimate_tokens(row.get('completion_text', '')), cached_tokens=0)
    duration = row.get('duration_ms')
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0):
        raise ValueError('duration_ms must be finite and nonnegative')
    return usage, billed, actual, exact


def audit_transactions(rows, allow_model_map=None, fail_ratio=None, warn_ratio=None, parse_errors=None):
    """Audit decoded rows; invalid schemas become explicit parse errors."""
    errors = list(parse_errors or [])
    transactions = []
    totals = {k: dict(prompt_tokens=0, completion_tokens=0, total_tokens=0) for k in ('reported', 'billed', 'actual_exact', 'actual_estimated')}
    overcharge = dict(exact=0, approximate=0)
    durations = []
    for index, row in enumerate(rows, 1):
        try:
            usage, billed, actual, exact = validate(row)
        except ValueError as exc:
            errors.append(dict(type='parse_error', severity='fail', evidence='exact', transaction=index, message=str(exc)))
            continue
        evidence = 'exact' if exact else 'approximate'
        actual_total = actual['prompt_tokens'] + actual['completion_tokens']
        billed_total = billed['prompt_tokens'] + billed['completion_tokens']
        ratio = billed_total / actual_total if actual_total else (None if billed_total else 1.0)
        effective_ratio = float('inf') if ratio is None else ratio
        fail = fail_ratio if fail_ratio is not None else (1.25 if exact else 1.50)
        warn = warn_ratio if warn_ratio is not None else (1.10 if exact else 1.20)
        findings = []
        def add(kind, severity, message, basis='exact'):
            findings.append(dict(type=kind, severity=severity, evidence=basis, message=message))
        add('inflated_billing', 'fail' if effective_ratio >= fail else 'warn' if effective_ratio >= warn else 'ok',
            'Billed / actual tokens; null ratio means positive billed tokens with zero actual tokens.' if ratio is None else 'Billed / actual tokens: {:.4f}'.format(ratio), evidence)
        for name, data in (('reported', usage), ('billed', billed)):
            if data['cached_tokens'] > data['prompt_tokens']:
                add('cache_impossible', 'fail', name + ' cached tokens exceed prompt tokens')
        returned = row.get('returned_model')
        if returned is not None and returned != row['requested_model'] and returned not in (allow_model_map or {}).get(row['requested_model'], []):
            add('model_substitution', 'fail', 'Returned model differs from requested model')
        tx = dict(id=row.get('id', index), evidence=evidence, reported=usage, billed=billed, actual=actual, ratio=ratio, findings=findings)
        transactions.append(tx)
        for key, data in (('reported', usage), ('billed', billed), ('actual_exact' if exact else 'actual_estimated', actual)):
            for field in ('prompt_tokens', 'completion_tokens'):
                totals[key][field] += data[field]
                totals[key]['total_tokens'] += data[field]
        overcharge[evidence] += max(0, billed_total - actual_total)
        if row.get('duration_ms') is not None and actual['completion_tokens'] > 0:
            durations.append((tx, row['duration_ms'] * 1000 / actual['completion_tokens']))
    median = statistics.median(rate for _, rate in durations) if durations else None
    if len(durations) >= 5:
        for tx, rate in durations:
            if rate > 3 * median:
                tx['findings'].append(dict(type='duration_outlier', severity='warn', evidence=tx['evidence'], message='Duration per 1k actual output tokens exceeds 3x file median'))
    severity = 'fail' if errors else 'ok'
    for tx in transactions:
        tx['verdict'] = verdict(tx['findings'])
        for finding in tx['findings']:
            if finding['severity'] == 'fail' or finding['severity'] == 'warn' and severity == 'ok':
                severity = finding['severity']
    return dict(verdict={'ok': 'PASS', 'warn': 'SUSPICIOUS', 'fail': 'FAIL'}[severity],
                exit_code={'ok': 0, 'warn': 1, 'fail': 2}[severity], transactions=transactions,
                parse_errors=errors, parse_error_count=len(errors), totals=totals,
                overcharge_tokens=overcharge, duration_median_ms_per_1k_output_tokens=median)


def verdict(findings):
    levels = {f['severity'] for f in findings}
    return 'FAIL' if 'fail' in levels else 'SUSPICIOUS' if 'warn' in levels else 'PASS'
