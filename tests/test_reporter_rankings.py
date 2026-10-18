"""Cost and latency rankings the data contradicts are rejected (issue #135)."""

from pathlib import Path

from advisor.reporter import build_prompt, contradicted_rankings, make_reporter
from advisor.schemas import Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs

# Cut from results/sample4/metrics.json. Idle cost: ecs-ec2 0.0641/h, eks-ec2 0.1641/h.
SAMPLE4 = Path(__file__).resolve().parent / "fixtures" / "reporter" / "sample4-metrics.json"
PROFILES = ["scale-from-min", "spike", "steady", "step"]
WRONG_COST = "ECS with EC2 is more costly than EKS with EC2"
RIGHT_COST = "EKS with EC2 is more costly than ECS with EC2"


class StubLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.drafts.pop(0)


def sample4() -> Metrics:
    return Metrics.model_validate_json(SAMPLE4.read_text())


def report(body: str) -> str:
    sections = "".join(f"### {p}\n| Metric | ecs-fargate |\n|---|---|\n\n" for p in PROFILES)
    return f"## Verdict\n{body}\n\n## Comparison table\n{sections}## Trade-offs\ntext\n"


def test_sample4_rejects_the_wrong_cost_ranking_and_accepts_the_right_one():
    metrics = sample4()
    assert contradicted_rankings(WRONG_COST, metrics) == [WRONG_COST]
    assert contradicted_rankings(f"Pick it. {WRONG_COST}. Done.", metrics) == [WRONG_COST]
    assert contradicted_rankings(RIGHT_COST, metrics) == []


def test_cell_names_in_other_spellings_are_found():
    metrics = sample4()
    for text in (
        "ecs-ec2 costs more than eks-ec2.",
        "ECS EC2 has a higher idle cost than EKS on EC2 instances.",
        "EKS with EC2 is cheaper than ECS with EC2.",
        # The sentence from sample4/report.md, with a cell before and after the comparison.
        (
            "ECS with EC2 instances offers a middle ground but generally lags in performance compared "
            "to Fargate and is more costly than EKS with EC2 instances."
        ),
    ):
        assert contradicted_rankings(text, metrics) != [], text


def test_wrong_latency_ranking_is_rejected_and_the_right_one_accepted():
    metrics = sample4()
    # ecs-fargate has the lowest p95 and p99 of every profile.
    assert contradicted_rankings("EKS with EC2 is faster than ECS Fargate.", metrics) != []
    assert contradicted_rankings("ECS Fargate has higher latency than EKS EC2.", metrics) != []
    assert contradicted_rankings("ECS Fargate is faster than EKS with EC2.", metrics) == []
    assert contradicted_rankings("EKS with EC2 is slower than ECS Fargate.", metrics) == []


def test_draft_with_no_ranking_sentence_passes():
    metrics = sample4()
    assert contradicted_rankings(report("ECS Fargate wins. No errors in most cells."), metrics) == []
    # One cell only, or no cost or latency word: not a clear ranking, so not checked.
    assert contradicted_rankings("ECS is cheaper than running it yourself.", metrics) == []
    assert contradicted_rankings("ECS EC2 needs fewer config lines than EKS EC2.", metrics) == []


def test_prompt_lists_the_ranking_facts():
    prompt = build_prompt(sample4())
    assert "idle_usd_per_hour: lowest to highest: ecs-fargate, ecs-ec2, eks-ec2" in prompt
    assert "p95_ms (steady): lowest to highest: ecs-fargate, ecs-ec2, eks-ec2" in prompt


def test_reporter_retries_a_contradicted_ranking_then_fails_after_three(tmp_path):
    paths = create_run_dirs("sample4", tmp_path)
    paths.metrics.write_text(SAMPLE4.read_text())
    state = RunState(
        run_id="sample4",
        cells=[CellSpec(name="ecs-ec2", profile="steady")],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
    )
    llm = StubLLM([report(WRONG_COST), report(RIGHT_COST)])
    assert make_reporter(llm, tmp_path)(state) == {"status": "reported"}
    assert f"These rankings contradict metrics.json: {WRONG_COST}" in llm.prompts[1]
    assert paths.report.read_text() == report(RIGHT_COST)

    paths.report.unlink()
    update = make_reporter(StubLLM([report(WRONG_COST)] * 3), tmp_path)(state)
    assert update["status"] == "report_failed"
    assert WRONG_COST in update["errors"][-1]
    assert not paths.report.exists()
