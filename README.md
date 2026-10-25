# relay-audit

A Python standard-library-only CLI for auditing relay billing offline. It compares
reported and billed usage with supplied ground truth or a deterministic text
estimate, flags impossible cache counts and unexpected model changes, and screens
for unusually long durations. It makes no network requests and needs no API keys.
All bundled fixtures are synthetic; no real accounts or relay traffic are used.

## Install and run

Requires Python 3.9+. Build tooling uses setuptools and wheel; the runtime has no
third-party dependencies.

```sh
python3 -m build
python3 -m pip install dist/relay_audit-0.1.0-py3-none-any.whl
relay-audit --version
relay-audit audit examples/sample.jsonl
relay-audit audit examples/sample.jsonl --json
relay-audit check transaction.json
relay-audit check - --json < transaction.json
python3 -m unittest discover -s tests -v
```

The synthetic sample intentionally exits 2 because it contains failing cases.

## Input

`audit` reads one JSON object per physical line (JSONL). `check` reads a single
JSON object from a file or stdin (`-`). Example transaction:

```json
{"id":"synthetic-normal","requested_model":"example-model","returned_model":"example-model","messages":[{"role":"user","content":"hello world"}],"completion_text":"hello","usage":{"prompt_tokens":4,"completion_tokens":2},"ground_truth":{"prompt_tokens":4,"completion_tokens":2},"duration_ms":20}
```

`prompt_text` may replace `messages`. Message role and content must be strings;
message framing and roles are not estimated. `completion_text` defaults to empty.
`usage` requires nonnegative integer `prompt_tokens` and `completion_tokens`;
`cached_tokens` is optional and defaults to zero. `billed` has the same format
and defaults to `usage`. `ground_truth` optionally supplies prompt/completion
counts from a trusted source. Optional metadata: `id`, `timestamp`, `endpoint`.
`returned_model` and nonnegative finite `duration_ms` are optional.

Invalid JSON, blank lines and invalid transaction schemas are counted and
reported as `parse_error`, never silently skipped. An empty JSONL file is PASS
with zero totals. An empty single-transaction file is a parse error.

Legitimate routing can change the returned model. Provide a JSON allowlist:

```json
{"example-model":["example-model-release-1","example-model-release-2"]}
```

```sh
relay-audit audit transactions.jsonl --allow-model-map allowed.json
```

Only the listed directed pairs are allowed. Missing returned models are not
assumed to be substitutions.

## Findings and evidence

Verdicts are **PASS / SUSPICIOUS / FAIL**. Every finding reports its evidence
class (`exact` or `approximate`). Billing uses total prompt plus completion
counts; cache counts are subsets and are never added again.

| Finding | Rule |
| --- | --- |
| inflated_billing, exact | billed / ground truth: warn >= 1.10, fail >= 1.25 |
| inflated_billing, approximate | billed / estimate: warn >= 1.20, fail >= 1.50 |
| cache_impossible | reported or billed cached count exceeds its own prompt count: fail |
| model_substitution | unequal requested/returned models without an allowed pair: fail |
| duration_outlier | duration per 1,000 actual output tokens > 3x file median: warn |
| parse_error | malformed JSON or schema: fail |

Duration comparisons require at least five valid transactions with duration data
and positive actual output counts. Zero-output rows cannot define a per-token
rate and are excluded from this calculation. Actual output is supplied ground
truth when present, otherwise the estimate. A zero median still uses the strict
`rate > 3 * median` rule. Duration findings inherit the output-count evidence
class. Cache and model findings rely on exact supplied field comparisons.

`--fail-ratio` and `--warn-ratio` override their respective thresholds for both
evidence classes (on either subcommand). Ratios must be positive and finite,
and warn must not exceed fail in either class after defaults are applied.
When actual tokens are zero, zero billed tokens gives ratio 1; positive billed
tokens fails inflation checks and emits JSON `ratio: null` (unbounded ratio).

Reports include reported/billed totals, separate exact and estimated actual
totals, and overcharge tokens for each evidence class. Overcharge is the sum of
positive per-transaction billed-minus-actual differences; undercharges do not
cancel overcharges. Parse errors contribute no token totals.

Exit codes:

- **0**: no warn or fail findings (PASS).
- **1**: warnings only (SUSPICIOUS).
- **2**: any fail finding or parse error (FAIL); invalid CLI arguments also exit 2.

## Honest limitations

This is a screening heuristic, not a proof of fraud. The estimator is **not a
tokenizer**. It counts each CJK ideograph, kana or Hangul character as one token
and counts each other whitespace-separated run as `ceil(characters / 4)`,
splitting runs at CJK boundaries. It does not model provider tokenization,
message framing, tool definitions, images, or hidden/system prompt overhead.
It is deterministic, but its counts are approximate.

Ratios near a threshold are warn, not fail: approximate ratios from 1.20 up to
1.50 and exact ratios from 1.10 up to 1.25 occupy the warning band. A FAIL on
estimator grounds means **investigate**. A FAIL on ground-truth billing grounds
means **the numbers do not add up**. A ratio alone does not prove intent;
incorrect input data, capture coverage, and legitimate provider accounting can
explain discrepancies. Supplied ground truth is trusted, not independently
verified. The tool never uses fraud as a verdict. Model names are compared as
literal strings; use the allowlist for legitimate routing.

## Differentiation

- **blackbox-bill** attributes subscription pools and routing; relay-audit compares
  billed usage with supplied ground truth or locally estimated usage.
- **ccusage / agentcost-cli** report usage and cost; this tool checks internal
  consistency and billing inflation signals.
- **Packet-capture tools** collect traffic; relay-audit audits an exported capture
  or client log. It does not capture traffic or contact a relay.
