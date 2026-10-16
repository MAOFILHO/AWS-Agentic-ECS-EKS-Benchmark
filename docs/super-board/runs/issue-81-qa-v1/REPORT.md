# QA v1 · issue #81 · head a642abb

| AC | Result | Proof |
|---|---|---|
| 1 hand-computed estimate | pass | `tests/test_cost.py::test_estimate_for_default_matrix_matches_hand_computed_figure`; recomputed by hand: 0.7890167 USD |
| 2 missing price is a gap | pass | `test_missing_price_is_a_gap_not_a_zero`, plus `test_api_error_is_a_gap_and_the_estimate_counts_no_zero_lines`, `test_two_different_prices_are_a_gap_not_a_pick` |
| 3 shared lines | pass | `test_shared_costs_are_own_lines_not_inside_a_cell` |
| 4 default matrix | pass | `test_default_matrix_is_ecs_fargate_vs_eks_ec2`, `test_asked_cells_replace_the_default_pair` |
| 5 no account id / email | pass | `test_no_account_id_or_email_in_outputs_or_files`; regex scan of `git diff origin/main...HEAD`: no match |
| 6 ruff + pytest | fail | ruff clean; pytest 219 passed, 1 failed (`test_alb_hour_reads_the_region_price_from_live_shaped_rows`) |

## Live Pricing API (read-only, us-east-1, t3.medium, 2026-10-15)

`read_prices(boto3 pricing client, "us-east-1", "t3.medium")` on head a642abb:

- 9 of 10 prices read: eks 0.10, fargate vCPU 0.04048, fargate GB 0.004445, ec2 0.0416, alb lcu 0.008, nat hour 0.045, nat GB 0.045, vpce 0.01, cw ingest 0.50
- gap: `price alb_hour: 2 different prices in AWSELB for us-east-1`
- the `alb_hour` filter (`usagetype CONTAINS LoadBalancerUsage`, family `Load Balancer-Application`) matches 3 live rows:
  - `LoadBalancerUsage` · AWS Region · 0.0225
  - `TS-LoadBalancerUsage` · AWS Region · 0.005 (trust store)
  - `Outposts-LoadBalancerUsage` · AWS Outposts · 0.0225
- the fixture has only `USE1-LoadBalancerUsage`, so the fixture tests cannot see this.

Fixed looks like: the live-shaped rows give `alb_hour == 0.0225` and no gap (for example, add `locationType = AWS Region` and drop usagetypes that carry a prefix before `LoadBalancerUsage` other than the region code).
