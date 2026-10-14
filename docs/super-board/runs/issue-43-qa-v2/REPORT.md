# QA v2 · issue #43 · PR #49 · head 4e761bb

| AC | Result | Proof |
|---|---|---|
| 1 no region exits, names it | pass | tests/test_push_image.py:75, :83 (CLI) |
| 2 tag = short sha, dirty tree stops | pass | tests/test_push_image.py:133, :138 against a real temp git repo |
| 3 no token, no 12-digit account id in output | pass | v1 witness tests/test_push_image.py:93 now green; :112 mirror DescribeImages denied; :172 docker failure hides token and account; scripts/push_image.py:79-82, :107-115 |
| 4 cfn-lint + cfn-guard, ecr_immutable_tags covers it | pass | cfn-lint.log exit 0; CI cfn job (cfn-guard 3.1.2 validate infra/cfn) green on 4e761bb; rules.guard:4 selects every AWS::ECR::Repository; tests/test_push_image.py:217 |
| 5 docker build workload/ with no arg | pass | docker-build.log exit 0 |
| 6 ruff + pytest | pass | ruff.log exit 0; pytest.log 58 passed |

## Test gaps (not written)
- Low: an unknown AWS_PROFILE raises botocore ProfileNotFound from boto3.Session as a traceback (exit 1). It names the profile only, no token or account id.
- Low: mirrored uv image is pushed but the Dockerfile still copies uv from ghcr.io; --base-image only swaps the python base (carried from v1).
- Low: base-images lifecycle keeps the last 10 images; old mirrored bases a pinned build needs could expire over time.
- Not run: a real ECR push (needs the owner's AWS_PROFILE and a bootstrap update).
