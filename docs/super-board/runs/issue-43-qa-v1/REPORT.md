# QA v1 · issue #43 · PR #49 · head e605085

| AC | Result | Proof |
|---|---|---|
| 1 no region exits, names it | pass | tests/test_push_image.py:83 (CLI, mutation-checked) and :74 |
| 2 tag = short sha, dirty tree stops | pass | tests/test_push_image.py:119, :124 |
| 3 no token, no 12-digit account id in output | FAIL | tests/test_push_image.py:93 red: AccessDeniedException from GetAuthorizationToken carries the caller ARN; scripts/push_image.py:74 lets the ClientError escape, so the traceback prints the account id |
| 4 cfn-lint + cfn-guard, ecr_immutable_tags covers it | pass | cfn-lint.log exit 0; CI cfn job success on e605085 (cfn-guard 3.1.2 validate infra/cfn); rules.guard:4 selects every AWS::ECR::Repository |
| 5 docker build workload/ with no arg | pass | docker-build.log exit 0 |
| 6 ruff + pytest | FAIL | ruff.log exit 0; pytest.log 1 failed (the AC3 witness), 56 passed |

What fixed looks like: catch botocore ClientError (and BotoCoreError) around the ECR calls in
push_image.py and exit with a redact()-ed message, so the AC3 witness test goes green.

## Test gaps (not written)
- Medium: tag_exists re-raises non-NotFound ClientErrors (push_image.py tag_exists) the same way; one fix covers both.
- Low: mirrored uv image is pushed but the Dockerfile still copies uv from ghcr.io; --base-image only swaps the python base.
- Low: ruff format --check reports pre-existing formatting drift in tests/test_push_image.py (not a verify command).

root-cause-hash: 6d258bf532cb
