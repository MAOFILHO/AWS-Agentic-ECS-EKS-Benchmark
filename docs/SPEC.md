# SPEC: agentic-platform-advisor

Status: draft for owner review (Phase 0). No code is written until this is approved.

## 1. Question

Where should we host an AI agent fleet on AWS, ECS or EKS, and what does it cost?

The repo answers with data. One command deploys each platform, loads it with the same agent
workload, measures latency, scale-out and cost, writes a decision report, then destroys everything.

## 2. Goals

- G1. Compare ECS and EKS on the same image, the same workload and the same capacity.
- G2. Separate the orchestrator effect (ECS vs EKS) from the compute effect (Fargate vs EC2).
- G3. Report load cost, idle cost per hour, and $ per 1k requests per cell.
- G4. Every number in the report traces back to a raw file of the run.
- G5. One command runs the full cycle: plan, approve, deploy, load, collect, analyse, report, tear down.
- G6. Zero console clicks and no long-lived credentials.
- G7. A smoke run finishes in under 35 min and under $2.

## 3. Non-goals

- Not a general Kubernetes vs ECS feature comparison. Only what the workload exercises.
- Not a model benchmark. `LLM_MODE=bedrock` measures end to end; model quality is out of scope.
- No Spot, Graviton or Savings Plans pricing in v1. On-demand only.
- No multi-region runs. Region is a parameter; one region per run.
- No service mesh, no GPU nodes, no Karpenter in v1.
- Azure (ACA, AKS) is Phase 7. v1 keeps a stable provider interface but ships AWS only.

## 4. Benchmark matrix

Fargate is a compute backend, not an orchestrator. The matrix has two axes:

| | Fargate | EC2 (managed capacity) |
|---|---|---|
| **ECS** | `ecs-fargate` | `ecs-ec2` (capacity provider + ASG) |
| **EKS** | `eks-fargate` (Fargate profile) | `eks-ec2` (managed node group) |

- Default run: `ecs-fargate` vs `eks-ec2`, the choice most teams face.
- Full run: all four cells. `eks-fargate` runs only in the full run (Decision 8).
- Equal capacity in every cell: same vCPU and memory per replica (the lowest-cost SKU that fits the workload, Decision 2), same min and max replicas,
  same autoscaling target (CPU 60%).
- Known limits, documented in the report: EKS on Fargate has slower pod start and no
  DaemonSets, so Container Insights runs as a sidecar (ADOT) there.

## 5. Workload under test

`workload/`: FastAPI service around a small LangGraph support-ticket triage agent
(classify, retrieve from an in-memory KB, draft a reply).

| Item | Value |
|---|---|
| Endpoints | `POST /triage`, `GET /healthz`, `GET /readyz` |
| `LLM_MODE=mock` | fixed-latency fake LLM, default 0 ms. Measures platform overhead only. |
| `LLM_MODE=bedrock` | real Bedrock call, model id read from `BEDROCK_MODEL_ID` (Decision 3). Measures end to end. |
| Telemetry | OpenTelemetry traces, Prometheus-style metrics, request id in every log line. Agent traces go to Langfuse (Decision 4). |
| Image | one image in ECR per commit, used by every cell |

## 6. Load profiles

k6 runs as an ECS RunTask in a separate harness cluster in the same VPC. The harness has a
fixed size and never autoscales, so it is not part of the measurement.

| Profile | Shape | Answers |
|---|---|---|
| `steady` | constant RPS | baseline latency and error rate |
| `step` | RPS rises in steps | latency per load band |
| `spike` | sudden jump to peak RPS | scale-out time, errors under burst |
| `scale-from-min` | idle at min replicas, then load | cold start and first-replica time |

## 7. Metric definitions

| Metric | Definition | Source |
|---|---|---|
| p50 / p95 / p99 latency | percentiles of k6 request duration, per cell and profile | k6 summary |
| Error rate | non-2xx plus timeouts, divided by total requests | k6 summary |
| Scale-out time | autoscaling alarm time until the new replica passes its health check | CloudWatch alarm history, ECS events, Kubernetes events |
| Cold start | task or pod scheduled until `/readyz` returns 200 | ECS task events, pod conditions |
| Deploy time | first stack create until the service is healthy behind the ALB | CloudFormation events, ALB target health |
| $ per 1k requests | load cost of the cell divided by successful requests, times 1000 | cost model, k6 summary |
| Idle $ per hour | cost per hour at min replicas with no traffic | cost model |
| Config lines | lines of CloudFormation plus Helm values for the cell | repo files |
| Moving parts | count of controllers and add-ons the cell needs | deploy record |

