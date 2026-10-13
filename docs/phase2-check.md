# Phase 2 check

Real-account run of the bootstrap stack and a test network deploy. Region `us-east-1`.

## Results

| Item | Result |
|---|---|
| Bootstrap stack `agentic-platform-advisor-bootstrap` | `CREATE_COMPLETE` |
| Network stack `p2test2-network` create | 2.2 minutes (2:11 wall time) to `CREATE_COMPLETE` |
| Network stack `p2test2-network` delete | 5.1 minutes (5:04 wall time) to `DELETE_COMPLETE` |
| Leftover resources for `RunId` `p2test2` | 0 |

## Commands run

a. `make bootstrap`
b. `RUN_ID=p2test2 uv run --locked python scripts/deploy_stack.py --stack p2test2-network --template infra/cfn/10-network.yaml --env-param RunId=RUN_ID`
c. `aws cloudformation delete-stack` then `aws cloudformation wait stack-delete-complete` on `p2test2-network`
d. `aws ec2 describe-nat-gateways` and `aws ec2 describe-vpc-endpoints`, both filtered on tag `run-id=p2test2`

## Leftover check

- The test stack was deleted 5.1 minutes after create finished, inside the 1 hour limit.
- The NAT gateway lists as `deleted`. AWS keeps a deleted NAT gateway visible for about an hour; it is not billed.
- `describe-vpc-endpoints` returns no endpoint, and no VPC carries the tag.
- The Resource Groups tagging API still listed 5 endpoints and the NAT gateway shortly after delete. Direct lookup confirms they are gone or `deleted`. The tag index lags, so it was not used for the count.
