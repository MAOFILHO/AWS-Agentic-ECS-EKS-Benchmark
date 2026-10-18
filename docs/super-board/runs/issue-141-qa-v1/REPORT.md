# QA v1 for #141 at 7544c4a

- Local tests: `uv run --locked pytest -q` gives 561 passed, 4 skipped; `ruff check .` clean
- Fixture matches `results/short1/metrics.json`: 47 figures, same values, same gaps
- Real short1 metrics rendered through `make_reporter`: `short1-report-from-real-metrics.md` (no blank, no N/A where a value exists)
- Not checked: `usd`-unit mixing in source data (spike p50 4.078 vs 5406 across cells is in metrics.json itself)
