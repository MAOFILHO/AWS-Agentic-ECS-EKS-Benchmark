# QA report, issue #70, v1

Docs-only change: `docs/phase4-eks-ec2-check.md`. Checked against the owner evidence comment on issue #70 and the pasted teardown output.

| AC | Result | Proof |
|---|---|---|
| Stack statuses and create time | pass | Results table: 3 stacks `CREATE_COMPLETE`, about 18 min, rerun 1 min 23 s |
| HTTP 200 from `POST /triage` | pass | Results table row, matches evidence comment |
| Scale-out time | pass | 22 s, HPA decision 21:56:01 to 21:56:23 UTC; says pods only, no node added |
| Peak memory, 512 MiB | pass | 75 MiB idle = 14.6% (75/512 = 0.1465); peak not captured, "Probably" |
| Delete time, 0 leftovers | pass | 10 min 34 s, matches `time make teardown` output; 0 leftovers |
| Comparison table | pass | Fargate, ECS on EC2, EKS columns; ECS values match the two phase 3 docs |
| PR `needs-you:` line | pass | 8 commands in order, matches doc "Commands run" a to h |
| No account id or email | pass | grep of added lines: no 12-digit number, no email |

Local tests: git diff origin/main...HEAD | grep -E '^\+' | grep -nE '(^|[^0-9])[0-9]{12}([^0-9]|$)'
