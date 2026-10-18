# ECS vs EKS for Hosting an AI Agent Fleet

## Verdict
When choosing between ECS and EKS for hosting an AI agent fleet, the decision boils down to the specific needs of latency, cost, and management overhead. ECS on Fargate generally offers lower latency and more straightforward cost calculation but has higher idle costs compared to EKS on EC2. EKS on EC2 provides more control over the infrastructure but incurs higher idle costs and has longer deployment times.

## Comparison Table

### scale-from-min

| Metric                        | ecs-fargate | eks-ec2 | ecs-ec2 |
| ----------------------------- | ---------- | -------- | -------- |
| p50 latency (ms)             | 3.739      | 5.615    | 5.645    |
| p95 latency (ms)             | 4.004      | 10.46    | 8.588    |
| p99 latency (ms)             | 7.147      | 124.9    | 32.24    |
| error rate (ratio)           | 0.0        | 0.0      | 0.0      |
| config lines                | 436.0      | 534.0    | 436.0    |
| idle cost (USD/h)            | 0.03484    | 0.1641   | 0.0641   |
| cost per thousand requests (USD) | 0.0008543 | 0.001846 | 0.001078 |

### spike

| Metric                        | ecs-fargate | eks-ec2 |
| ----------------------------- | ---------- | -------- |
| p50 latency (ms)             | 3.773      | 6.016    |
| p95 latency (ms)             | 4.487      | 10.51    |
| p99 latency (ms)             | 9.982      | 71.2     |
| error rate (ratio)           | 0.0        | 0.0      |
| config lines                | 436.0      | 534.0    |
| idle cost (USD/h)            | 0.03484    | 0.1641   |
| cost per thousand requests (USD) | 0.0008543 | 0.001846 |

### steady

| Metric                        | ecs-fargate | eks-ec2 |
| ----------------------------- | ---------- | -------- |
| p50 latency (ms)             | 3.802      | 5.568    |
| p95 latency (ms)             | 4.116      | 11.07    |
| p99 latency (ms)             | 8.619      | 100.2    |
| error rate (ratio)           | 0.0        | 0.0      |
| config lines                | 436.0      | 534.0    |
| idle cost (USD/h)            | 0.03484    | 0.1641   |
| cost per thousand requests (USD) | 0.0008543 | 0.001846 |

### step

| Metric                        | ecs-fargate | eks-ec2 |
| ----------------------------- | ---------- | -------- |
| p50 latency (ms)             | 3.714      | 5.608    |
| p95 latency (ms)             | 4.418      | 11.01    |
| p99 latency (ms)             | 9.487      | 121.0    |
| error rate (ratio)           | 0.0        | 0.001106 |
| config lines                | 436.0      | 534.0    |
| idle cost (USD/h)            | 0.03484    | 0.1641   |
| cost per thousand requests (USD) | 0.0008543 | 0.001846 |

### Per cell

| Metric                        | ecs-fargate | eks-ec2 | ecs-ec2 |
| ----------------------------- | ---------- | -------- | -------- |
| deploy time (s)              | 433.4      | 441.6    | 441.6    |
| config lines                | 436.0      | 534.0    | 436.0    |
| idle cost (USD/h)            | 0.03484    | 0.1641   | 0.0641   |
| cost per thousand requests (USD) | 0.0008543 | 0.001846 | 0.001078 |

## Shared Costs
- shared nat gateway: 0.09778 USD
- shared nat data: 0.045 USD
- shared vpc endpoints: 0.10864 USD
- shared harness vcpu: 0.08796 USD
- shared harness memory: 0.01932 USD

## Trade-offs
- **ECS on Fargate**: Lower latency but higher idle costs.
- **EKS on EC2**: Higher latency but more control and lower idle costs.
- **ECS on EC2**: Similar to EKS on EC2 in terms of control and latency but with lower idle costs than EKS on EC2.

## Recommendation
Choose ECS on Fargate if low latency is a critical requirement and cost per request is more flexible. Opt for EKS on EC2 if you prefer more control over the infrastructure and are okay with higher latency and idle costs. ECS on EC2 may offer a middle ground between cost and control but may not be as performant as EKS on EC2.