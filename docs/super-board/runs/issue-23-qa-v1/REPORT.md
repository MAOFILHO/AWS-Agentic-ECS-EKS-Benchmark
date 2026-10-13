# Issue #23 QA v1

Branch `issue-23-github-oidc-deploy-role`, built head `dfde8c7`.

| AC | Check | Result |
|---|---|---|
| 1 | `uvx cfn-lint==1.57.2 infra/cfn/00-bootstrap.yaml` | exit 0 (`cfn-lint.txt`) |
| 2 | `test_deploy_role_trusts_only_this_repository`; trust `sub` is `repo:MAOFILHO/agentic-platform-advisor:ref:refs/heads/main` under `StringEquals` | pass (`pytest.txt`) |
| 3 | `test_no_statement_allows_every_action_on_every_resource` | pass (`pytest.txt`) |
| 4 | `test_no_tracked_text_file_holds_an_account_id` plus `git grep` for 12-digit runs | pass, no hits |

CI on `dfde8c7`: lint, test, image, cfn (cfn-lint + cfn-guard) all green.

## Test gap fixed
- High: the trust test pooled `sub` values across statements, so a second trust statement
  with no `sub` condition (trusts every repo) still passed. Now each statement must carry its own
  `sub`. Proof in `mutation.txt`: the mutant survived before, is killed after.

## Test gaps (not written)
- Medium: AC3 test reads only inline `Policies` on `AWS::IAM::Role`; a future
  `AWS::IAM::ManagedPolicy` or `AWS::IAM::Policy` resource would not be scanned.
- Low: AC3 is met literally, but `ec2:*`, `ecs:*`, `eks:*`, `elasticloadbalancing:*`,
  `logs:*` and `application-autoscaling:*` are granted on `Resource: "*"`. Reviewer judgment.
- Low: the trust test does not assert the `aud` condition.

Not run: stack deploy (needs owner SSO, per PR `needs-you`).
