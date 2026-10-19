# Agentic Platform Advisor: ECS vs EKS for an AI agent fleet

Should a team host a fleet of AI agents on Amazon ECS or on Amazon EKS, and on Fargate or on EC2?
This repo deploys the same agent workload on each platform, drives the same load at it, and
reports latency, deploy time, configuration size and cost side by side. A LangGraph advisor plans
the run, asks before any spend, deploys, measures, tears everything down and writes the report.

## Verdict

Three runs (`short1`, `short2`, `short3`) in us-east-1, mock workload, on ECS Fargate, ECS EC2 and
EKS EC2. Each figure is the median, with the min to max of the 3 runs in brackets. One finding per
question:

1. **Scale out under a burst: EKS EC2 is fastest.** In the `spike` profile EKS EC2 went from alarm
   to a healthy new replica in 21 s (21 to 23), against 72.8 s (72.5 to 76.5) on ECS EC2 and
   75.9 s (72.8 to 84.7) on ECS Fargate. In `scale-from-min` only ECS EC2 scaled out, in 186 s
   (148 to 233). [Chart](results/published/chart-burst-scaling.svg).
2. **Latency under a burst: only ECS Fargate stayed fast.** In `spike` ECS Fargate held p50
   4.08 ms and p95 4.84 ms. ECS EC2 had a p50 of 5412 ms (5406 to 6210), so most of its requests
   waited. EKS EC2 kept p50 at 5.79 ms but its p95 was 4675 ms (3862 to 5311): most requests were
   fast and a tail waited. One EKS EC2 run returned errors on 1.57% of requests. The cause is not
   proven: the runs did not record why requests waited on the EC2 cells.
   [Chart](results/published/chart-burst-latency.svg).
3. **Cost: ECS Fargate is cheapest.** Idle cost was 0.0348 USD/h on ECS Fargate, 0.0641 on ECS EC2
   and 0.1641 on EKS EC2. Per thousand requests: 0.0009, 0.0011 and 0.0015 USD.
   [Chart](results/published/chart-cost.svg).
4. **Deploy: EKS EC2 is slowest.** ECS Fargate deployed in 426 s (401 to 439), ECS EC2 in 467 s
   (460 to 505) and EKS EC2 in 1125 s (1041 to 1171). [Chart](results/published/chart-deploy.svg).

Read the limits below before you generalise it.

![How fast does it scale out under a burst?](results/published/chart-burst-scaling.svg)

![How slow do replies get under a burst?](results/published/chart-burst-latency.svg)

![What does it cost?](results/published/chart-cost.svg)

![How long does a deploy take?](results/published/chart-deploy.svg)

## Which one should I pick?

- **Replies must stay fast through a burst:** ECS Fargate. Its `spike` p95 was 4.84 ms, against
  6167 ms on ECS EC2 and 4675 ms on EKS EC2.
- **New capacity must arrive fast:** EKS EC2. It scaled out in 21 s, but in the same burst its p95
  reached 4675 ms, so test the tail before you rely on it.
- **The idle bill matters most:** ECS Fargate, at 0.0348 USD/h idle, against 0.1641 on EKS EC2.

## Comparison

Written by `python -m scripts.make_readme_tables` from
[`results/published/aggregate.json`](results/published/aggregate.json), which
`scripts/aggregate_runs.py` built from the three runs' `metrics.json`
([`short1`](results/published/short1-metrics.json), [`short2`](results/published/short2-metrics.json),
[`short3`](results/published/short3-metrics.json)). Each cell is `median (min to max)`, or one value when
all runs agreed.

<!-- tables: python -m scripts.make_readme_tables -->
**Profile `spike`**

| Metric | ecs-fargate | ecs-ec2 | eks-ec2 |
|---|---|---|---|
| p50 latency (ms) | 4.08 (3.76 to 4.33) | 5412 (5406 to 6210) | 5.79 (5.74 to 5.88) |
| p95 latency (ms) | 4.84 (4.83 to 5.63) | 6167 (6077 to 7586) | 4675 (3862 to 5311) |
| p99 latency (ms) | 12.2 (10.4 to 16.1) | 7174 (7168 to 8252) | 6051 (6020 to 6056) |
| Error rate | 0.00% | 0.00% | 0.00% (0.00% to 1.57%) |
| Scale out (s) | 75.9 (72.8 to 84.7) | 72.8 (72.5 to 76.5) | 21 (21 to 23) |
| Cold start (s) | 66.8 (66.0 to 74.3) | 66.0 (65.5 to 66.2) | 21 (20 to 23) |

**Profile `scale-from-min`**

