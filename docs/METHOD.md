# Method

How the numbers in the [README](../README.md) were produced, so you can judge whether the
comparison is fair. The full spec is [`SPEC.md`](SPEC.md). Run figures below come from the plan
config [`runs/default.json`](../runs/default.json) and the k6 scripts in
[`loadtests/`](../loadtests); `tests/test_method.py` checks that they still match.

### Matrix

Two questions hide in "ECS or EKS". One is the orchestrator: ECS or EKS. The other is the
compute: Fargate or EC2. Fargate is a compute backend that both orchestrators can use, not a
third orchestrator, so the matrix has 2 axes and four cells:

| | Fargate | EC2 |
|---|---|---|
| **ECS** | `ecs-fargate` | `ecs-ec2` (capacity provider and Auto Scaling group) |
| **EKS** | `eks-fargate` (Fargate profile) | `eks-ec2` (managed node group) |

Comparing cells in the same row isolates the compute effect. Comparing cells in the same column
isolates the orchestrator effect. Every cell runs the same container image from ECR.

### Equal capacity

One capacity block in the plan config applies to every cell:
0.25 vCPU (256 CPU units) and 512 MiB per replica, 1 to 4 replicas, CPU target 60%.

- ECS cells get it as CloudFormation parameters; EKS cells get the same values through the Helm
  chart (`resources`, `replicas`, `hpa.cpuTarget`).
- ECS scales with target tracking on average service CPU; EKS scales with a HorizontalPodAutoscaler
  on the same target.
- Hosts are equal too. Both EC2 cells run on the plan config's `t3.medium`: the deployer passes
  it to the ECS container instances and the EKS node group, and each cell's `config.json` records
  it. Fargate cells have no host to size.

### Workload

A FastAPI service wraps a small LangGraph agent that triages support tickets: classify, look up
an in-memory knowledge base, draft a reply. Load hits `POST /triage`.

The published runs use `LLM_MODE=mock`: a fake model that answers at once. So the numbers measure
the platform (network, load balancer, scheduler, container runtime), not model latency.
`LLM_MODE=bedrock` calls a real Bedrock model and measures end to end.

### Load profiles

k6 runs as a task in a separate harness cluster in the same VPC. The harness has a fixed size and
never scales, so it is not part of the measurement; the analyst fails a run when harness CPU
runs too high. Each profile sets the request rate, not the number of users. In a run, the load
runner passes the plan config's peak and duration:

| Profile | Shape | Answers |
|---|---|---|
| `steady` | 50 RPS for 600 s | baseline latency and error rate |
| `step` | 13, 25, 38 and 50 RPS, each held 150 s | latency per load band |
| `spike` | 5 RPS for 150 s, then 50 RPS within 1 s, held 450 s | scale out and errors under a burst |
| `scale-from-min` | 0 RPS for 150 s, then 50 RPS within 1 s, held 450 s | cold start from minimum replicas |

The step rates are 25, 50, 75 and 100 percent of the peak; the spike starts at 10 percent of it.
Run by hand with no environment set, the scripts default to 50 RPS for 60 s.

### Metrics

| Metric | Definition | Source file |
|---|---|---|
| p50, p95, p99 latency | percentiles of k6 request duration | `k6-<profile>.json` |
| Error rate | failed requests (non-2xx and timeouts) over all requests | `k6-<profile>.json` |
| Scale out | autoscaling alarm until the new replica is healthy | `events.json` (CloudWatch alarm history, ECS or Kubernetes events) |
| Cold start | task or pod scheduled until `/readyz` returns 200 | `events.json` |
| Deploy time | first stack create until the target is healthy behind the ALB | `deploy.json` |
| USD per thousand requests | load cost of the cell over successful requests | `cost.json` and `k6-<profile>.json` |
| Idle cost per hour | one hour at minimum replicas, no traffic, no LCUs, no logs | `cost.json` |
| Config lines | lines of CloudFormation plus Helm values for the cell | `config.json` |
| Moving parts | controllers and add-ons the cell needs | `config.json` |

The analyst computes them in plain Python, with no model in the loop. Each figure in
`metrics.json` names the raw file it came from, and a missing input is listed as a gap, never
filled in. The reporter may only state numbers found in `metrics.json`, and a test checks it.

### Cost model

- Cost is resource-seconds times the on-demand price, per cell. Prices come from the AWS Pricing
  API at plan time and are stored with the run. No Spot, Savings Plans or Graviton.
- Per cell: EKS control plane hours, Fargate vCPU and GB hours, EC2 instance hours, ALB hours,
  ALB LCU hours and CloudWatch ingest.
- Shared, on their own line and never spread across cells: the one NAT gateway (hours and data),
  the VPC endpoints, and the k6 harness.
- EC2 hours are priced at the plan config's `t3.medium`, the instance type both EC2 cells run on.
- The planner prints an estimate and waits for a yes before any spend.

**Blind spot.** The cost figures price the load window only. They leave out the time spent
deploying, waiting for health checks and tearing down, so real spend is higher: about two to
three times the estimate on the sample run. Cost Explorer shows the real figure about a day
later, under the `run-id` cost-allocation tag.

### Reproduce a run

You need an AWS account with an SSO profile, `uv`, Docker, `helm` and `kubectl`.

- a. Run the one-time setup from the README: `make bootstrap`, `make mirror-base`, then push
  the workload and k6 images.
- b. Edit `runs/default.json` if you want a different capacity, peak or duration.
- c. Run `make benchmark RUN_ID=<id>`. It prints the estimate and asks y/N before any spend.
- d. Read `results/<id>/metrics.json` and `results/<id>/report.md`. Teardown has already run.

### Known limits

- Mock workload: platform overhead only, no model latency.
- The sample run used the steady profile only, one region and one run, with no repeats and no
  error bars. Small gaps between cells may be noise.
- `eks-fargate` was not in the sample run. It also starts pods more slowly and has no DaemonSets,
  so Container Insights runs there as a sidecar.
- Scale out and cold start need an alarm and a new replica inside the load window. Under the
  steady profile most cells logged neither, so those figures are gaps for them.
- Config lines and moving parts count what this repo needs, not what any team would build.
