# QA v1 · issue 52 · head 97d0857

Non-visual change (CLI flag); no screenshots by design.

| AC | Result | Proof |
|---|---|---|
| Build has `--platform linux/amd64` by default | pass | `tests/test_push_image.py::test_push_logs_in_builds_tags_and_pushes` |
| Mirror pull uses the same platform | pass | `test_mirror_pushes_exact_versions_and_skips_existing` |
| `--platform linux/arm64` overrides | pass | `test_platform_flag_overrides_build_and_mirror_pull`, `test_cli_platform_defaults_to_amd64_and_overrides` |
| ruff + pytest | pass | ruff: All checks passed; pytest: 93 passed |

Mutation check (each reverted, tree clean afterwards):
- dropped `--platform` from build: 2 tests red
- dropped `--platform` from pull: 2 tests red
- dropped `args.platform` in the `--mirror-base` call: 1 test red

Test gaps (not written, Medium): the mirror skips a tag already in the immutable ECR repo, so an
existing arm64 mirror is not replaced by this fix (operator step, named in the PR risk block).
