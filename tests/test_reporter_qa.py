import pytest

from advisor.numcheck import check
from advisor.reporter import FALLBACK_VERDICT, make_reporter
from advisor.schemas import Figure, Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs

# Built at runtime so no committed file holds an id- or email-shaped string (AC4).
ACCOUNT = "1234" * 3
EMAIL = "someone" + "@" + "example.com"
CLEAN = "ECS Fargate has a p95 of 120 ms. "


def make_metrics() -> Metrics:
    return Metrics(
        run_id="qa-83",
        figures=[
            Figure(name="p95_ms", value=120.4, unit="ms", cell="ecs-fargate", source="k6.json")
        ],
    )


def make_state() -> RunState:
    return RunState(
        run_id="qa-83",
        cells=[CellSpec(name="ecs-fargate", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )


class StubLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)

    def complete(self, prompt: str) -> str:
        return self.drafts.pop(0)


@pytest.mark.parametrize(
    "text",
    [
        f"arn:aws:iam::{ACCOUNT}:role/agent",
        f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/agent",
        # Account id glued to a name: CDK bucket, snake_case, prefixed id.
        f"bucket cdk-assets-{ACCOUNT}-us-east-1",
        f"acct_{ACCOUNT}",
        f"id{ACCOUNT}",
    ],
    ids=["arn", "ecr-uri", "cdk-bucket", "snake-case", "prefixed"],
)
def test_account_id_anywhere_in_the_draft_is_an_offender(text):
    assert check(text, make_metrics()) != []


@pytest.mark.parametrize(
    "leak", [f"bucket cdk-assets-{ACCOUNT}-us-east-1", f"owner {EMAIL}"], ids=["account", "email"]
)
def test_a_leaking_draft_never_reaches_report_md(tmp_path, leak):
    paths = create_run_dirs("qa-83", tmp_path)
    paths.metrics.write_text(make_metrics().model_dump_json())
    update = make_reporter(StubLLM([CLEAN + leak] * 3), tmp_path)(make_state())
    assert update["status"] == "reported"
    report = paths.report.read_text()
    assert FALLBACK_VERDICT in report and ACCOUNT not in report and EMAIL not in report
    assert ACCOUNT not in update["errors"][-1]
    assert EMAIL not in update["errors"][-1]
