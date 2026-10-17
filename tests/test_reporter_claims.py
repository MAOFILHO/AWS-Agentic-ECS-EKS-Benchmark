"""Reporter claims the data does not support, and make report (issue #116)."""

import shutil
from pathlib import Path

from advisor.reporter import build_prompt, make_reporter, unsupported_claims
from advisor.schemas import Figure, Metrics, read_metrics
from advisor.state import Capacity, CellSpec, RunState
from scripts import make_report

# results/sample3/metrics.json from the first full run: steady only, every error_rate 0.0.
SAMPLE3 = Path(__file__).resolve().parent / "fixtures" / "reporter" / "sample3-metrics.json"
HIGHER_ERRORS = "## Verdict\nECS wins.\n\necs-ec2 has slightly higher error rates.\n"
NO_ERRORS = "## Verdict\nECS wins.\n\nNo errors in any cell.\n"


class StubLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.drafts.pop(0)


def sample3() -> Metrics:
    return read_metrics(SAMPLE3)


def test_all_zero_error_rates_reject_a_comparative_error_claim():
    metrics = sample3()
    assert {f.value for f in metrics.figures if f.name == "error_rate"} == {0.0}
    for draft in (
        HIGHER_ERRORS,
        "Pick ecs-ec2 despite more errors.",
        "The error rate of eks-ec2 is worse.",
        "eks-ec2 has a lower error_rate than ecs-ec2.",
    ):
        assert unsupported_claims(draft, metrics) != [], draft


def test_all_zero_error_rates_accept_no_errors_and_unrelated_comparatives():
    metrics = sample3()
    assert unsupported_claims(NO_ERRORS, metrics) == []
    assert unsupported_claims("EKS has more moving parts. No errors in any cell.", metrics) == []


def test_different_error_rates_allow_a_comparison():
    metrics = Metrics(
        run_id="diff-01",
        figures=[
            Figure(name="error_rate", value=0.0, unit="ratio", cell="ecs-fargate", source="a.json"),
            Figure(name="error_rate", value=0.2, unit="ratio", cell="eks-ec2", source="b.json"),
        ],
    )
    assert unsupported_claims("eks-ec2 has a higher error rate.", metrics) == []


def test_reporter_retries_a_comparative_error_claim_then_writes_the_clean_draft(tmp_path):
    run = tmp_path / "results" / "sample3"
    run.mkdir(parents=True)
    shutil.copy(SAMPLE3, run / "metrics.json")
    llm = StubLLM([HIGHER_ERRORS, NO_ERRORS])
    state = RunState(
        run_id="sample3",
        cells=[CellSpec(name="ecs-ec2", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )
    assert make_reporter(llm, tmp_path)(state) == {"status": "reported"}
    assert len(llm.prompts) == 2
    assert "higher error rates" in llm.prompts[1]
    assert (run / "report.md").read_text() == NO_ERRORS


def test_reporter_fails_after_three_comparative_error_claims(tmp_path):
    run = tmp_path / "results" / "sample3"
    run.mkdir(parents=True)
    shutil.copy(SAMPLE3, run / "metrics.json")
    state = RunState(
        run_id="sample3",
        cells=[CellSpec(name="ecs-ec2", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )
    update = make_reporter(StubLLM([HIGHER_ERRORS] * 3), tmp_path)(state)
    assert update["status"] == "report_failed"
    assert "higher error" in update["errors"][-1]
    assert not (run / "report.md").exists()


def test_steady_only_prompt_forbids_scaling_and_cold_start_claims():
    prompt = build_prompt(sample3())
    assert "Profiles that ran: steady." in prompt
    assert "no claim about scaling, scale-out or cold start" in prompt
    assert 'write "no errors in any cell"' in prompt


def test_prompt_with_a_scaling_profile_drops_the_steady_only_rule():
    metrics = Metrics(
        run_id="spike-01",
        figures=[
            Figure(name="p95_ms", value=9.0, unit="ms", cell="ecs-fargate", source="a.json", profile="steady"),
            Figure(name="error_rate", value=0.1, unit="ratio", cell="ecs-fargate", source="a.json", profile="spike"),
        ],
    )
    prompt = build_prompt(metrics)
    assert "Profiles that ran: spike, steady." in prompt
    assert "no claim about scaling" not in prompt
    assert "no errors in any cell" not in prompt


def test_prompt_calls_out_each_shared_cost_line():
    metrics = sample3()
    assert metrics.shared_costs  # the analyst already reads raw/shared-cost.json into metrics.json
    prompt = build_prompt(metrics)
    assert "shared cost" in prompt.lower()
    for line in metrics.shared_costs:
        assert f"- {line.item}: {line.usd} USD" in prompt


def test_make_report_writes_report_md_from_saved_metrics_without_aws(tmp_path, monkeypatch):
    def no_boto3(*args, **kwargs):
        raise AssertionError("AWS call")

    monkeypatch.setattr(make_report.boto3, "client", no_boto3)
    run = tmp_path / "results" / "sample3"
    run.mkdir(parents=True)
    shutil.copy(SAMPLE3, run / "metrics.json")
    llm = StubLLM([NO_ERRORS])
    assert make_report.main(["--run-id", "sample3"], root=tmp_path, llm=llm) == 0
    assert (run / "report.md").read_text() == NO_ERRORS


def test_make_report_calls_only_bedrock_in_the_given_region(tmp_path, monkeypatch):
    clients = []

    class FakeBedrock:
        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": NO_ERRORS}]}}}

    def client(service, **kwargs):
        clients.append((service, kwargs.get("region_name")))
        return FakeBedrock()

    monkeypatch.setattr(make_report.boto3, "client", client)
    run = tmp_path / "results" / "sample3"
    run.mkdir(parents=True)
    shutil.copy(SAMPLE3, run / "metrics.json")
    assert make_report.main(["--run-id", "sample3", "--region", "us-east-1"], root=tmp_path) == 0
    assert clients == [("bedrock-runtime", "us-east-1")]


def test_make_report_exits_1_without_metrics_or_after_three_bad_drafts(tmp_path, capsys):
    assert make_report.main(["--run-id", "nope"], root=tmp_path, llm=StubLLM([])) == 1
    run = tmp_path / "results" / "sample3"
    run.mkdir(parents=True)
    shutil.copy(SAMPLE3, run / "metrics.json")
    assert make_report.main(["--run-id", "sample3"], root=tmp_path, llm=StubLLM([HIGHER_ERRORS] * 3)) == 1
    assert not (run / "report.md").exists()
    assert "higher error" in capsys.readouterr().err
