# QA · issue #122 · v1

Branch `issue-122-refresh-sample-report`, builder head `c87972d`. Docs-only change, no UI.

| AC | Result | Proof |
|---|---|---|
| 1. `results/sample/report.md` equals the new report | pass | `cmp.log`: byte-identical to `report-new.md` |
| 2. README table units on row labels only, verdict paragraph | pass | new `test_tables_put_units_on_row_labels_only` (red on main's report, green here); README verdict paragraph under the table |
| 3. Every README number traces; `tests/test_readme.py` passes | pass | `test_readme.log` 6 passed; full suite `pytest-full.log` |
| 4. No 12-digit account id, email or ALB host in tracked files | pass | `secret-scan.txt`: no 12-digit token outside lockfile hashes; ALB hosts only as placeholders in `tests/` (on main, untouched); `test_published_files_hold_no_alb_host_or_email` green |

Test gaps (not written): none High. Low: AC1 cannot be a repo test, the source report lives outside the repo.
