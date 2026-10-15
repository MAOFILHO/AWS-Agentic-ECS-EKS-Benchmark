# Phase 4 EKS on EC2 check

Real-account deploy of the `eks-ec2` cell, run by the repo owner in `us-east-1` on 2026-10-15.
Run id `p4ek1`. Source: owner evidence comment on issue #70.

## Results

| Item | Result |
|---|---|
| Stack `p4ek1-network` | `CREATE_COMPLETE` |
| Stack `p4ek1-eks-ec2` | `CREATE_COMPLETE` |
| Stack `p4ek1-eks-lbc-iam` | `CREATE_COMPLETE` |
| Kubernetes version | 1.37 (read from SSM) |
| Load balancer controller chart | 3.6.0 |
| Node group `workload` | `ACTIVE`, 1 `t3.medium` node, 5 add-ons |
| Create time, three stacks | about 18 minutes, from the network change set to the third stack. Includes 3 prompts the owner answered and 2 failed attempts (see Findings). |
| Create time, clean rerun | 1 min 23 s. Network and `eks-ec2` showed no changes, so this is only the `eks-lbc-iam` update. It is not a full create. |
| Time from rerun to a healthy ALB target | about 90 s |
| `GET /healthz` through the ALB | HTTP 200 |
| `GET /readyz` through the ALB | HTTP 200 |
| `POST /triage` through the ALB | HTTP 200 (billing answer) |
| Scale-out time | 22 s from load start (21:56:01 UTC) to the HPA deciding 2 pods (21:56:23 UTC) |
| Node count after scale-out | 1. Both pods fit on the one node, so no node was added. |
| Scale-in | 22:02:23 UTC, 5 minutes after the load ended |
| Scaling policy | CPU target 60%, min 1, max 4 |
| Pod memory | 75 MiB at idle. Peak under load not captured (Container Insights is off). |
| Is 512 MiB enough | Probably. 75 MiB is 14.6% of the 512 MiB limit, the same share the ECS cells showed. Not proven under load on EKS. |
| Teardown time | 10 min 34 s |
| Live leftovers for `p4ek1` | 0 |
| Cost estimate | about USD 1.2 for the run, about USD 0.30 per hour for about 4 hours |

The scale-out time is the HPA decision, not a pod passing its readiness check.

## Leftover check

- Helm release `workload` and Helm release `aws-load-balancer-controller` were uninstalled.
- The teardown reported `no ALBs left`.
- All three stacks were deleted, newest first.
- No stacks and no EKS cluster remain.
- The NAT gateway is deleted.
- No VPC endpoints remain.
- 0 load balancers remain.
- 0 Kubernetes security groups remain.

## ECS and EKS compared

ECS numbers come from `docs/phase3-fargate-check.md` (run `p3fg2`) and `docs/phase3-ec2-check.md` (run `p3ec2`).

| Item | Fargate (`p3fg2`) | ECS on EC2 (`p3ec2`) | EKS on EC2 (`p4ek1`) |
|---|---|---|---|
| Create time | about 5 minutes for the ECS stack | about 8 minutes for both stacks | about 18 minutes for three stacks, with 2 failed attempts. Not like for like. |
| Scale-out time | 69 s from alarm to the second task healthy | not measured | 22 s to the HPA decision. Pod readiness not timed. |
| Peak memory | 14.6% of 512 MB | 14.6% of 512 MB | 75 MiB at idle (14.6% of 512 MiB). Peak under load not captured. |
| Is 512 enough | Yes | Yes | Probably, not proven under load |
| Teardown time | 26 minutes | 14 min 17 s | 10 min 34 s |
| Live leftovers | 0 | 0 | 0 |
| Cost per hour | about USD 0.20 | about USD 0.20 | about USD 0.30 |
| Cost of the run | about USD 0.30 | about USD 0.30 | about USD 1.2 |

## Findings

- The first deploy gave no ALB. The role boundary lacked `eks-auth:AssumeRoleForPodIdentity`, so the controller could not get credentials. Card #75 (PR 76) fixed it, then `make bootstrap` was run again.
- The second deploy still gave no ALB. The controller role could not edit the node security group rules. Card #77 (PR 78) fixed it, then `make eks-ec2` was run again.
- The ALB stopped accepting outside traffic during load. Requests reached it only in the first 2 minutes (3,198 and 1,303 requests in the two bursts), then 0. The owner's `curl` also timed out afterwards. This happened in 2 of 2 heavy bursts, on two different ALB setups. It is the second sighting of the ECS finding in card #64. Cause unknown, probably outside the stack.
- Scale-out is proven for pods (1 to 2) but not for nodes. The 2 pods fit on the single `t3.medium` node.
- The cost rate is higher than ECS because of the EKS control plane. The run cost was inflated by holding the stack up while 2 fixes landed.

## Commands run

a. `aws login --profile default-advanced`, then set `AWS_PROFILE`, `AWS_REGION=us-east-1`, `AWS_DEFAULT_REGION=us-east-1`, `BUDGET_EMAIL` and `OIDC_PROVIDER_ARN`
b. Install Helm with `brew`
c. `make bootstrap`, run again after the card #75 fix merged
d. `make push-image`
e. `make eks-ec2 RUN_ID=p4ek1`, run twice (the second run after the card #77 fix)
f. `curl` against the ALB DNS name: `/healthz`, `/readyz`, then `POST /triage`
g. Parallel `POST /triage` requests with `xargs` and `curl` to drive scale-out
h. `make teardown RUN_ID=p4ek1`
