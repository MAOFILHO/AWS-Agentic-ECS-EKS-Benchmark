# Claude Code prompt — Agentic Platform Advisor (ECS vs EKS benchmark)

Paste everything below the line into a fresh Claude Code session at the root of an empty repo.

---

## Role and goal

You are the lead engineer for a new public portfolio repo: **agentic-platform-advisor**.

The repo answers one buyer question with data: *"Where should we host an AI agent fleet on AWS — ECS or EKS — and what does it cost?"*

A LangGraph multi-agent system deploys both platforms, loads them with the same agent workload, measures latency, scale-out and cost, writes a decision report, and destroys everything. One command runs the full cycle.

## Hard rules (do not break)

1. **Zero console clicks.** All AWS infrastructure is CloudFormation (YAML). Kubernetes objects are Helm charts applied by the deployer agent or by a GitHub Actions job. No manual step in any AWS console, ever. If a step seems to need the console, stop and ask me.
2. **No long-lived credentials.** GitHub Actions uses GitHub OIDC → IAM role. Local runs use AWS SSO / `aws configure sso` profiles. Never create IAM access keys. Never use the root user.
3. **Cost guard on every run.** AWS Budgets alarm in the bootstrap stack. Every benchmark stack gets a `ttl-hours` tag. The teardown agent runs in a `finally` path, even after failures. A scheduled workflow deletes any stack older than its TTL.
4. **Same image, same code, both platforms.** One container image in ECR, built once per commit. Platform differences live only in IaC and Helm values.
5. **No hardcoded versions that drift.** Resolve the current EKS Kubernetes version and EKS-optimized AMI at deploy time (SSM public parameters / `aws eks describe-cluster-versions`). Pin only in the run record.
6. **Python 3.12** for agents and workload (LangGraph, LangChain, boto3). **React + TypeScript** (Vite) for the report viewer. `uv` for Python deps.
7. Region is a parameter. Default `us-east-1`. Exclude AZ `us-east-1e` from control-plane subnets (EKS does not support it).

## Reference material

Three console-based lab guides were used as reference. They are copyrighted: **do not copy them into the repo**. Translate their resources into CloudFormation:

| Lab | What to translate |
|---|---|
| EKS cluster lab | Cluster IAM role; node role with `AmazonEKSWorkerNodePolicy`, `AmazonEKS_CNI_Policy`, `AmazonEC2ContainerRegistryReadOnly`; managed node group (AL2023, t3.medium baseline); add-ons vpc-cni, kube-proxy, coredns, metrics-server, eks-pod-identity-agent; access entries (API auth mode, not aws-auth ConfigMap). |
| ECR lab | Private repo, immutable tags, scan on push, lifecycle policy (keep last 10). Build + push from GitHub Actions via OIDC — not from an EC2 box with root keys. |
| ECS Fargate lab | Cluster, task definition (awsvpc), service, security group. Upgrade: ALB in front, private subnets, no public task IPs, CloudWatch logs on. |

Known failure modes from those labs — design them out:
- Docker Hub pull rate limits → all images come from ECR (mirror base images if needed).
- "Cluster has node groups attached" on delete → CloudFormation `DependsOn` order; teardown deletes Helm releases and load balancers first.
- Stale kubeconfig after cluster delete → deployer regenerates kubeconfig per run in a temp dir.

## Benchmark matrix

Fargate is a **compute backend**, not an orchestrator. The matrix has two axes:

| | Fargate | EC2 (managed capacity) |
|---|---|---|
| **ECS** | `ecs-fargate` | `ecs-ec2` (capacity provider + ASG) |
| **EKS** | `eks-fargate` (Fargate profile) | `eks-ec2` (managed node group) |

- Default run: `ecs-fargate` vs `eks-ec2` (the real-world choice most teams face).
- Full run: all four cells, so the report can separate "orchestrator effect" from "compute effect".
- Equalize capacity: same vCPU/memory per replica, same min/max replicas, same autoscaling target (CPU 60%).
- Known limits to document, not hide: EKS on Fargate has slower pod start and no DaemonSets (Container Insights uses a sidecar/ADOT pattern there).

## Workload under test

`workload/` — FastAPI service wrapping a small LangGraph **support-ticket triage agent** (classify → retrieve from a tiny in-memory KB → draft reply).

Two modes via env var `LLM_MODE`:
- `mock` — fixed-latency fake LLM (configurable, default 0 ms). Measures **platform overhead only**.
- `bedrock` — real Bedrock call (model id is a parameter; default a small, cheap model). Measures **end-to-end**.

Endpoints: `POST /triage`, `GET /healthz`, `GET /readyz`. Emits OpenTelemetry traces and Prometheus-style metrics. Request IDs flow through logs.

## Orchestrator (the agentic part)

`advisor/` — LangGraph graph with a typed state (Pydantic) and a checkpointer (SQLite locally).

