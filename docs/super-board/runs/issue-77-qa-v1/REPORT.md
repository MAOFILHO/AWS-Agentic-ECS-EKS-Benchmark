# QA v1 · issue #77 · head 18a532f

| AC | Result | Proof |
|---|---|---|
| Role allows Authorize/Revoke ingress | pass | `infra/cfn/41-eks-lbc-iam.yaml:97-103` unconditioned on `*`; `reference-compare.log` |
| Test lists reference actions, fails on a missing one, passes on the template | pass | `pytest.log`; `mutation.log` (dropping Authorize from the real template fails `test_lbc_role_grants_the_reference_ec2_and_elb_policy`) |
| cfn-lint and cfn-guard pass | pass | `cfn-lint.log`; CI job `cfn` (runs `cfn-guard validate`) green on 18a532f |
| ruff and pytest pass | pass | `ruff.log`, `pytest.log` (182 passed) |
| No account id or email | pass | `no-ids.log` |

`reference-compare.log`: the template's EC2/ELB grants (action, resource, condition) equal the
upstream v3.6.0 `docs/install/iam_policy.json` exactly (68 grants, no missing, no wider).
