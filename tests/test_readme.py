"""README.md and the published sample run (issue #118).

Every number in README.md must come from results/sample/metrics.json or the sample's cost files.
A sum of cost-file values counts as from the cost files: the "cost of this run" line is one.
"""

import json
import re
import subprocess
from pathlib import Path

from advisor.numcheck import check
from advisor.schemas import SharedCost, read_metrics

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SAMPLE = ROOT / "results" / "sample"


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


def test_every_readme_number_comes_from_the_sample_run():
    metrics = read_metrics(SAMPLE / "metrics.json")
    extra = [SharedCost(item="cost file", usd=v, source="cost.json") for v in _cost_values()]
    metrics = metrics.model_copy(update={"shared_costs": metrics.shared_costs + extra})
    assert check(README.read_text(), metrics) == []


def test_readme_shows_chart_table_verdict_cost_and_limits():
    text = README.read_text()
    assert "](results/sample/chart.svg)" in text
    assert re.search(r"^\| *Metric", text, re.MULTILINE)
    assert "## Verdict" in text
    assert "## Cost of this run" in text
    limits = text.split("## Limits", 1)[1].lower()
    for phrase in ("mock", "steady", "three cells", "one run", "eks-fargate"):
        assert phrase in limits, phrase


def test_published_files_hold_no_alb_host_or_email():
    # Account ids: test_bootstrap_oidc.py scans every tracked file. Elsewhere, tests hold placeholder hosts.
    files = subprocess.run(
        ["git", "ls-files", "-z", "README.md", "results/sample"], cwd=ROOT, capture_output=True, check=True
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
