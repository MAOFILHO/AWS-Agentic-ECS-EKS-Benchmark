"""QA: the env-driven path create_app() uses when uvicorn starts the service."""

import boto3
from fastapi.testclient import TestClient

from workload.app import create_app

TICKET = {"text": "my login is locked"}


def test_triage_env_mock_mode_returns_category_and_reply(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.delenv("MOCK_LATENCY_MS", raising=False)
    resp = TestClient(create_app()).post("/triage", json=TICKET)
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "account"
    assert body["reply"]


def test_bedrock_env_uses_boto3_client_and_model_id(monkeypatch):
    calls = []

    class Stub:
        def converse(self, **kwargs):
            calls.append(kwargs)
            return {"output": {"message": {"content": [{"text": "stub"}]}}}

    services = []
    monkeypatch.setattr(boto3, "client", lambda name, **kw: services.append(name) or Stub())
    monkeypatch.setenv("LLM_MODE", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env-model-id")

    client = TestClient(create_app())
    for n in (1, 2):
        resp = client.post("/triage", json=TICKET)
        assert resp.status_code == 200
        assert resp.json()["reply"] == "stub"
        assert len(calls) == n
    assert services == ["bedrock-runtime"]
    assert all(c["modelId"] == "env-model-id" for c in calls)


def test_triage_rejects_body_without_text(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    resp = TestClient(create_app()).post("/triage", json={})
    assert resp.status_code == 422
