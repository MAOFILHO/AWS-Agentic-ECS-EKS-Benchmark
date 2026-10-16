# QA v1 issue #105 @ 9c1245b
- AC1 tests/test_harness_template.py::test_cluster_turns_container_insights_on pass
- AC2 tests/test_collector.py::test_a_harness_cpu_series_that_stays_empty_is_one_gap_after_the_waits pass
- AC3 tests/test_collector.py::test_harness_cpu_waits_for_the_metric_lag_then_writes_the_file pass
- AC4 ruff: all checks passed. pytest: 430 passed, 4 skipped. Diff 52 added, 6 removed lines.
- Cost: one short task adds a few Container Insights metrics, under USD 0.01. No estimate change.
