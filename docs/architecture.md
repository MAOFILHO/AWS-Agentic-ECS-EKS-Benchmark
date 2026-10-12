# Architecture

Three views of the system. Decisions behind them are in `docs/adr/`. Scope and metrics are in `docs/SPEC.md`.

## 1. Deploy flow

Stacks are created in layer order and deleted in reverse. EKS cells add a Helm release on top of their stack.

```mermaid
flowchart TD
    dev["Laptop: SSO profile"] --> boot["00-bootstrap: OIDC role, budget, ECR, cost tags"]
    ci["GitHub Actions: OIDC"] --> img["Build image once per commit"]
    boot --> img
    img --> ecr[("ECR private repo")]
    boot --> net["10-network: VPC, subnets, NAT, endpoints"]
    net --> harness["20-harness: k6 ECS cluster"]
    net --> ecs["30-ecs: cluster, service, ALB, autoscaling"]
    net --> eks["40-eks: cluster, add-ons, node group or Fargate profile"]
    eks --> lbc["41-eks-lbc-iam: Pod Identity role"]
    lbc --> helm["Helm: workload chart with Ingress and HPA"]
    ecr --> ecs
    ecr --> helm
    ecs --> ready{"Healthy behind ALB?"}
    helm --> ready
    ready -->|yes| load["Load test"]
    ready -->|no| down["Teardown"]
    load --> down
    down --> gone["Helm releases, then LBs, then stacks in reverse"]
```

## 2. Agent graph

The advisor is a LangGraph graph with a Pydantic state and a SQLite checkpointer. Teardown runs on every path, including failures.

```mermaid
flowchart TD
    start(["advisor run"]) --> planner["planner: run matrix and cost estimate"]
    planner --> gate{"approval_gate: interrupt"}
    gate -->|no| stop(["Stop, no spend"])
    gate -->|yes| deployer["deployer: change sets, Helm"]
    deployer --> loadr["load_runner: k6 RunTask in harness"]
    loadr --> collector["collector: CloudWatch, ALB, events"]
    collector --> analyst["analyst: deterministic metrics.json"]
    analyst --> reporter["reporter: LLM narrative from metrics.json"]
    reporter --> check{"Every number in metrics.json?"}
    check -->|no| reporter
    check -->|yes| teardown["teardown: verify nothing tagged remains"]
    deployer -. "failure" .-> teardown
    loadr -. "failure" .-> teardown
    collector -. "failure" .-> teardown
    teardown --> done(["Report and actual spend"])
```

## 3. Benchmark data flow

Raw data is written once. Every figure in the report traces back to a raw file.

```mermaid
flowchart LR
    k6["k6 summaries"] --> raw[("results/run-id/raw/")]
    cw["CloudWatch and Container Insights"] --> raw
    alb["ALB metrics"] --> raw
    ev["ECS task and Kubernetes pod events"] --> raw
    price["Pricing API"] --> cost["Cost model"]
    raw --> analyst["analyst"]
    cost --> analyst
    analyst --> metrics[("metrics.json")]
    metrics --> reporter["reporter"]
    reporter --> report["report.md and report.html"]
    metrics --> web["web viewer"]
    ce["Cost Explorer, next day"] --> rec["advisor reconcile"]
    metrics --> rec
```
