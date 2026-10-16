# QA v2 issue #99 @ 8b20f29

R1 from the v1 review (an empty metric series became a zero) is fixed.

$ uv run --locked ruff check
All checks passed!

$ uv run --locked pytest -q
406 passed, 4 skipped

New tests: `test_an_empty_series_is_a_gap_and_no_file_not_a_zero`, `test_no_5xx_datapoints_is_a_real_zero_when_requests_were_counted`.
No UI: no screenshots.
