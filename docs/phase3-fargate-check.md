# Phase 3 Fargate check

Real-account deploy of the `ecs-fargate` cell, run by the repo owner in `us-east-1` on 2026-10-14.
Run id `p3fg2`. Source: owner evidence comment on issue #46.

## Results

| Item | Result |
|---|---|
| Stack `p3fg2-network` | `CREATE_COMPLETE` |
| Stack `p3fg2-ecs-fargate` | `CREATE_COMPLETE` |
| ECS stack create time | about 5 minutes (not timed with a stopwatch) |
| `POST /triage` through the ALB | HTTP 200 |
| Scale-out time | 69 s from alarm to the second task healthy |
| Scaling policy | CPU target 60%, min 1, max 4 |
| Peak task memory | 14.6% of 512 MB |
| Is 512 MB enough | Yes. No task was killed for memory. The 256 CPU / 512 MB size stands. |
| Network stack delete time | 5.1 minutes |
| Whole teardown run | 26 minutes |
| Live leftovers for `p3fg2` | 0 |
| Cost estimate | about USD 0.30 |

## Leftover check

- Both stacks reached `DELETE_COMPLETE`.
- The NAT gateway is deleted.
- No VPC endpoints remain.
- 0 load balancers remain.
- The ECS cluster is `INACTIVE`.

## Findings

- The first deploy (`p3fg1`) failed with `CannotPullContainerError`. The M1 Mac built an arm64 image, and Fargate here runs x86. The image was re-pushed as `linux/amd64`. Card #52 pins the platform.
- Teardown ran 26 minutes because stale ECS tag entries count as live in the leftover check. Cards #50 and #51 cover this.

## Commands run

a. `aws login --profile default-advanced`, then set `AWS_PROFILE`, `AWS_REGION=us-east-1`, `AWS_DEFAULT_REGION=us-east-1` and `BUDGET_EMAIL`
b. `make bootstrap`
c. `make mirror-base`
d. `make push-image`
e. `make ecs-fargate RUN_ID=p3fg2`
f. `curl` against the ALB DNS name: `/healthz`, `/readyz`, then `POST /triage`
g. Parallel `POST /triage` requests to drive scale-out
h. `make teardown RUN_ID=p3fg2`
