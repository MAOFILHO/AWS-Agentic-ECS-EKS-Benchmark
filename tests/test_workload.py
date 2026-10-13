import time

from fastapi.testclient import TestClient

from workload.agent import BedrockLLM, MockLLM, make_llm
from workload.app import create_app

TICKET = {"text": "I was charged twice on my invoice this month"}


def client_for(llm):
    return TestClient(create_app(llm=llm))


def test_triage_mock_returns_category_and_reply():
    resp = client_for(MockLLM()).post("/triage", json=TICKET)
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "billing"
    assert body["reply"]


def test_health_and_ready():
    client = client_for(MockLLM())
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_mock_latency_delays_triage():
    client = client_for(make_llm({"LLM_MODE": "mock", "MOCK_LATENCY_MS": "200"}))
    start = time.perf_counter()
    resp = client.post("/triage", json=TICKET)
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert elapsed >= 0.2


def test_mock_is_default_mode_with_zero_latency():
    llm = make_llm({})
    assert isinstance(llm, MockLLM)
    assert llm.latency_ms == 0


class StubBedrock:
    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"role": "assistant", "content": [{"text": "stub reply"}]}}}


def test_bedrock_calls_client_once_per_request_with_model_id():
    stub = StubBedrock()
    llm = make_llm({"LLM_MODE": "bedrock", "BEDROCK_MODEL_ID": "test-model-id"}, client=stub)
    assert isinstance(llm, BedrockLLM)
    client = client_for(llm)

    resp = client.post("/triage", json=TICKET)
    assert resp.status_code == 200
    assert resp.json()["reply"] == "stub reply"
    assert len(stub.calls) == 1
    assert stub.calls[0]["modelId"] == "test-model-id"

    client.post("/triage", json=TICKET)
    assert len(stub.calls) == 2


def test_bedrock_model_id_defaults_when_unset_or_empty():
    for env in ({"LLM_MODE": "bedrock"}, {"LLM_MODE": "bedrock", "BEDROCK_MODEL_ID": ""}):
        llm = make_llm(env, client=StubBedrock())
        assert isinstance(llm, BedrockLLM)
        assert llm.model_id == "amazon.nova-micro-v1:0"


def test_bedrock_model_id_env_overrides_default():
    llm = make_llm({"LLM_MODE": "bedrock", "BEDROCK_MODEL_ID": "x.test-model"}, client=StubBedrock())
    assert llm.model_id == "x.test-model"