| Metric | ecs-fargate | ecs-ec2 | eks-ec2 |
|---|---|---|---|
| p50 latency (ms) | 4.77 (4.09 to 5.22) | 5.48 (5.03 to 5.77) | 5.35 (5.33 to 5.75) |
| p95 latency (ms) | 6.13 (4.83 to 6.55) | 6.55 (5.95 to 11.1) | 8.28 (8.23 to 9.09) |
| p99 latency (ms) | 20.0 (9.30 to 45.3) | 11.0 (9.98 to 26.7) | 15.6 (13.0 to 16.1) |
| Error rate | 0.00% | 0.00% | 0.00% |
| Scale out (s) | n/a | 186 (148 to 233) | n/a |
| Cold start (s) | n/a | 183 (144 to 222) | n/a |

**Per cell**

| Metric | ecs-fargate | ecs-ec2 | eks-ec2 |
|---|---|---|---|
| Deploy time (s) | 426 (401 to 439) | 467 (460 to 505) | 1125 (1041 to 1171) |
| Config lines | 436 | 436 | 534 |
| Moving parts | 4 | 4 | 4 |
| Idle cost (USD/h) | 0.0348 | 0.0641 | 0.1641 |
| USD per thousand requests | 0.0009 | 0.0011 (0.0011 to 0.0012) | 0.0015 (0.0015 to 0.0016) |
<!-- /tables -->

## Cost of one run

This is the single `sample3` run (steady profile), the only run with its cost files published.
**About $0.33** for the load window: $0.3252 in all, from the per-cell
[`cost.json`](results/sample/raw) files and [`shared-cost.json`](results/sample/raw/shared-cost.json).

| Item                          | USD    |
|-------------------------------|--------|
| ECS Fargate cell              | 0.0577 |
| ECS EC2 cell                  | 0.0632 |
| EKS EC2 cell                  | 0.0810 |
| Cells, total                  | 0.2019 |
| Shared NAT gateway            | 0.0244 |
| Shared NAT data               | 0.0450 |
| Shared VPC endpoints          | 0.0271 |
| Shared harness vCPU           | 0.0220 |
| Shared harness memory         | 0.0048 |
| Shared, total                 | 0.1233 |
| **Run, total**                | **0.3252** |

This prices only the load window. Real spend, with deploy and teardown time, was about two to
three times this estimate.

## Limits

- **3 runs.** Three repeats in us-east-1. The brackets show their spread; there are no error bars.
- **Two profiles.** Only `spike` and `scale-from-min` were run; no steady, step or soak profile.
- **Mock workload.** The agent ran with `LLM_MODE=mock`, so the numbers show platform overhead
  only, not model latency.
- **Three cells.** ECS Fargate, ECS EC2 and EKS EC2; **eks-fargate was not run**.
- **Two gaps.** In `scale-from-min`, ECS Fargate and EKS EC2 logged no scale-out in their load
  window: the load stayed under their CPU scaling target, so they have no scale-out or cold-start
  figure there.

## How to run

You need an AWS account, an `AWS_PROFILE` (SSO or `aws login`), a region, `uv`, Docker, `helm`
and `kubectl`. The image targets build Intel/AMD Linux images by default, even on an Apple silicon Mac.

```bash
make bootstrap                      # budget alarm, deploy role, OIDC; needs BUDGET_EMAIL
make mirror-base                    # mirror the base images into ECR
make push-image                     # build and push the workload image; export IMAGE_URI
# build and push the load-generator image too (see the Makefile); export K6_IMAGE_URI
make benchmark RUN_ID=<id>          # prints the estimate and asks y/N before any spend
make report RUN_ID=<id>             # rewrite report.md from metrics.json
make chart RUN_ID=<id>              # draw chart.svg from metrics.json
make teardown RUN_ID=<id>           # delete every stack of a run and check nothing is left
```

`make benchmark` always tears its stacks down, even on failure. Run `make teardown` yourself only
when a run was interrupted. Resume a stopped run with `uv run --locked python -m advisor resume <run-id>`.

### Safety rules

From the safety rules section of [`docs/SPEC.md`](docs/SPEC.md):

- All AWS resources are CloudFormation and all Kubernetes objects are Helm. No console step.
- No IAM access keys and no root user.
- The bootstrap stack holds a monthly AWS Budgets alarm.
- Every benchmark stack carries a `ttl-hours` tag, and teardown runs in a `finally` path. A
  scheduled janitor workflow deletes any stack past its TTL.
- All images come from ECR, including the mirrored base images.

## More

- [`docs/METHOD.md`](docs/METHOD.md): how the comparison was run, so you can judge whether it is fair.
- [`docs/SPEC.md`](docs/SPEC.md): the question, matrix, metric definitions and cost model.
- [`docs/architecture.md`](docs/architecture.md): how the advisor and the cells fit together.
