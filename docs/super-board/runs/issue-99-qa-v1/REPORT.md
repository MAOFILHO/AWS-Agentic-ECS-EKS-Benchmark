# QA v1 issue #99 @ 5743a7f

$ uv run --locked ruff check
All checks passed!

$ uv run --locked pytest tests/test_collector.py -v
rootdir: /Users/marco/K21/Temp/AWS-Agentic-ECS-EKS-Benchmark/.claude/worktrees/issue-99-qa
configfile: pyproject.toml
plugins: langsmith-0.14.4, anyio-4.15.1
collecting ... collected 8 items

tests/test_collector.py::test_every_raw_file_passes_its_reader PASSED    [ 12%]
tests/test_collector.py::test_analyst_yields_the_scale_out_time_for_both_fixtures PASSED [ 25%]
tests/test_collector.py::test_config_counts_the_cells_template_lines PASSED [ 37%]
tests/test_collector.py::test_no_file_holds_an_account_id_or_an_email PASSED [ 50%]
tests/test_collector.py::test_the_collector_only_reads PASSED            [ 62%]
tests/test_collector.py::test_a_failed_section_is_a_gap_and_the_others_still_run PASSED [ 75%]
tests/test_collector.py::test_a_cell_without_a_load_window_is_a_gap_not_empty_files PASSED [ 87%]
tests/test_collector.py::test_a_failing_kubectl_is_a_gap PASSED          [100%]

============================== 8 passed in 0.57s ===============================

$ uv run --locked pytest -q
................................................                         [100%]
404 passed, 4 skipped in 5.58s

$ grep -rEn "[0-9]{12}" advisor/collector.py
no match