| Node | Job | Output the user sees |
|---|---|---|
| `planner` | Builds the run matrix from CLI args / `runs/*.yaml`. Estimates cost with the Pricing API. | A plan table with estimated $ and minutes. |
| `approval_gate` | LangGraph `interrupt()`. Shows the estimate. CI mode: `--auto-approve-under <usd>`. | "Approve? (y/n)" — no spend before yes. |
| `deployer` | Deploys stacks via boto3 CloudFormation (change sets), then Helm for EKS cells. Records deploy duration. | Stack status stream; deploy minutes per cell. |
| `load_runner` | Starts k6 as an ECS RunTask in a separate **harness** cluster in the same VPC (fixed size, never autoscaled). Profiles: steady, step, spike, scale-from-min. | Live RPS / p95 per cell. |
| `collector` | Pulls CloudWatch / Container Insights metrics, ALB metrics, task/pod start events. Writes raw data to `results/<run-id>/raw/`. | Raw CSV/Parquet files. |
| `analyst` | Computes p50/p95/p99, error rate, scale-out time (trigger → new replica healthy), cold start, $ per 1k requests, idle $/hour. Deterministic Python — the LLM never invents numbers. | `metrics.json` with every figure traceable to a raw file. |
| `reporter` | LLM writes the narrative and a recommendation by team size and traffic band, **only from `metrics.json`**. A check rejects any number not present in `metrics.json`. | `report.md` + `report.html`. |
| `teardown` | Deletes Helm releases → LBs → stacks in reverse order. Verifies nothing tagged with the run id remains. | "All resources destroyed. Actual spend: $X." |

Retries with backoff on deployer/collector. Every node logs to LangSmith or Langfuse (pick one, make it a config flag; default Langfuse self-host off, LangSmith if key present).

## Cost model

- Per-run cost = Σ(resource-seconds × on-demand price from the Pricing API), per cell. Includes: EKS control plane hours, Fargate vCPU/GB-seconds, EC2 instance-seconds, ALB LCU-hours, NAT gateway hours + GB, CloudWatch ingest.
- Cost Explorer lags ~24 h, so it is a **next-day reconciliation** job only (`advisor reconcile <run-id>`), using cost-allocation tag `run-id`.
- Report both **load cost** and **idle cost per hour** — idle cost is where ECS vs EKS differs most for small fleets.

## Operational metrics (also in the report)

- Deploy time from zero to healthy (minutes).
- IaC + Helm lines of config per cell.
- Number of moving parts (controllers, add-ons) per cell.

## Repo layout

```
agentic-platform-advisor/
├── README.md                 # headline chart + verdict at the top
├── Makefile                  # make bootstrap | benchmark | teardown | report
├── docs/
│   ├── SPEC.md
│   ├── architecture.md       # Mermaid diagrams
│   └── adr/                  # one ADR per decision
├── infra/cfn/
│   ├── 00-bootstrap.yaml     # OIDC provider, deploy role, budget, ECR, cost tags
│   ├── 10-network.yaml       # VPC, 2-3 AZ private/public subnets, NAT, endpoints (ECR, S3, logs, STS)
│   ├── 20-harness.yaml       # k6 ECS cluster + task def
│   ├── 30-ecs.yaml           # cluster, task def, service, ALB, autoscaling; param ComputeType=FARGATE|EC2
│   ├── 40-eks.yaml           # cluster, add-ons, access entries, node group OR Fargate profile; param ComputeType
│   └── 41-eks-lbc-iam.yaml   # Pod Identity role for AWS Load Balancer Controller
├── infra/helm/workload/      # Deployment, Service, Ingress (ALB), HPA
├── workload/                 # FastAPI + LangGraph triage agent + Dockerfile
├── advisor/                  # orchestrator graph, nodes, tools, CLI (typer)
├── loadtests/                # k6 scripts per profile
├── runs/                     # run configs (yaml)
├── results/                  # gitignored except published sample run
├── web/                      # React/TS report viewer (static, reads results JSON)
├── tests/                    # unit + cfn-lint + cfn-guard + helm lint + smoke
└── .github/workflows/
    ├── ci.yml                # lint, tests, cfn-lint, cfn-guard, build image
    ├── benchmark.yml         # workflow_dispatch, OIDC, runs advisor in CI mode
    └── janitor.yml           # schedule: delete stacks past TTL
```

## Quality gates

- `cfn-lint` and `cfn-guard` clean on all templates.
- Unit tests for analyst math and cost model (fixtures, no AWS).
- Smoke test: deploy `ecs-fargate` only with `LLM_MODE=mock`, 60 s steady load, teardown. Must finish < 25 min and < $2.
- The reporter's "no invented numbers" check has its own test.

## Phases and acceptance criteria

| Phase | Done when |
|---|---|
| 0. Spec | `docs/SPEC.md`, ADRs, architecture diagram written. **Stop and wait for my review.** |
| 1. Skeleton | Repo layout, Makefile, CI green, workload runs locally in Docker in both LLM modes. |
| 2. Bootstrap + network | `make bootstrap` creates OIDC role, budget, ECR, VPC from my laptop with one command. |
| 3. ECS cells | Both ECS cells deploy, serve `/triage` behind ALB, autoscale, tear down clean. |
| 4. EKS cells | Both EKS cells deploy via CFN + Helm, same checks pass. |
| 5. Agents | Full LangGraph run with approval gate, collector, analyst, reporter, teardown. |
| 6. Report + README | Sample run committed to `results/sample/`; README shows chart + verdict + "cost of this run". |
| 7 (later). Azure | Provider interface stays stable; ACA + AKS via Bicep added without touching agent code. |

## Your first action

Do **only Phase 0** now:
1. Write `docs/SPEC.md` from this prompt — goals, non-goals, matrix, metrics definitions, cost model, risks.
2. Write ADRs: `0001-cloudformation-plus-helm.md`, `0002-fargate-as-matrix-axis.md`, `0003-mock-llm-mode.md`, `0004-deterministic-analyst-llm-reporter.md`, `0005-oidc-no-access-keys.md`.
3. Write `docs/architecture.md` with Mermaid diagrams (deploy flow, agent graph, benchmark data flow).
4. List open questions for me at the end of SPEC.md.

Then stop. Do not scaffold code until I approve the spec.
