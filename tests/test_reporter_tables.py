"""Code writes every table from metrics.json; the LLM writes only the Verdict (issue #141)."""

import re
from pathlib import Path

import pytest

from advisor.reporter import CELL_ROWS, FALLBACK_VERDICT, PROFILE_ROWS, make_reporter
from advisor.schemas import Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs

# A copy of results/short1/metrics.json: spike and scale-from-min, three cells, two gaps.
# Values cut to 7 significant digits: a 12-digit float tail reads as an account id to the repo scan.
SHORT1 = Path(__file__).resolve().parent / "fixtures" / "reporter" / "short1-metrics.json"
VERDICT = "ECS Fargate is cheaper than EKS with EC2, with no errors in any cell."
MATRIX = [CellSpec(name=c, profile=p) for c in ("ecs-fargate", "ecs-ec2", "eks-ec2")
          for p in ("spike", "scale-from-min")]


class StubLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.drafts.pop(0)


def short1() -> Metrics:
    return Metrics.model_validate_json(SHORT1.read_text())


def run(tmp_path, drafts, metrics: Metrics | None = None):
    paths = create_run_dirs("short1", tmp_path)
    paths.metrics.write_text((metrics or short1()).model_dump_json())
    state = RunState(run_id="short1", cells=MATRIX, region="us-east-1",
                     capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4))
    llm = StubLLM(drafts)
    return make_reporter(llm, tmp_path)(state), paths.report.read_text(), llm


def tables(report: str) -> dict[str, dict[str, dict[str, str]]]:
    """### heading -> row label -> cell -> shown text."""
    out = {}
    for section in re.split(r"^### ", report.split("\n## Shared costs")[0], flags=re.MULTILINE)[1:]:
        title, *lines = section.splitlines()
        rows = [[c.strip() for c in line.strip("|").split("|")] for line in lines if line.startswith("|")]
        header, body = rows[0], rows[2:]
        out[title] = {r[0]: dict(zip(header[1:], r[1:])) for r in body}
    return out


def test_every_short1_figure_shows_in_its_table(tmp_path):
    update, report, _ = run(tmp_path, [VERDICT])
    assert update == {"status": "reported"}
    found = tables(report)
    assert list(found) == ["spike", "scale-from-min", "Per cell"]
    labels = PROFILE_ROWS | CELL_ROWS
    for f in short1().figures:
        shown = found[f.profile or "Per cell"][labels[f.name]][f.cell]
        assert shown not in ("", "n/a", "N/A"), f
        assert abs(float(shown) - f.value) <= max(0.5, abs(f.value) * 5e-4), f
    # The three faults of the first short1 report.
    assert found["spike"]["P50 latency (ms)"]["ecs-ec2"] == "5406"
    assert found["spike"]["P95 latency (ms)"]["eks-ec2"] == "3862"
    assert found["spike"]["P99 latency (ms)"]["eks-ec2"] == "6051"
    assert found["spike"]["Scale-out (s)"]["ecs-fargate"] == "72.77"


def test_layout_is_pinned(tmp_path):
    _, report, _ = run(tmp_path, [VERDICT])
    headings = re.findall(r"^#+ .*$", report, re.MULTILINE)
    assert headings == ["# ECS vs EKS: short1", "## Verdict", "## Comparison table", "### spike",
                        "### scale-from-min", "### Per cell", "## Shared costs"]
    assert f"## Verdict\n{VERDICT}\n" in report
    for header in re.findall(r"^\| Metric \|.*$", report, re.MULTILINE):
        assert header == "| Metric | ecs-fargate | ecs-ec2 | eks-ec2 |"  # matrix order, no unit in it
    assert [r for r in tables(report)["spike"]] == list(PROFILE_ROWS.values())
    assert [r for r in tables(report)["Per cell"]] == list(CELL_ROWS.values())
    for line in short1().shared_costs:
        assert f"| {line.item} |" in report


def test_missing_figure_shows_na_and_its_gap_under_the_table(tmp_path):
    _, report, _ = run(tmp_path, [VERDICT])
    found = tables(report)["scale-from-min"]
    assert found["Scale-out (s)"] == {"ecs-fargate": "n/a", "ecs-ec2": "148.3", "eks-ec2": "n/a"}
    assert found["Cold start (s)"]["eks-ec2"] == "n/a"
    section = report.split("### scale-from-min", 1)[1].split("###", 1)[0]
    for gap in short1().gaps:
        assert f"- {gap}" in section
    assert "no scale-out happened" not in report.split("### scale-from-min", 1)[0]


@pytest.mark.parametrize(
    "draft",
    [
        "## Verdict\nECS wins.\n\n| Metric | ecs-ec2 |\n|---|---|\n| P50 latency (ms) | 1 |\n",
        "ECS Fargate p95 is 9999 ms.",
        "",
        "### spike\nECS wins.",
    ],
    ids=["table", "wrong-number", "empty", "heading"],
)
def test_llm_output_cannot_change_a_table(tmp_path, draft):
    _, clean, _ = run(tmp_path / "clean", [VERDICT])
    update, report, llm = run(tmp_path / "bad", [draft, VERDICT])
    assert update == {"status": "reported"} and len(llm.prompts) == 2
    assert report == clean
    _, fallback, _ = run(tmp_path / "fallback", [draft] * 3)
    assert fallback.split("## Comparison table", 1)[1] == clean.split("## Comparison table", 1)[1]
    assert "9999" not in fallback and "| 1 |" not in fallback


def test_three_failed_verdicts_still_write_the_report_with_the_fallback(tmp_path):
    update, report, llm = run(tmp_path, ["ECS Fargate p95 is 9999 ms."] * 3)
    assert len(llm.prompts) == 3
    assert update["status"] == "reported"
    assert "9999" in update["errors"][-1]
    assert f"## Verdict\n{FALLBACK_VERDICT}\n" in report
    assert tables(report)["spike"]["P50 latency (ms)"]["ecs-ec2"] == "5406"


def test_short1_fixture_holds_no_account_id_or_email():
    assert not re.search(r"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])|[\w.+-]+@[\w-]+\.[\w.-]+", SHORT1.read_text())
