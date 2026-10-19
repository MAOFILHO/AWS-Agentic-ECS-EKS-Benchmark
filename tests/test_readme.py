"""README.md, the published 5-run results and the sample run (issues #118, #144 and #161).

Every number in README.md must come from results/published/aggregate.json (a min, median or max),
the run count, or the sample's cost files. A sum of cost-file values counts as from the cost files:
the "cost of one run" line is one.
"""

import json
import re
import subprocess
from pathlib import Path

from advisor.numcheck import check, check_scoped
from advisor.schemas import Figure, Metrics, SharedCost, read_aggregate, read_metrics
from scripts.make_readme_tables import END, START, render

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SAMPLE = ROOT / "results" / "sample"
PUBLISHED = ROOT / "results" / "published"
CROSS_CHECK_RUNS = ("kf4",)  # named in the README as an unaggregated cross-check
# Issue #163: README facts with no run file in the aggregate: the kf4 cross-check figures, the real spend of
# one Fargate run and the add-on version date.
FACTS = json.loads((PUBLISHED / "readme-facts.json").read_text())
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


def _readme_metrics_and_text() -> tuple[Metrics, str]:
    agg = read_aggregate(PUBLISHED / "aggregate.json")
    figures = [
        Figure(name=f.name, value=v, unit=f.unit, source="aggregate.json", cell=f.cell, profile=f.profile)
        for f in agg.figures for v in (f.min, f.median, f.max)
    ]
    sample = read_metrics(SAMPLE / "metrics.json")
    extra = [SharedCost(item="cost file", usd=v, source="cost.json") for v in _cost_values()]
    extra.append(SharedCost(item="run count", usd=len(agg.runs), source="aggregate.json"))
    # Issue #159: the Runs row shows n per cell, which can be under the run count.
    extra += [SharedCost(item="runs per cell", usd=n, source="aggregate.json") for n in sorted({f.n for f in agg.figures})]
    figures += [Figure(name=name, value=v, unit="s", source="kf4", cell=cell, profile="spike")
                for cell, named in FACTS["kf4"].items() for name, v in named.items()]
    extra += [SharedCost(item="Fargate run spend", usd=v, source="readme-facts.json")
              for v in FACTS["fargate_run_usd"].values()]
    extra += [SharedCost(item="add-on date", usd=int(part), source="readme-facts.json")
              for part in FACTS["addon_date"].split() if part.isdigit()]
    metrics = Metrics(run_id=sample.run_id, figures=figures, shared_costs=sample.shared_costs + extra)
    text = README.read_text()
    for run_id in [*agg.runs, *CROSS_CHECK_RUNS]:  # run ids are names, not figures
        text = text.replace(run_id, "")
    return metrics, text


def test_every_readme_number_comes_from_the_aggregate_or_cost_files():
    metrics, text = _readme_metrics_and_text()
    assert check(text, metrics) == []


def test_every_readme_number_matches_the_cell_and_metric_it_sits_with():
    metrics, text = _readme_metrics_and_text()
    assert check_scoped(text, metrics) == []


