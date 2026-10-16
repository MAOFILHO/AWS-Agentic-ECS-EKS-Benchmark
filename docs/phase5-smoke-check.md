# Phase 5 smoke check

Full advisor graph run against real AWS by the repo owner in `us-east-1` on 2026-10-16.
Cell `ecs-fargate`, profile `steady`, `LLM_MODE=mock`. Run id `smoke5`. Source: owner evidence on issue #96.

## Results

| Item | Result |
|---|---|
| Run status | `done`, no errors |
| Whole run time | about 32 minutes (target: under 35 minutes) |
| Spend | USD 0.13 estimated at the gate (target: under USD 2) |
| Stacks | 3 created, 3 `DELETE_COMPLETE` |
| Live leftovers for `smoke5` | 0 (teardown said "no leftovers" on the first check) |
| Report | written by Bedrock (`amazon.nova-micro-v1:0`) |

## Gate

The CLI shows a cost estimate and asks before the first deploy.

### Answer "no" (run `smoke0`)

- Estimate shown: USD 0.13.
- Output: `run smoke0: stopped (human: no)`.
- The CLI stopped before the first deploy. No stack was created.

### Answer "yes" (run `smoke5`)

Estimate shown: USD 0.13.

| Line | USD |
|---|---|
| fargate vcpu | 0.0017 |
| fargate memory | 0.0004 |
| alb | 0.0037 |
| alb lcu | 0.0013 |
| cloudwatch ingest | 0.0500 |
| nat gateway | 0.0075 |
| nat data | 0.0450 |
| vpc endpoints | 0.0083 |
| harness vcpu | 0.0067 |
| harness memory | 0.0015 |

## Run time

Times come from `runs/smoke5/commands.jsonl`. Load, collection, analysis and report time comes from file times in the raw folder (local time).

| Step | Time |
|---|---|
| Network stack deploy | 157.6 s |
| Harness stack deploy | 66.9 s |
| ECS cell deploy | 278.8 s |
| Deploy total | 503.3 s |
| Teardown | 759.8 s (12.7 min) |
| Load, collection, analysis, report | about 10 min (raw folder created 18:53, `metrics.json` and `report.md` written 19:03) |
| Whole run | about 32 min |

About 21 of the 32 minutes is AWS create and delete time (503.3 s deploy plus 759.8 s teardown). That time does not shrink with code changes. For that reason the smoke target moves from 25 to 35 minutes in `docs/SPEC.md` (G7 and section 11).

## First figures

All figures are for `ecs-fargate`. Each is in `results/smoke5/metrics.json` and in `results/smoke5/report.md`.

| Figure | Value | Source |
|---|---|---|
| p50 | 5.27 ms | `ecs-fargate/k6-steady.json` |
| p95 | 8.80 ms | `ecs-fargate/k6-steady.json` |
| p99 | 86.92 ms | `ecs-fargate/k6-steady.json` |
| Error rate | 0.0 | `ecs-fargate/k6-steady.json` |
| Scale out | 80.59 s | `ecs-fargate/events.json` |
| Cold start | 75.05 s | `ecs-fargate/events.json` |
| Deploy time | 440.27 s | `ecs-fargate/deploy.json` |
| Config lines | 436 | `ecs-fargate/config.json` |
| Moving parts | 4 | `ecs-fargate/config.json` |

- The report holds no number that is not in `metrics.json`.
- The EKS columns show `N/A` because the run had one cell.
- `metrics.json` lists one gap: `ecs-fargate/cost.json: missing`. `harness_bound` is `false`.

## What failed on the way

Six runs (`smoke0` to `smoke5`) cost about USD 1.5 in all. This is an estimate. Cost Explorer lags by a day.

| Run | What happened | Fix |
|---|---|---|
| smoke0 | The "no" test. Stopped at the gate. | None needed |
| smoke1 | Harness CPU had no data (Container Insights off) | #105 |
| smoke2 | A stale subnet tag failed the leftover check | #107 |
| smoke3 | The analyst and reporter were not wired into the graph | #109 |
| smoke4 | The owner's connection to AWS dropped during teardown. The network stack delete had already started and finished on its own. All 3 stacks reached `DELETE_COMPLETE` and no NAT gateway was left. | No code change |
| smoke5 | Passed | None needed |

Card #103 added the harness deploy, the harness teardown and `make push-k6` before the first run.

## Commands run

a. `aws login --profile default-advanced`, then set `AWS_PROFILE`, `AWS_REGION=us-east-1`, `AWS_DEFAULT_REGION=us-east-1` and `LLM_MODE=mock`
b. `make mirror-base`
c. `make push-image ARGS=--allow-dirty` (workload image, `linux/amd64`, tag `df4371c`), then set `IMAGE_URI`
d. `make push-k6 ARGS=--allow-dirty` (tag `k6-df4371c`), then set `K6_IMAGE_URI`
e. `uv run --locked python -m advisor run --cells ecs-fargate --profile steady --region us-east-1 --run-id smoke0`, answer `N`
f. `caffeinate -dimsu uv run --locked python -m advisor run --cells ecs-fargate --profile steady --region us-east-1 --run-id smoke5`, answer `y`
g. Read-only checks of the CloudFormation stacks and NAT gateways
