"""Offline command-line interface."""
import argparse
import json
import math
import sys
from pathlib import Path
from . import __version__
from .audit import audit_transactions, validate


def reject_constant(value):
    raise ValueError('Nonfinite JSON number: ' + value)


def decode(text):
    return json.loads(text, parse_constant=reject_constant)


def positive_ratio(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('ratio must be a finite positive number')
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('ratio must be a finite positive number')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description='Audit relay billing offline. Estimator findings are screening evidence.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('audit', 'check'):
        sub = commands.add_parser(name)
        sub.add_argument('file', help='JSONL transactions' if name == 'audit' else 'JSON transaction, or - for stdin')
        sub.add_argument('--json', action='store_true', help='emit JSON report')
        sub.add_argument('--allow-model-map', help='JSON object mapping requested model to allowed returned models')
        sub.add_argument('--fail-ratio', type=positive_ratio)
        sub.add_argument('--warn-ratio', type=positive_ratio)
    args = parser.parse_args(argv)
    for exact in (True, False):
        fail = args.fail_ratio if args.fail_ratio is not None else (1.25 if exact else 1.50)
        warn = args.warn_ratio if args.warn_ratio is not None else (1.10 if exact else 1.20)
        if warn > fail:
            parser.error('warn ratio must not exceed fail ratio for either evidence class')
    rows, errors = [], []
    allow = {}
    try:
        if args.allow_model_map:
            allow = decode(Path(args.allow_model_map).read_text(encoding='utf-8'))
            if not isinstance(allow, dict) or any(not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(item, str) for item in v) for k, v in allow.items()):
                raise ValueError('allow-model-map must map model strings to arrays of model strings')
        content = sys.stdin.read() if args.command == 'check' and args.file == '-' else Path(args.file).read_text(encoding='utf-8')
        if args.command == 'check':
            rows.append(decode(content))
        else:
            for line_number, line in enumerate(content.splitlines(), 1):
                try:
                    row = decode(line)
                    # Validate here to retain original physical line numbers.
                    validate(row)
                    rows.append(row)
                except (ValueError, TypeError) as exc:
                    errors.append(dict(type='parse_error', severity='fail', evidence='exact', line=line_number, message=str(exc)))
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(dict(type='parse_error', severity='fail', evidence='exact', message=str(exc)))
    report = audit_transactions(rows, allow, args.fail_ratio, args.warn_ratio, errors)
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
    else:
        print('{}: {} transactions, {} parse errors'.format(report['verdict'], len(report['transactions']), report['parse_error_count']))
        for tx in report['transactions']:
            print('  {}: {} ({})'.format(tx['id'], tx['verdict'], tx['evidence']))
            for finding in tx['findings']:
                print('    {} [{}; {}]: {}'.format(finding['type'], finding['severity'], finding['evidence'], finding['message']))
        for error in report['parse_errors']:
            print('  parse_error: ' + json.dumps(error, ensure_ascii=True))
        print('Totals: ' + json.dumps(report['totals'], sort_keys=True))
        print('Overcharge tokens by evidence class: ' + json.dumps(report['overcharge_tokens'], sort_keys=True))
        print('Approximate FAIL means investigate; exact billing FAIL means the numbers do not add up.')
    return report['exit_code']
