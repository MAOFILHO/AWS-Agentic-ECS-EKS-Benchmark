# QA report issue-67 v1 @ a9325b0
- AC1 cfn-lint 1.57.2 on infra/cfn/*.yaml: exit 0 (cfn-lint.log)
- AC2 cfn-guard validate: not runnable locally (not installed, no uv-installable package). CI job `cfn` (runs `cfn-guard validate --data infra/cfn`) is SUCCESS at a9325b0.
- AC3 EC2 vs FARGATE resources: tests in ac-tests.log pass
- AC4 boundary coverage test: passes with both new templates (ac-tests.log)
- AC5 ruff clean; pytest 149 passed, 13 skipped (ruff.log, pytest.log)
- AC6 no 12-digit id or email: test passes; grep of both templates finds none
