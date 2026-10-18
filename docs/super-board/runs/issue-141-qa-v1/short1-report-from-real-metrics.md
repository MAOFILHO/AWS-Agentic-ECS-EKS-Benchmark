{'status': 'reported'}
# ECS vs EKS: short1

## Verdict
ECS Fargate is cheaper than EKS with EC2, with no errors in any cell.

## Comparison table

### spike
| Metric | ecs-fargate | ecs-ec2 | eks-ec2 |
|---|---|---|---|
| P50 latency (ms) | 4.078 | 5406 | 5.787 |
| P95 latency (ms) | 4.832 | 6077 | 3862 |
| P99 latency (ms) | 10.36 | 7174 | 6051 |
| Error rate | 0 | 0 | 0 |
| Scale-out (s) | 72.77 | 76.55 | 23 |
| Cold start (s) | 66.02 | 66.04 | 23 |

### scale-from-min
| Metric | ecs-fargate | ecs-ec2 | eks-ec2 |
|---|---|---|---|
| P50 latency (ms) | 4.091 | 5.479 | 5.351 |
| P95 latency (ms) | 4.831 | 6.549 | 8.227 |
| P99 latency (ms) | 9.301 | 9.982 | 16.09 |
| Error rate | 0 | 0 | 0 |
| Scale-out (s) | n/a | 148.3 | n/a |
| Cold start (s) | n/a | 144.3 | n/a |

Notes:
- ecs-fargate/scale-from-min scale_out_s, cold_start_s: no scale-out happened in its load window
- eks-ec2/scale-from-min scale_out_s, cold_start_s: no scale-out happened in its load window

### Per cell
| Metric | ecs-fargate | ecs-ec2 | eks-ec2 |
|---|---|---|---|
| Deploy time (s) | 426.4 | 459.7 | 1125 |
| Config lines | 436 | 436 | 534 |
| Moving parts | 4 | 4 | 4 |
| Idle cost (USD/h) | 0.03484 | 0.0641 | 0.1641 |
| Cost per 1k requests (USD) | 0.000895 | 0.001094 | 0.001537 |

## Shared costs
Not part of any cell.

| Item | USD |
|---|---|
| shared nat gateway | 0.04893 |
| shared nat data | 0.045 |
| shared vpc endpoints | 0.05437 |
| shared harness vcpu | 0.04402 |
| shared harness memory | 0.009666 |