def test_swapping_two_real_readme_figures_fails_the_number_check():
    metrics, text = _readme_metrics_and_text()
    # Table: the p50 of ecs-ec2 and eks-ec2 in `spike` trade places. Both are real figures.
    row = "| p50 latency (ms) | 3.87 (3.67 to 4.33) | 5412 (5406 to 6210) | 3.66 (3.18 to 4.14) | 5.79 (5.74 to 5.88) |"
    swapped = "| p50 latency (ms) | 3.87 (3.67 to 4.33) | 5.79 (5.74 to 5.88) | 3.66 (3.18 to 4.14) | 5412 (5406 to 6210) |"
    assert row in text
    swapped_text = text.replace(row, swapped)
    assert check(swapped_text, metrics) == []  # the pooled check cannot see it
    assert check_scoped(swapped_text, metrics) != []
    # Prose: the idle cost of ECS Fargate quoted for a metric it is not (deploy time 402 s -> 0.0348).
    prose = "ECS Fargate deployed in 402 s"
    assert prose in text
    assert check_scoped(text.replace(prose, "ECS Fargate deployed in 5412 s"), metrics) != []
    assert check_scoped(text.replace(prose, "ECS Fargate deployed in 0.0348 s"), metrics) != []
    # A figure of another cell in a sentence that names one cell.
    other = "EKS EC2 kept p50 at 5.79 ms"
    assert other in text
    assert check_scoped(text.replace(other, "EKS EC2 kept p50 at 4.08 ms"), metrics) != []


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
    # Issue #161: eks-fargate rests on 2 runs, and its spike scale-out on 1.
    for phrase in ("2 for eks-fargate", "rest on 1 run", "different run sets", "add-on versions", "fargate sizing",
                   "no run with a real model"):
        assert phrase in limits, phrase
    assert not re.search(r"eks fargate (?:rests )?on 3 runs|3 runs for eks-fargate", limits)


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
    for cell in ("ecs-fargate", "ecs-ec2", "eks-fargate", "eks-ec2"):
        assert f"- {cell}: " + ", ".join(moving_parts(cell)) in block, cell


def test_sample_report_verdict_still_passes_the_ranking_checks():
    from advisor.reporter import contradicted_rankings, unsupported_claims, verdict_of

    metrics = read_metrics(SAMPLE / "metrics.json")
    verdict = verdict_of((SAMPLE / "report.md").read_text().split("## Verdict", 1)[1].split("\n## ", 1)[0])
    assert contradicted_rankings(verdict, metrics) == []
    assert unsupported_claims(verdict, metrics) == []



def test_kf4_cross_check_quotes_its_two_eks_fargate_figures():
    # Issue #163: kf4 is quoted with its figures, not "close to" kf5.
    kf4 = FACTS["kf4"]["eks-fargate"]
    bullet = " ".join(README.read_text().split("**Cross-check:**", 1)[1].split("\n\n", 1)[0].split())
    for value in (kf4["scale_out_s"], kf4["cold_start_s"]):
        assert re.search(rf"(?<![\d.]){re.escape(f'{value:g}')} s\b", bullet), value
    assert "2 observations, not a measured spread" in bullet


def test_readme_reads_fargate_spend_and_addon_date_from_their_source():
    spend = FACTS["fargate_run_usd"]
    assert f"USD {spend['min']:g} to {spend['max']:g}" in README.read_text().split("## Cost of one run", 1)[1]
    limits = README.read_text().split("## Limits", 1)[1].split("\n## ", 1)[0]
    assert FACTS["addon_date"] in " ".join(limits.split())


def test_readme_tech_stack_names_only_tools_found_in_the_repo():
    # Issue #165: each tool named in the section is read back from the repo file that uses it.
    section = README.read_text().split("## Tech stack", 1)[1].split("\n## ", 1)[0]
    assert len(section.strip().splitlines()) < 25
    assert "—" not in section
    assert not re.search(r"\b\d{12}\b|@", section)
    pyproject = (ROOT / "pyproject.toml").read_text()
    for dep in ("langgraph", "boto3", "fastapi", "uvicorn", "pydantic", "pytest", "httpx", "ruff"):
        assert dep in pyproject and dep in section.lower(), dep
    assert list((ROOT / "loadtests").glob("*.js")) and "k6" in section
    assert (ROOT / "infra" / "helm" / "workload" / "Chart.yaml").is_file() and "Helm" in section
    assert list((ROOT / "infra" / "cfn").glob("*.yaml")) and "CloudFormation" in section
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for tool in ("cfn-lint", "cfn-guard", "helm lint"):
        assert tool in ci and tool in section, tool
    assert "amazon.nova-micro" in (ROOT / "advisor" / "reporter.py").read_text() and "Bedrock" in section
