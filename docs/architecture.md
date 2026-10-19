# Architecture

Three views of the system. Decisions behind them are in `docs/adr/`. Scope and metrics are in `docs/SPEC.md`.

## 1. Deploy flow

Stacks are created in layer order and deleted in reverse. EKS cells add a Helm release on top of their stack.

```mermaid
flowchart TD
    dev["Laptop<br/>SSO profile"] --> boot["00-bootstrap<br/>OIDC role<br/>budget, ECR<br/>cost tags"]
    ci["GitHub<br/>Actions<br/>OIDC"] --> img["Build image<br/>once per<br/>commit"]
    boot --> img
    img --> ecr[("ECR<br/>private repo")]
    boot --> net["10-network<br/>VPC, subnets<br/>NAT<br/>endpoints"]
    net --> harness["20-harness<br/>k6 ECS<br/>cluster"]
    net --> ecs["30-ecs<br/>cluster<br/>service, ALB<br/>autoscaling"]
    net --> eks["40-eks<br/>cluster<br/>add-ons<br/>node group<br/>or Fargate<br/>profile"]
    eks --> lbc["41-eks-lbc-iam<br/>Pod Identity<br/>role"]
    lbc --> helm["Helm<br/>workload<br/>chart<br/>Ingress, HPA"]
    ecr --> ecs
    ecr --> helm
    ecs --> ready{"Healthy<br/>behind<br/>ALB?"}
    helm --> ready
    ready -->|yes| load["Load test"]
    ready -->|no| down["Teardown"]
    load --> down
    down --> gone["1. Helm<br/>releases<br/>2. LBs<br/>3. stacks in<br/>reverse"]
```

## 2. Agent graph

The advisor is a LangGraph graph with a Pydantic state and a SQLite checkpointer. Teardown runs on every path, including failures.

```mermaid
flowchart TD
    start(["advisor run"]) --> planner["planner<br/>run matrix<br/>cost estimate"]
    planner --> gate{"approval<br/>gate<br/>interrupt"}
    gate -->|no| stop(["Stop<br/>no spend"])
    gate -->|yes| deployer["deployer<br/>change sets<br/>Helm"]
    deployer --> loadr["load_runner<br/>k6 RunTask<br/>in harness"]
    loadr --> collector["collector<br/>CloudWatch<br/>ALB, events"]
    collector --> analyst["analyst<br/>deterministic<br/>metrics.json"]
    analyst --> reporter["reporter<br/>LLM narrative<br/>from<br/>metrics.json"]
    reporter --> check{"Every<br/>number in<br/>metrics.json?"}
    check -->|no| reporter
    check -->|yes| teardown["teardown<br/>verify<br/>nothing tagged<br/>remains"]
    deployer -. "failure" .-> teardown
    loadr -. "failure" .-> teardown
    collector -. "failure" .-> teardown
    analyst -. "failure" .-> teardown
    reporter -. "failure" .-> teardown
    teardown --> done(["Report<br/>actual spend"])
```

## 3. Benchmark data flow

Raw data is written once. Every figure in the report traces back to a raw file.

```mermaid
flowchart LR
    k6["k6<br/>summaries"] --> raw[("results/<br/>run-id/raw/")]
    cw["CloudWatch<br/>Container<br/>Insights"] --> raw
    alb["ALB<br/>metrics"] --> raw
    ev["ECS task<br/>and K8s pod<br/>events"] --> raw
    price["Pricing<br/>API"] --> cost["Cost<br/>model"]
    raw --> analyst["analyst"]
    cost --> analyst
    analyst --> metrics[("metrics.json")]
    metrics --> reporter["reporter"]
    reporter --> report["report.md<br/>report.html"]
    metrics --> web["web<br/>viewer"]
    ce["Cost<br/>Explorer<br/>next day"] --> rec["advisor<br/>reconcile"]
    metrics --> rec
```
