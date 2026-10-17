# QA v1 · issue #118 · PR #121 · builder head 0f238af

| AC | Result | Proof |
|---|---|---|
| 1 `results/sample/` tracked with metrics.json, report.md, chart.svg | pass | `git ls-files results/sample` lists all three; `test_sample_run_is_tracked`; `.gitignore` has `!results/sample/` |
| 2 README shows chart, table, verdict, cost of this run | pass | `test_readme_shows_chart_table_verdict_cost_and_limits`; cost rows checked by hand: cells 0.2019, shared 0.1233, run 0.3252 match the cost-file sums |
| 3 every README number from metrics.json or cost files, checked by a test | pass | `test_every_readme_number_comes_from_the_sample_run` (advisor.numcheck); mutations M1 (invented 9.99 ms) and M2 (run total 0.4252) both turn it red |
| 4 README states the 4 limits | pass | Limits section: mock, steady only, three cells / eks-fargate not run, one run; M4 (drop eks-fargate) turns the test red |
| 5 no account id, email, ALB host in tracked files; guard test passes | pass | own grep over README.md + results/sample: no 12-digit run, no email, no amazonaws/arn/IP; only 12-digit hits repo-wide are hex hashes in uv.lock / skills-lock.json (not in this PR); M3 (ALB host) and M5 (account id) turn the guards red |

Extra: `make chart RUN_ID=sample` redraws chart.svg byte-identical. New test
`test_published_chart_matches_the_published_metrics` keeps it that way (M6 stale chart turns it red).

Local tests: `uv run --locked ruff check` (clean) · `uv run --locked pytest -q` 484 passed, 4 skipped. Logs: verify.log, mutation.log.

Test gaps (not written):
- Medium: numcheck accepts a shown number within half a step of ANY known value, so a rounded figure like `$0.33` could pass against an unrelated value. Tighter matching is a numcheck change, not this ticket.
- Low: the README table is not diffed against report.md cell by cell (headers were renamed on purpose).
