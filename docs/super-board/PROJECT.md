# agentic-platform-advisor

Public portfolio repo. Answers one question with data: where to host an AI agent fleet on AWS, ECS or EKS, and what it costs. A LangGraph multi-agent system deploys both platforms, loads them with the same agent workload, measures latency, scale-out and cost, writes a decision report, then destroys everything. Full brief: `docs/kickoff-prompt.md`.

## Hard rules

| Rule | Detail |
|---|---|
| No console clicks | All AWS infra is CloudFormation YAML. Kubernetes objects are Helm charts. |
| No long-lived credentials | GitHub OIDC to an IAM role in CI. SSO profiles locally. NEVER create IAM access keys or use the root user. |
| Cost guard | Budgets alarm in the bootstrap stack. `ttl-hours` tag on every stack. Teardown runs in a `finally` path. A scheduled workflow deletes expired stacks. |
| One image | One container image in ECR per commit. Platform differences live only in IaC and Helm values. |

## Layout

| Path | Holds |
|---|---|
| `infra/cfn/` | CloudFormation: bootstrap, network, harness, ECS, EKS |
| `infra/helm/workload/` | Deployment, Service, Ingress, HPA |
| `workload/` | FastAPI + LangGraph triage agent + Dockerfile |
| `advisor/` | Orchestrator graph, nodes, CLI (typer) |
| `loadtests/` | k6 scripts per profile |
| `runs/`, `results/` | Run configs; results (gitignored except one sample) |
| `web/` | React/TS report viewer |
| `tests/` | Unit, cfn-lint, cfn-guard, helm lint, smoke |

## Quality gates

`cfn-lint` and `cfn-guard` clean. Unit tests for analyst math and cost model (no AWS). Smoke test: `ecs-fargate` only, `LLM_MODE=mock`, 60 s load, under 25 min and under $2. A test proves the reporter never states a number missing from `metrics.json`.

## Board notes

Any card that deploys real AWS resources costs money and needs a human: add a `needs-you:` line to the PR body. `verify_commands` in the board config is empty until the first code lands; fill it then.
