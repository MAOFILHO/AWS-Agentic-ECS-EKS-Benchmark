# Issue #135 QA v1

Head tested: `5d845fe` (builder) plus QA edge tests.

| AC | Result | Proof |
|---|---|---|
| AC1 sample4 rejects the wrong cost ranking, accepts the right one | pass | `tests/test_reporter_rankings.py::test_sample4_rejects_the_wrong_cost_ranking_and_accepts_the_right_one` |
| AC2 wrong latency ranking rejected | pass | `tests/test_reporter_rankings.py::test_wrong_latency_ranking_is_rejected_and_the_right_one_accepted` |
| AC3 draft with no ranking sentence passes | pass | `tests/test_reporter_rankings.py::test_draft_with_no_ranking_sentence_passes` |
| AC4 existing numcheck and reporter tests pass | pass | `pytest-full.txt`: 553 passed, 4 skipped; `ruff.txt` clean |

Real `results/sample4/report.md` against its `metrics.json`: only the wrong sentence is flagged (`real-sample4.txt`).

## Test gaps (not written)
- Medium: a negated sentence ("ECS EC2 is not more costly than EKS EC2") is rejected. A false rejection, which costs one retry.
- Medium: only the first comparison in a sentence is checked. "ECS EC2 is cheaper than EKS EC2, but EKS EC2 is faster than ECS Fargate" passes, though the second half is wrong.
- Low: "deploys faster" or "scales faster" is read as a latency claim. It passes when any latency figure agrees, so it rarely misfires on sample4.

Edge probes: `edge-probe.txt`. Local tests: `uv run --locked ruff check && uv run --locked pytest`.
