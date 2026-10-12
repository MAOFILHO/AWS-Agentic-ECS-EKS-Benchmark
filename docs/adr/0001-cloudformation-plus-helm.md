# 0001. CloudFormation for AWS, Helm for Kubernetes

Status: proposed

## Context

Every AWS resource must be created without a console click and destroyed in a known order.
EKS cells also need Kubernetes objects (Deployment, Service, Ingress, HPA). The same workload
must run on ECS and EKS with differences only in IaC and Helm values.

## Decision

- All AWS resources are CloudFormation YAML in `infra/cfn/`, one stack per layer:
  bootstrap, network, harness, ECS, EKS, EKS load balancer controller IAM.
- The deployer uses boto3 change sets, so every change is previewed before it runs.
- Kubernetes objects are one Helm chart, `infra/helm/workload/`. Per-cell differences are values files.
- No Terraform, CDK or eksctl.

## Consequences

- Stack deletion order is explicit (`DependsOn`, reverse layer order), which designs out the
  "cluster has node groups attached" failure.
- `cfn-lint` and `cfn-guard` check every template in CI.
- Helm releases are deleted before their stack, so ALBs created by the controller do not block teardown.
- CloudFormation is more verbose than CDK. The config line count per cell is reported, not hidden.
