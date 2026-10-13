# QA v1 · issue #24 · PR #29 · base head `b57ec62`

| AC | Result | Proof |
|---|---|---|
| 1 cfn-lint exits 0 | pass | `cfn-lint.log` (cfn-lint 1.57.2, exit 0) · CI `cfn` job success on `b57ec62` |
| 2 exactly one NatGateway | pass | `test_exactly_one_nat_gateway` · `ac-checks.log` count 1 |
| 3 endpoints S3, ecr.api, ecr.dkr, logs, sts | pass | `test_defines_required_endpoints` (tightened in QA, mutation-checked: `mutation-check.log`) |
| 4 `us-east-1e` only in exclusion comment | pass | `test_us_east_1e_only_in_exclusion_comment` · `ac-checks.log` line 7 only |
| 5 run-id, ttl-hours, project on every taggable resource | pass | `test_every_taggable_resource_has_run_tags` (skips Route, SubnetRouteTableAssociation, VPCGatewayAttachment: no Tags property) |

Test change: the AC3 test matched service names anywhere in the file text; it now maps each
`AWS::EC2::VPCEndpoint` ServiceName to its type. Renaming `sts` to `ssm` makes it fail.

Test gaps (not written):
- Low: cfn-guard not run locally (binary not installed); CI `cfn` job ran it green on `b57ec62`.
- Low: no check that both AZ selects never resolve to `us-east-1e` at deploy time (parameter default is `us-east-1a,us-east-1b`; a caller can still pass `us-east-1e`).

Verify commands: `verify.log` (`uv run --locked ruff check` exit 0 · `uv run --locked pytest` 14 passed).
