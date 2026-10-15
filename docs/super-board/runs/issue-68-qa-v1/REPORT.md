# Issue #68 · QA v1 · builder head `23273c8`

| AC | Result | Proof |
|---|---|---|
| 1 Missing RUN_ID / IMAGE_URI / region exits non-zero, names it, no AWS call | ✅ | `make-eks-ec2-no-inputs.txt`, `make-eks-fargate-no-region.txt` (exit 2) · `test_missing_input_exits_before_any_aws_call` |
| 2 Test asserts stack and Helm order | ✅ | `test_stacks_then_kubeconfig_then_controller_then_workload` |
| 3 Kubeconfig is a temp file, `~/.kube/config` untouched | ✅ | `test_kubeconfig_is_a_temp_file_and_home_is_untouched` |
| 4 Output never contains a 12-digit account id | ❌ | `test_aws_error_from_main_masks_the_account_id` red: an AWS `ClientError` (e.g. AccessDenied with `arn:aws:sts::<account>:...`) escapes `main()` unmasked as a traceback |
| 5 `ruff check` and `pytest` pass | ❌ on `23273c8` | `pytest-builder-head.txt`: `test_bootstrap_oidc.py::test_no_tracked_text_file_holds_an_account_id` failed, because `tests/test_deploy_eks.py:12` held a literal 12-digit id. QA fixed the test file. |

Fixed looks like: `main()` wraps the deploy in `except (BotoCoreError, ClientError) as err: raise SystemExit(mask(f"error: {err}")) from None`, as `scripts/delete_run.py:289` and `scripts/push_image.py:84` do.

Test gaps (not written): Medium · no test for a SSM minor gap across a 10-batch boundary. Low · no real deploy (costs money, needs the owner).
