# Issue #128 QA v1

Head under test: `ca03988` (branch `issue-128-ec2-same-instance-type`, PR #131).

| AC | Result | Proof |
|---|---|---|
| 1. `ecs-ec2` and `eks-ec2` deploy the same `InstanceType` from the run config | pass | `tests/test_deployer.py::test_both_ec2_cells_deploy_the_run_config_instance_type`; `tests/test_deploy_eks.py::test_ec2_cell_passes_the_instance_type_to_the_eks_stack`. Mutations M2, M3, M5 turn them red |
| 2. A test fails when the template defaults differ | pass | `tests/test_instance_type.py`; mutation M1 (`30-ecs.yaml` back to `t3.small`) turns both tests red |
| 3. An EC2 cell's `config.json` records the instance type | pass | `tests/test_collector.py::test_ec2_cell_config_records_the_instance_type`; mutation M4 turns it red |
| 4. Existing deployer, cost and report tests pass | pass | `uv run --locked pytest`: 505 passed, 4 skipped; `ruff check` clean |
| 5. No AWS call in tests | pass | full suite passes with outbound sockets blocked and `AWS_PROFILE` unset (`test-run.txt`) |

Each mutation was reverted after its run (`mutations.txt`).

## Test gaps (not written)
- Medium: Fix step 3 says the report and README table show the instance type. Neither `advisor/reporter.py` nor `README.md` shows it. This is not an AC.
- Low: no end-to-end test runs `scripts/deploy_stack.py` with `--env-param InstanceType=INSTANCE_TYPE` to resolve the env var. Its `--env-param` path is the same one `ComputeType` already uses.
