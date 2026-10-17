"""Reporter layout: pinned sections, units on rows, a Verdict section is required (issue #119)."""

from advisor.reporter import build_prompt, has_verdict, make_reporter
from advisor.schemas import Figure, Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs

WITH_VERDICT = "## Verdict\nECS wins.\n\n## Comparison Table\n| Metric | ecs |\n|---|---|\n| P95 (ms) | 120 |\n"
NO_VERDICT = "# ECS vs EKS: A Verdict\n\n## Comparison Table\n| Metric | ecs |\n|---|---|\n| P95 (ms) | 120 |\n"


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


def test_prompt_names_the_four_sections_in_order():
    prompt = build_prompt(make_metrics())
    names = ["Verdict", "Comparison table", "Trade-offs", "Recommendation"]
    positions = [prompt.index(f"## {n}") for n in names]
    assert positions == sorted(positions)


def test_prompt_pins_the_unit_in_row_label_rule():
    prompt = build_prompt(make_metrics())
    assert "unit in the row label" in prompt
    assert "no unit in any column header" in prompt


def test_has_verdict_rejects_missing_and_accepts_present():
    assert not has_verdict(NO_VERDICT)
    assert not has_verdict("## Verdict\n\n## Trade-offs\ntext\n")
    assert has_verdict(WITH_VERDICT)


def test_draft_without_verdict_is_retried(tmp_path):
    paths = create_run_dirs("lay-01", tmp_path)
    paths.metrics.write_text(make_metrics().model_dump_json())
    state = RunState(
        run_id="lay-01",
        cells=[CellSpec(name="ecs-fargate", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )
    llm = StubLLM([NO_VERDICT, WITH_VERDICT])
    assert make_reporter(llm, tmp_path)(state) == {"status": "reported"}
    assert len(llm.prompts) == 2
    assert "Verdict" in llm.prompts[1]
    assert paths.report.read_text() == WITH_VERDICT
