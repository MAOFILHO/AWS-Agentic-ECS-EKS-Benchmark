# QA v2 · issue #82 · head f2174d6

- AC1–AC6 ✅: ruff clean · 257 passed (37 in `tests/test_analyst.py`) · account id / email scan 0 hits.
- R1 ✅ NaN or Infinity harness CPU point → `cloudwatch-harness-cpu.json` gap (`tests/test_analyst.py:132`).
- R2 ✅ `load_usd` Infinity, -Infinity, NaN → `cost.json` gap; `metrics.json` reloads (`tests/test_analyst.py:139`).
- Red check: with the pre-fix `rawformats.py`, the R1 test and the `Infinity` R2 case fail.
- Probes: Infinity k6 `med` → gap; NaN point beside a 90% peak → file is a gap, `harness_bound` false.
