# Issue #134 QA v1

Head tested: `11027c9` (builder) plus the QA test commit.

| AC | Result | Proof |
|---|---|---|
| 1 Prompt names all 4 profiles (sample4 fixture) | pass | `tests/test_reporter_layout.py::test_prompt_asks_for_a_table_per_profile_of_sample4` |
| 2 Draft missing a profile rejected, all accepted | pass | `test_missing_profile_section_is_rejected_and_all_accepted`, `test_draft_missing_a_profile_is_retried`, QA edge test `test_profile_heading_matches_whole_names_in_any_case` |
| 3 `make report RUN_ID=sample4` has all 4 profiles, passes numcheck | pass, 2 of 2 live Bedrock runs | `report-run1.md`, `report-run2.md`, `ac3-check.log` |
| 4 Existing reporter tests pass | pass | `suite.log`: ruff clean, 534 passed, 4 skipped |

## Findings outside the ACs (live reports)

- Profile tables drop a cell: `ecs-ec2` ran all 4 profiles but has a column only in `scale-from-min` (both runs).
- Per-cell rows are mis-attributed: run 1 shows eks-ec2 deploy time 441.6 s (metrics: 1188.4 s, 441.6 belongs to ecs-ec2); run 2 leaves it blank. numcheck passes because 441.6 is a real figure.
- Run 1 Verdict says Fargate has higher idle cost than EKS on EC2; metrics say 0.0348 vs 0.1641 USD/h.
- Per-cell rows repeat inside each profile table (builder noted this).

## Test gaps (not written)

- Medium: a profile table missing a cell column is not checked (needs app code).
- Low: values of 10000 or more lose integer digits to the 4-significant-digit cut (12345.6 shown as 12350), which numcheck would reject; no sample run has such a value today (max 1188.4).
