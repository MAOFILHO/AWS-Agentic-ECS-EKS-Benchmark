# QA v1 · issue 22 · head e12ff9c

Template-only card, no UI: no screenshots.

| AC | Result | Proof |
|---|---|---|
| cfn-lint exits 0 | pass | `uvx cfn-lint==1.57.2 infra/cfn/00-bootstrap.yaml` exit 0; `infra/cfn/*.yaml` exit 0 |
| `BudgetEmail` has no `Default` | pass | parsed template: no `Default` key (`00-bootstrap.yaml:5-9`) |
| 15 USD monthly, 80 percent actual, 100 percent forecast | pass | `BudgetLimit` 15 USD, `TimeUnit` MONTHLY, notifications ACTUAL 80 and FORECASTED 100, both PERCENTAGE |
| ECR immutable, scan on push, keep 10 | pass | `ImageTagMutability: IMMUTABLE`, `ScanOnPush: true`, lifecycle `imageCountMoreThan` 10 expire |
| No email address in repo | pass | none outside `.claude/skills`; three vendored doc placeholders predate this card (`a@b.co`, `testuser@example.com`, `you+bot@gmail.com`) |

Not run: cfn-guard (not installed here). The `ecr_immutable_tags` rule is met by the parsed values above; the CI `cfn` job runs it.
No OIDC provider or IAM role in the template (scope check, issue Context f).
Local tests: uvx cfn-lint==1.57.2 infra/cfn/*.yaml
