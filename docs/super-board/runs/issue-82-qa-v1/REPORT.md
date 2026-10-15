# QA v1 · issue #82 · head 220677e

| AC | Result | Proof |
|---|---|---|
| AC1 each metric matches hand-computed value | ✅ | 19 cases `tests/test_analyst.py:52` |
| AC2 missing raw file is a gap, never zero | ✅ | 6 files `tests/test_analyst.py:68` |
| AC3 zero requests: no raise, no NaN | ✅ | `tests/test_analyst.py:77` |
| AC4 every figure source exists | ✅ | real + edge `tests/test_analyst.py:88` |
| AC5 no account id or email | ✅ | `tests/test_analyst.py:126`, `scan.txt` 0 hits |
| AC6 ruff + pytest | ✅ | `ruff.txt` clean, `pytest.txt` 240 passed |

k6 Rate shape: legacy `--summary-export` writes `passes` = count of true samples; for
`http_req_failed` true = failed. Fixture `value` 0.002 = 6/3000 agrees.

Test gaps (not written), see `gaps.txt`:
- Medium: a NaN harness CPU point hides a peak above the threshold (`advisor/analyst.py:95`).
- Medium: `load_usd` Infinity gives an inf figure; `metrics.json` then fails to reload.
- Low: deploy start after target healthy gives a negative `deploy_time_s`.
- Low: k6 `http_req_failed` counts 3xx as success; SPEC 7 says non-2xx.
