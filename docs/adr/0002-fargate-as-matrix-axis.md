# 0002. Fargate is a matrix axis, not a third platform

Status: proposed

## Context

Fargate runs under both ECS and EKS. Comparing "ECS on Fargate" with "EKS on EC2" mixes two
effects: the orchestrator and the compute backend. A reader cannot tell which one caused a difference.

## Decision

- The matrix has two axes: orchestrator (ECS, EKS) and compute (Fargate, EC2).
- Four cells: `ecs-fargate`, `ecs-ec2`, `eks-fargate`, `eks-ec2`.
- The default run is `ecs-fargate` vs `eks-ec2`. The full run covers all four cells.
- Every cell gets the same vCPU and memory per replica, the same min and max replicas and the
  same autoscaling target (CPU 60%).
- `ComputeType` is a parameter of `30-ecs.yaml` and `40-eks.yaml`, not a separate template.

## Consequences

- The report can state the orchestrator effect and the compute effect separately.
- A full run costs about twice a default run.
- EKS on Fargate has slower pod start and no DaemonSets. Container Insights uses a sidecar there.
  The report states these limits.
