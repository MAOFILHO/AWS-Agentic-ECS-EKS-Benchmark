# 0005. GitHub OIDC and SSO, no access keys

Status: proposed

## Context

This is a public repo that creates and deletes paid AWS resources. A leaked long-lived key
could run up a large bill. The reference labs used root keys on an EC2 box; this repo must not.

## Decision

- CI uses GitHub OIDC to assume an IAM deploy role. The OIDC provider and the role live in the
  `00-bootstrap` stack.
- The role trust policy is limited to this repo and to named branches or environments.
- Local runs use AWS SSO profiles (`aws configure sso`).
- No IAM user access keys are created. The root user is never used.
- EKS workloads that call AWS use Pod Identity (for example, the load balancer controller in
  `41-eks-lbc-iam`).

## Consequences

- No AWS secret is stored in GitHub.
- The first `make bootstrap` runs from the owner's laptop with an SSO profile; CI can deploy only
  after that.
- The deploy role needs broad CloudFormation, ECS, EKS, EC2 and IAM rights. Its scope is reviewed
  with the bootstrap stack.
