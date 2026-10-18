"""Reporter layout: the LLM writes only the Verdict; code writes the sections and tables (issues #119, #134, #141)."""

from pathlib import Path

from advisor.numcheck import check
from advisor.reporter import build_prompt, make_reporter, verdict_of
from advisor.schemas import Figure, Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs


class StubLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.drafts.pop(0)


def make_metrics() -> Metrics:
    fig = Figure(name="p95_ms", value=120.0, unit="ms", cell="ecs-fargate", source="k6.json")
    return Metrics(run_id="lay-01", figures=[fig])


def test_prompt_asks_for_the_verdict_paragraph_only():
    prompt = build_prompt(make_metrics())
    assert "one short paragraph of plain prose" in prompt
    assert "No heading, no table, no list" in prompt


def test_verdict_of_drops_a_leading_verdict_heading_only():
    assert verdict_of("## Verdict\nECS wins.\n") == "ECS wins."
    assert verdict_of("  ECS wins.  ") == "ECS wins."
    assert verdict_of("ECS wins.\n## Verdict\n") == "ECS wins.\n## Verdict"


def test_report_has_the_sections_in_order(tmp_path):
    paths = create_run_dirs("lay-01", tmp_path)
    paths.metrics.write_text(make_metrics().model_dump_json())
    state = RunState(
        run_id="lay-01",
        cells=[CellSpec(name="ecs-fargate", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )
    assert make_reporter(StubLLM(["## Verdict\nECS wins."]), tmp_path)(state) == {"status": "reported"}
    text = paths.report.read_text()
    positions = [text.index(h) for h in ("## Verdict\nECS wins.\n", "## Comparison table", "### steady", "### Per cell")]
    assert positions == sorted(positions)
    assert "| P95 latency (ms) | n/a |" in text  # the figure has no profile, so it is not a steady figure


# Cut from results/sample4/metrics.json: ecs-fargate, ecs-ec2 and eks-ec2, all 4 profiles.
SAMPLE4 = Path(__file__).resolve().parent / "fixtures" / "reporter" / "sample4-metrics.json"


def sample4() -> Metrics:
    return Metrics.model_validate_json(SAMPLE4.read_text())


def test_prompt_names_the_profiles_of_sample4():
    assert "Profiles that ran: scale-from-min, spike, steady, step." in build_prompt(sample4())


def test_prompt_figures_are_rounded_to_four_significant_digits():
    prompt = build_prompt(sample4())
    assert '"value": 1188.0' in prompt and "1188.430724" not in prompt
    assert '"value": 0.0008543' in prompt
    assert check("Deploy 1188 s, 11.07 ms, cost 0.0008543 USD", sample4()) == []
