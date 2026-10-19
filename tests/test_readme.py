"""README.md, the published 3-run results and the sample run (issues #118 and #144).

Every number in README.md must come from results/published/aggregate.json (a min, median or max),
the run count, or the sample's cost files. A sum of cost-file values counts as from the cost files:
the "cost of one run" line is one.
"""

import json
import re
import subprocess
from pathlib import Path

from advisor.numcheck import check
from advisor.schemas import Figure, Metrics, SharedCost, read_aggregate, read_metrics
from scripts.make_readme_tables import END, START, render

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SAMPLE = ROOT / "results" / "sample"
PUBLISHED = ROOT / "results" / "published"
CHARTS = ("chart-burst-scaling.svg", "chart-burst-latency.svg", "chart-cost.svg", "chart-deploy.svg")


def _cost_values() -> list[float]:
    loads = []
    for path in sorted((SAMPLE / "raw").glob("*/cost.json")):
        cost = json.loads(path.read_text())
        loads.append(cost["load_usd"])
    shared = [item["usd"] for item in json.loads((SAMPLE / "raw" / "shared-cost.json").read_text())]
    return loads + [sum(loads), sum(shared), sum(loads) + sum(shared)]


def test_sample_run_is_tracked():
    tracked = subprocess.run(
        ["git", "ls-files", "results/sample"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    for name in ("metrics.json", "report.md", "chart.svg"):
        assert f"results/sample/{name}" in tracked


def test_every_readme_number_comes_from_the_aggregate_or_cost_files():
    agg = read_aggregate(PUBLISHED / "aggregate.json")
    figures = [
        Figure(name=f.name, value=v, unit=f.unit, source="aggregate.json", cell=f.cell, profile=f.profile)
        for f in agg.figures for v in (f.min, f.median, f.max)
    ]
    sample = read_metrics(SAMPLE / "metrics.json")
    extra = [SharedCost(item="cost file", usd=v, source="cost.json") for v in _cost_values()]
    extra.append(SharedCost(item="run count", usd=len(agg.runs), source="aggregate.json"))
    metrics = Metrics(run_id=sample.run_id, figures=figures, shared_costs=sample.shared_costs + extra)
    text = README.read_text()
    for run_id in agg.runs:  # run ids are names, not figures
        text = text.replace(run_id, "")
    assert check(text, metrics) == []


def test_readme_tables_are_written_from_the_aggregate():
    text = README.read_text()
    block = text.split(START, 1)[1].split(END, 1)[0].strip("\n")
    assert block == render(read_aggregate(PUBLISHED / "aggregate.json"))


def test_readme_embeds_the_four_published_charts():
    text = README.read_text()
    for name in CHARTS:
        assert re.search(rf"!\[[^\]]*\]\(results/published/{re.escape(name)}\)", text), name
        assert (PUBLISHED / name).is_file(), name


def test_readme_shows_verdict_cost_and_limits():
    text = README.read_text()
    assert re.search(r"^\| *Metric", text, re.MULTILINE)
    assert "## Verdict" in text
    assert "## Which one should I pick?" in text
    assert "## Cost of one run" in text
    limits = text.split("## Limits", 1)[1].split("\n## ", 1)[0].lower()
    for phrase in ("3 runs", "`spike`", "`scale-from-min`", "mock", "eks-fargate", "two gaps", "logged no scale-out"):
        assert phrase in limits, phrase


def test_readme_claims_no_cause_for_the_ec2_spike_latency():
    verdict = " ".join(README.read_text().split("## Verdict", 1)[1].split("\n## ", 1)[0].split())
    assert "cause is not proven" in verdict
    assert not re.search(r"\b(because|caused by|due to)\b", verdict, re.IGNORECASE)


def test_published_chart_matches_the_published_metrics():
    from scripts.make_chart import render

    assert (SAMPLE / "chart.svg").read_text() == render(read_metrics(SAMPLE / "metrics.json"))


def test_published_files_hold_no_alb_host_or_email():
    # Account ids: test_bootstrap_oidc.py scans every tracked file. Elsewhere, tests hold placeholder hosts.
    files = subprocess.run(
        ["git", "ls-files", "-z", "README.md", "results/sample", "results/published"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    bad = re.compile(rb"elb\.amazonaws\.com|[\w.+-]+@[\w-]+\.[\w.-]+")
    hits = []
    for name in filter(None, files):
        path = ROOT / name.decode()
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" not in data and bad.search(data):
            hits.append(name.decode())
    assert hits == []


def test_tables_put_units_on_row_labels_only():
    # Issue #122: the report and README table carried "(USD/h)" on every column header.
    for path in (README, SAMPLE / "report.md"):
        text = path.read_text()
        header = re.search(r"^\| *Metric *\|.*$", text, re.MULTILINE)
        assert header, path.name
        assert "(" not in header.group(0), path.name
    assert re.search(r"^## Verdict\n+\S", (SAMPLE / "report.md").read_text(), re.MULTILINE)


def test_readme_lists_the_moving_parts_of_each_cell():
    # Issue #149: the count is measured from the templates, and the names under the table show what was counted.
    from advisor.collector import moving_parts

    block = README.read_text().split(START, 1)[1].split(END, 1)[0]
    for cell in ("ecs-fargate", "ecs-ec2", "eks-ec2"):
        assert f"- {cell}: " + ", ".join(moving_parts(cell)) in block, cell
