# QA v1 · issue 119

- `uv run --locked python -m pytest tests -q` : 483 passed, 4 skipped
- `tests/test_reporter_layout.py` : 4 passed (4 sections in order, unit-in-row rule, has_verdict reject/accept, retry on missing Verdict)
- Edge probes of `has_verdict`: `## Verdict` + text true; `### Verdict` true; CRLF false; bold `**Verdict**` false; heading followed by heading false (all fall back to the retry, fail-safe)
- Live `AWS_REGION=us-east-1 make report RUN_ID=sample3` : `## Verdict` paragraph first, table headers are cell names only, units in row labels. Output: `sample3-report.md`
