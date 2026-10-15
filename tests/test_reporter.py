from advisor.reporter import (
    DEFAULT_BEDROCK_MODEL_ID,
    BedrockLLM,
    build_prompt,
    make_reporter,
)
from advisor.schemas import Figure, Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs

GOOD = "## Verdict\nECS wins.\n\n| cell | p95 ms |\n|---|---|\n| ecs-fargate | 120 |\n"
BAD = "## Verdict\nECS wins.\n\n| cell | p95 ms |\n|---|---|\n| ecs-fargate | 97 |\n"


class StubLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.drafts.pop(0)


def make_state() -> RunState:
    return RunState(
        run_id="smoke-01",
        cells=[CellSpec(name="ecs-fargate", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )


def write_metrics(tmp_path):
    paths = create_run_dirs("smoke-01", tmp_path)
    metrics = Metrics(
        run_id="smoke-01",
        figures=[
            Figure(
                name="p95_ms",
                value=120.4,
                unit="ms",
                cell="ecs-fargate",
                source="k6.json",
            )
        ],
    )
    paths.metrics.write_text(metrics.model_dump_json())
    return paths


def test_clean_draft_writes_report(tmp_path):
    paths = write_metrics(tmp_path)
    llm = StubLLM([GOOD])
    update = make_reporter(llm, tmp_path)(make_state())
    assert update == {"status": "reported"}
    assert paths.report.read_text() == GOOD
    assert len(llm.prompts) == 1


def test_invented_number_triggers_a_retry_with_the_offenders(tmp_path):
    paths = write_metrics(tmp_path)
    llm = StubLLM([BAD, GOOD])
    update = make_reporter(llm, tmp_path)(make_state())
    assert update == {"status": "reported"}
    assert len(llm.prompts) == 2
    assert "97" in llm.prompts[1]
    assert paths.report.read_text() == GOOD


def test_third_failure_fails_the_step_and_writes_no_report(tmp_path):
    paths = write_metrics(tmp_path)
    metrics_before = paths.metrics.read_text()
    llm = StubLLM([BAD, BAD, BAD, GOOD])
    update = make_reporter(llm, tmp_path)(make_state())
    assert len(llm.prompts) == 3
    assert update["status"] == "report_failed"
    assert "97" in update["errors"][-1]
    assert not paths.report.exists()
    assert paths.metrics.read_text() == metrics_before


def test_failure_error_redacts_account_id_and_email(tmp_path):
    write_metrics(tmp_path)
    account, email = "1234" * 3, "someone" + "@" + "example.com"
    leak = f"Account {account}, owner {email}."
    update = make_reporter(StubLLM([leak] * 3), tmp_path)(make_state())
    assert update["status"] == "report_failed"
    assert account not in update["errors"][-1]
    assert email not in update["errors"][-1]
    assert "<redacted>" in update["errors"][-1]


def test_prompt_forbids_new_figures_and_carries_metrics(tmp_path):
    paths = write_metrics(tmp_path)
    prompt = build_prompt(Metrics.model_validate_json(paths.metrics.read_text()))
    assert "only the figures in metrics.json" in prompt
    assert "percentage difference" in prompt
    assert '"p95_ms"' in prompt


def test_bedrock_llm_uses_converse_with_default_model():
    calls = []

    class Client:
        def converse(self, **kwargs):
            calls.append(kwargs)
            return {"output": {"message": {"content": [{"text": "ok"}]}}}

    llm = BedrockLLM(client=Client(), env={})
    assert llm.complete("hi") == "ok"
    assert calls[0]["modelId"] == DEFAULT_BEDROCK_MODEL_ID == "amazon.nova-micro-v1:0"
