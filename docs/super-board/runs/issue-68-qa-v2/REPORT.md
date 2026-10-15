# Issue #68 · QA v2 · head `02f3792`

| AC | Result | Proof |
|---|---|---|
| AC1 missing RUN_ID / IMAGE_URI / region exits non-zero, names it, no AWS call | ✅ | `make-eks-ec2-no-inputs.txt` (exit 2, all four named) · `make-eks-fargate-no-region.txt` (exit 2, region named) · `test_missing_input_exits_before_any_aws_call[*]` |
| AC2 test asserts stack and Helm order | ✅ | `test_stacks_then_kubeconfig_then_controller_then_workload` |
| AC3 kubeconfig is a temp file, `~/.kube/config` untouched | ✅ | `test_kubeconfig_is_a_temp_file_and_home_is_untouched` |
| AC4 output never holds a 12-digit account id | ✅ | `test_output_never_contains_an_account_id` · `test_failed_helm_error_masks_the_account_id` · `test_aws_error_from_main_masks_the_account_id` |
| AC5 ruff + pytest pass | ✅ | `ruff.txt` All checks passed · `pytest.txt` 163 passed, 13 skipped |

Cross-checks (read, not run): stack parameters match `40-eks.yaml` (`RunId`, `NetworkStackName`, `ComputeType`, `KubernetesVersion`) and `41-eks-lbc-iam.yaml` (`RunId`, `EksStackName`, `Cell`); release names and namespace match `delete_run.py`; `delete_run.eks_cluster` finds `<id>-eks-<cell>` and skips `<id>-eks-lbc-iam` (no `ClusterName` output).

Test gaps (not written, Low): missing `AWS_PROFILE` alone is not parametrized; no test for SSM probing past 10 minors. No real AWS deploy ran (costs money, needs the owner).