The analyst computes all of these in deterministic Python (ADR 0004). Each figure in
`metrics.json` names the raw file it came from.

## 8. Cost model

- Per-run cost = sum over resources of (resource-seconds x on-demand price), per cell.
- Prices come from the AWS Pricing API at plan time and are stored in the run record.
- Included: EKS control plane hours, Fargate vCPU and GB seconds, EC2 instance seconds,
  ALB LCU hours, NAT gateway hours and GB, CloudWatch ingest.
- The planner shows an estimate before any spend. The approval gate stops until a yes,
  or until `--auto-approve-under <usd>` allows it in CI.
- Cost Explorer lags about 24 h, so it is a next-day check only: `advisor reconcile <run-id>`,
  using the cost-allocation tag `run-id`.
- Shared costs (NAT gateway, harness cluster, VPC endpoints) are reported on their own line,
  not spread across cells. One NAT gateway per run (Decision 6).

## 9. Architecture summary

Diagrams live in `docs/architecture.md`.

- Infra: CloudFormation stacks `00-bootstrap`, `10-network`, `20-harness`, `30-ecs`, `40-eks`,
  `41-eks-lbc-iam` (ADR 0001).
- Kubernetes objects: one Helm chart, `infra/helm/workload/` (Deployment, Service, Ingress, HPA).
- Orchestrator: LangGraph graph in `advisor/` with nodes planner, approval_gate, deployer,
  load_runner, collector, analyst, reporter, teardown, and a SQLite checkpointer.
- Access: GitHub OIDC role in CI, SSO profiles on a laptop (ADR 0005).

## 10. Safety rules

- All AWS resources are CloudFormation. All Kubernetes objects are Helm. No console step.
- No IAM access keys and no root user.
- Bootstrap stack holds an AWS Budgets alarm: USD 15.00 per month, alert email from the `BudgetEmail` parameter (Decision 1).
- Every benchmark stack has a `ttl-hours` tag, default 4 (Decision 5). Teardown runs in a `finally` path.
- A scheduled janitor workflow deletes any stack past its TTL.
- Kubernetes version and EKS AMI are resolved at deploy time from SSM public parameters and
  pinned only in the run record.
- Control-plane subnets exclude AZ `us-east-1e`.
- All images come from ECR, including mirrored base images, to avoid Docker Hub rate limits.

## 11. Quality gates

- `cfn-lint` and `cfn-guard` clean on all templates; `helm lint` clean.
- Unit tests for analyst math and the cost model, with fixtures and no AWS.
- A test proves the reporter never states a number missing from `metrics.json`.
- Smoke test: `ecs-fargate` only, `LLM_MODE=mock`, 60 s steady load, teardown, under 35 min and under $2.

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Teardown fails and stacks keep billing | money | `finally` teardown, tag sweep check, TTL janitor, budget alarm |
| EKS delete blocked by node groups or LBs | stuck stacks | `DependsOn` order; delete Helm releases and LBs first |
| Stale kubeconfig after cluster delete | wrong cluster targeted | new kubeconfig per run in a temp dir |
| Harness becomes the bottleneck | wrong latency numbers | fixed-size harness; record harness CPU; fail the run above a threshold |
| Uneven capacity between cells | unfair result | one capacity block in the run config, applied to every cell |
| Pricing API gaps for a resource | cost under-reported | report the gap in `metrics.json`; reconcile next day |
| Reporter invents a number | false claims | numeric check against `metrics.json`; its own test |
| Bedrock throttling in `bedrock` mode | latency reflects quota, not platform | default runs use `mock`; record throttle counts |
| Docker Hub rate limits | failed deploys | mirror base images into ECR |

## 13. Decisions

The owner answered the eight open questions. Each answer is final for v1.

1. **Budget alarm.** USD 15.00 per month. The alert email is the CloudFormation parameter `BudgetEmail`. The address is never written into a committed file, because the repo is public.
2. **Replica size.** The lowest-cost SKU that fits the workload, the same in every cell. Phase 3 and 4 cards set the exact SKU.
3. **Bedrock model.** The lowest-cost text model available in `us-east-1`, read from `BEDROCK_MODEL_ID`. Model access is confirmed for `amazon.nova-micro-v1:0` in `us-east-1` (see `docs/bedrock-check.md`).
4. **Tracing.** Langfuse. The owner supplies the keys through the local env file and GitHub secrets. No key is committed.
5. **`ttl-hours`.** 4.
6. **NAT gateway.** One per run (lowest cost).
7. **Sample run.** `us-east-1`, in the owner's own AWS account (SSO profile).
8. **Default run.** `ecs-fargate` vs `eks-ec2`. `eks-fargate` runs only in the full run.
