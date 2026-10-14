# QA v1 · issue #37 · head e1b4ae2

| AC | Result | Proof |
|---|---|---|
| Failed change set exits non-zero, message holds full StatusReason | pass | test_failed_change_set_exits_with_full_reason_and_deletes_it |
| Failed change set still deleted | pass | same test; Stubber assert_no_pending_responses covers delete_change_set |
| "no changes" exits 0 with `no changes.` | pass | test_no_changes_change_set_exits_zero_with_message |
| ruff + pytest pass | pass | ruff clean · 40 passed |

Mutation check: with the old deploy_stack.py (delete before print), test_failed_change_set_reason_is_shown_even_if_delete_fails fails (1 failed, 39 passed). With the fix: 40 passed.

Local tests: `uv run --locked ruff check && uv run --locked pytest -q`

Test gaps (not written): none High.
