# Issue #5 QA v1

Head under test: `217cf79` (branch `issue-5-workload-fastapi-triage-agent`).

| AC | Proof |
|---|---|
| 1 `POST /triage` mock → 200 with `category`, `reply` | `tests/test_workload.py::test_triage_mock_returns_category_and_reply`, `tests/test_workload_env.py::test_triage_env_mock_mode_returns_category_and_reply`, live smoke in `smoke.log` |
| 2 `/healthz`, `/readyz` → 200 | `tests/test_workload.py::test_health_and_ready`, live smoke |
| 3 mock 200 ms → `/triage` ≥ 200 ms | `tests/test_workload.py::test_mock_latency_delays_triage`; live smoke 0.209 s |
| 4 Bedrock: stub called once per request with `BEDROCK_MODEL_ID` | `tests/test_workload.py::test_bedrock_calls_client_once_per_request_with_model_id`, `tests/test_workload_env.py::test_bedrock_env_uses_boto3_client_and_model_id` (env path, `boto3.client` stubbed) |
| 5 `uv run pytest` passes | `pytest.log`: 8 passed |

QA added `tests/test_workload_env.py`: the builder's tests inject the LLM, so the env-driven
`create_app()` path that uvicorn runs was untested.

Test gaps (not written): Bedrock error handling (throttling, missing `BEDROCK_MODEL_ID` raises
KeyError at import) — not in the ACs.
