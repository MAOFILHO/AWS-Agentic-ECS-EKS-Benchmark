"""Reporter layout: pinned sections, units on rows, a Verdict section is required (issue #119).
One table per load profile when more than one ran (issue #134)."""

from pathlib import Path

from advisor.numcheck import check
from advisor.reporter import build_prompt, has_verdict, make_reporter, missing_profiles
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


# Cut from results/sample4/metrics.json: ecs-fargate and eks-ec2, all 4 profiles.
SAMPLE4 = Path(__file__).resolve().parent / "fixtures" / "reporter" / "sample4-metrics.json"
PROFILES = ["scale-from-min", "spike", "steady", "step"]


def sample4() -> Metrics:
    return Metrics.model_validate_json(SAMPLE4.read_text())


def draft_with(profiles) -> str:
    sections = "".join(f"### {p.capitalize()} profile\n| Metric | ecs-fargate |\n|---|---|\n\n" for p in profiles)
    return f"## Verdict\nECS wins.\n\n## Comparison table\n{sections}## Trade-offs\ntext\n"


def test_prompt_asks_for_a_table_per_profile_of_sample4():
    prompt = build_prompt(sample4())
    assert "Profiles that ran: scale-from-min, spike, steady, step." in prompt
    assert "one table per profile" in prompt
    assert f"just the profile name: {', '.join(PROFILES)}." in prompt


def test_missing_profile_section_is_rejected_and_all_accepted():
    metrics = sample4()
    assert missing_profiles(draft_with(["steady", "step", "spike"]), metrics) == ["scale-from-min"]
    assert missing_profiles(draft_with(PROFILES), metrics) == []
    # A profile named only in body text is not a section.
    assert missing_profiles(draft_with(["steady"]) + "step spike scale-from-min\n", metrics) == [
        "scale-from-min", "spike", "step"
    ]


def test_one_profile_needs_no_profile_section():
    assert missing_profiles(WITH_VERDICT, make_metrics()) == []


def test_draft_missing_a_profile_is_retried(tmp_path):
    paths = create_run_dirs("sample4", tmp_path)
    paths.metrics.write_text(SAMPLE4.read_text())
    state = RunState(
        run_id="sample4",
        cells=[CellSpec(name="ecs-fargate", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )
    llm = StubLLM([draft_with(["steady"]), draft_with(PROFILES)])
    assert make_reporter(llm, tmp_path)(state) == {"status": "reported"}
    assert "scale-from-min, spike, step" in llm.prompts[1]


def test_prompt_figures_are_rounded_to_four_significant_digits():
    prompt = build_prompt(sample4())
    assert '"value": 1188.0' in prompt and "1188.430724" not in prompt
    assert '"value": 0.0008543' in prompt
    assert check("Deploy 1188 s, 11.07 ms, cost 0.0008543 USD", sample4()) == []
