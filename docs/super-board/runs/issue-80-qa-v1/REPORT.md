# QA v1 · issue #80 · head 3c0ae3b

| AC | Result | Proof |
|---|---|---|
| AC1 RunState JSON round trip | ✅ | `tests/test_advisor_state.py:27`, full-field case `tests/test_advisor_state_qa.py:48` |
| AC2 figure without source rejected | ✅ | missing and empty `test_advisor_state.py:40`, null `test_advisor_state_qa.py:38` |
| AC3 bad run_id refused | ✅ | `test_advisor_state.py:67`, newline and edge cases `test_advisor_state_qa.py:26` |
| AC4 no account id or email | ✅ | outputs + tests scanned `test_advisor_state_qa.py:53`, `scan.txt` 0 hits |
| AC5 ruff + pytest | ✅ | `ruff.txt` clean, `pytest.txt` 207 passed |

Test gaps (not written):
- Medium: a whitespace-only `source` ("   ") passes `Figure` and `SharedCost` (`gap-whitespace-source.txt`). Fix needs app code (`str_strip_whitespace` or a pattern).
- Low: `Capacity` accepts `min_replicas > max_replicas`.
- Low: `source` is not checked to be relative (an absolute path or `../` passes).
