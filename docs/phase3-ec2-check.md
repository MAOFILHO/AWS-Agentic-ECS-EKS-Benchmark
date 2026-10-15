# Phase 3 EC2 check

Real-account deploy of the `ecs-ec2` cell, run by the repo owner in `us-east-1` on 2026-10-14 and 2026-10-15.
Two runs: `p3ec1` (failed) and `p3ec2` (passed). Source: owner evidence comment on issue #58.

## Results

| Item | Run `p3ec2` |
|---|---|
| Stack `p3ec2-network` | `CREATE_COMPLETE` |
| Stack `p3ec2-ecs-ec2` | `CREATE_COMPLETE` |
| Create time, both stacks | 8 min 13 s (about 8 minutes). Includes 2 prompts the owner answered. |
| EC2 instances registered with ECS | 1 |
| Tasks running | 1 |
| `GET /healthz` through the ALB | HTTP 200 |
| `GET /readyz` through the ALB | HTTP 200 |
| `POST /triage` through the ALB | HTTP 200 (billing answer) |
| ALB target | healthy |
| Scale-out time | not measured (see below) |
| Peak task memory | 14.6% of 512 MB |
| Is 512 MB enough | Yes. No task was killed for memory. |
| Instance CPU | peak 86%, average 43 to 45% over 2 minutes |
| Teardown time | 14 min 17 s |
| Live leftovers for `p3ec2` | 0 |
| Cost estimate | about USD 0.30 for `p3ec2` (about 80 minutes). Both runs about USD 0.70. |

## Why scale-out was not measured

- Load test 1 sent about 4,400 requests through the ALB over 3 minutes.
- CPU never held above the 60% target for 3 minutes. The alarm stayed `OK` and the task count stayed 1.
- After 20:32 EDT the ALB took no outside connections. The owner's laptop and the orchestrator both timed out for 26 minutes (3 load runs, 10 retries).
- From inside AWS the ALB was active and the target was healthy. Security group, NACL, routes and DNS showed no fault.
- Cause unknown. This is an open finding and has its own bug card. Neither the task-count rise nor the instance-count rise has been seen on EC2 yet.

## Leftover check

- Both stacks reached `DELETE_COMPLETE`.
- The NAT gateway is deleted.
- No VPC endpoints remain.
- 0 load balancers remain.
- 0 auto scaling groups remain.
- All EC2 instances are terminated.

## Fargate and EC2 compared

Fargate numbers come from `docs/phase3-fargate-check.md` (run `p3fg2`).

| Item | Fargate (`p3fg2`) | EC2 (`p3ec2`) |
|---|---|---|
| Create time | about 5 minutes for the ECS stack | about 8 minutes for both stacks (not like for like) |
| Scale-out time | 69 s from alarm to the second task healthy | not measured, ALB stopped taking outside traffic |
| Peak task memory | 14.6% of 512 MB | 14.6% of 512 MB |
| Is 512 MB enough | Yes | Yes |
| Teardown time | 26 minutes | 14 min 17 s |
| Live leftovers | 0 | 0 |
| Cost per hour | about USD 0.20 | about USD 0.20 |
| Cost of the run | about USD 0.30 | about USD 0.30 |

## Findings

- The first deploy (`p3ec1`) failed. 3 EC2 instances ran for 75 minutes and 0 registered with ECS. The role boundary had no `ecs:` actions. Card #60 (PR 63) fixed it, then `make bootstrap` was run again. The failed run cost about USD 0.40.
- A coverage test now guards the boundary against missing ECS agent actions.
- Teardown dropped from 26 to 14 minutes after cards #50, #51 and #61.
- Open: the ALB stopped accepting outside connections after 26 minutes of load. Scale-out on EC2 (tasks and instances) is not proven.

## Commands run

a. `aws login --profile default-advanced`, then set `AWS_PROFILE`, `AWS_REGION=us-east-1`, `AWS_DEFAULT_REGION=us-east-1`, `BUDGET_EMAIL` and `OIDC_PROVIDER_ARN`
b. `make bootstrap`
c. `make mirror-base`
d. `make push-image`
e. `make ecs-ec2 RUN_ID=p3ec1` (failed), then `make bootstrap`, then `make ecs-ec2 RUN_ID=p3ec2`
f. `curl` against the ALB DNS name: `/healthz`, `/readyz`, then `POST /triage`
g. Parallel `POST /triage` requests to drive scale-out
h. `make teardown RUN_ID=p3ec1` and `make teardown RUN_ID=p3ec2`
