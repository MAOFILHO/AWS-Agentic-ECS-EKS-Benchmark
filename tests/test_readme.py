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


AUTHOR_LINE = "**Marcos Oliveira** \u2014 [LinkedIn](https://www.linkedin.com/in/mfilho1/) | [GitHub](https://github.com/MAOFILHO)"
REQUIRED_SECTIONS = ("Project description", "Why this is production-grade", "The Problem", "The Solution",
                     "Results and Impact", "Limits", "Architecture", "Tech stack", "AWS services used", "AWS region", "Cost estimate",
                     "Key engineering decisions", "License", "Author")
BADGE_RE = re.compile(r"^!?\[.*?\]\(https://(?:img\.shields\.io|github\.com)/\S+?\)(?:\]\(\S+\))?$", re.MULTILINE)


def _section(title: str, level: str = "##") -> str:
    """Body of a heading, with or without a leading emoji, up to the next heading of the same level."""
    head = re.search(rf"^{level} (?:[^\w\s]+\s+)?{re.escape(title)}[ \t]*$", README.read_text(), re.MULTILINE)
    assert head, f"no '{level} {title}' heading"
    rest = README.read_text()[head.end():]
    nxt = re.search(rf"^{level} ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


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
    text = README.read_text().replace(AUTHOR_LINE, "")  # the owner's public profile line
    # Issue #171: badge rows and the divider image hold versions and ids, not measured figures. The badge
    # versions are checked against the repo files by test_readme_badges_match_the_repo_versions.
    text = BADGE_RE.sub("", text)
    text = re.sub(r"<img [^>]*>", "", text)
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
    assert "### Which one should I pick?" in text
    assert "## Cost estimate" in text
    limits = _section("Limits").lower()
    for phrase in ("3 runs", "`spike`", "`scale-from-min`", "mock", "eks-fargate", "two gaps", "logged no scale-out"):
        assert phrase in limits, phrase
    # Issue #161: eks-fargate rests on 2 runs, and its spike scale-out on 1.
    for phrase in ("2 for eks-fargate", "rest on 1 run", "different run sets", "add-on versions", "fargate sizing",
                   "no run with a real model"):
        assert phrase in limits, phrase
    assert not re.search(r"eks fargate (?:rests )?on 3 runs|3 runs for eks-fargate", limits)


def test_readme_claims_no_cause_for_the_ec2_spike_latency():
    results = _section("Results and Impact").split("### Which one should I pick?", 1)[0]
    verdict = " ".join(results.split())
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
    assert f"USD {spend['min']:g} to {spend['max']:g}" in _section("Cost estimate")
    limits = _section("Limits")
    assert FACTS["addon_date"] in " ".join(limits.split())


def test_readme_tech_stack_names_only_tools_found_in_the_repo():
    # Issue #165: each tool named in the section is read back from the repo file that uses it.
    section = _section("Tech stack")
    assert len(section.strip().splitlines()) < 25
    assert "\u2014" not in section
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


def _how_it_works() -> str:
    return _section("Architecture")


def _mermaid_nodes(block: str) -> set[str]:
    """Node ids of a Mermaid flowchart: edge labels and node shapes stripped, then split on arrows."""
    nodes = set()
    for line in block.strip().splitlines()[1:]:
        line = re.sub(r"\|[^|]*\||\[[^\]]*\]|\{[^}]*\}|\([^)]*\)", "", line.split("%%", 1)[0])
        nodes.update(part.strip() for part in re.split(r"-\.->|-->", line) if part.strip())
    return nodes


def test_readme_agent_graph_names_exactly_the_graph_nodes():
    # Issue #167: the diagram cannot drift from advisor/graph.py.
    from advisor.graph import STEPS

    section = _how_it_works()
    block = re.search(r"```mermaid\n(.*?)```", section, re.DOTALL)
    assert block, "no mermaid block in Architecture"
    nodes = {"planner", "gate", *STEPS, "teardown"}
    assert _mermaid_nodes(block.group(1)) == {"START", "END", *nodes}
    listed = re.findall(r"^\d+\. `(\w+)`", section, re.MULTILINE)
    assert listed == ["planner", "gate", *STEPS, "teardown"]
    assert "\u2014" not in section and "<" not in section
    assert not re.search(r"\b\d{12}\b|@", section)


def test_mermaid_node_parser_sees_a_missing_or_extra_node():
    block = "flowchart LR\n    START --> planner\n    planner --> gate{\"gate: y/N\"}\n    gate -->|no| END\n"
    assert _mermaid_nodes(block) == {"START", "planner", "gate", "END"}
    assert _mermaid_nodes(block.replace("planner", "plan")) != {"START", "planner", "gate", "END"}


def _locked_version(name: str) -> str:
    block = re.search(rf'^name = "{re.escape(name)}"\nversion = "([^"]+)"', (ROOT / "uv.lock").read_text(), re.MULTILINE)
    assert block, name
    return block.group(1)


def _badge_versions() -> dict[str, str]:
    """Badge name -> version text, from the shields.io badge rows between the H1 and the first heading."""
    head = README.read_text().split("\n## ", 1)[0]
    assert head.startswith("# "), "README must open with an H1"
    return dict(re.findall(r"!\[([^\]]+)\]\(https://img\.shields\.io/badge/[^-)]+-([^-)]+)-", head))


def _minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def test_readme_badges_match_the_repo_versions():
    # Issue #171: the badge row sits under the H1, and each version is read back from the file that pins it.
    badges = _badge_versions()
    for name in ("Python", "LangGraph", "FastAPI", "Pydantic", "boto3", "AWS", "Amazon EKS", "Helm",
                 "CloudFormation", "k6", "Docker", "License"):
        assert name in badges, name
    assert badges["Python"] == (ROOT / ".python-version").read_text().strip()
    assert 'requires-python = ">=3.12,<3.13"' in (ROOT / "pyproject.toml").read_text()
    assert badges["LangGraph"] == _minor(_locked_version("langgraph"))
    assert badges["FastAPI"] == _minor(_locked_version("fastapi"))
    assert badges["boto3"] == _minor(_locked_version("boto3"))
    assert badges["Pydantic"] == "v" + _locked_version("pydantic").split(".")[0]
    assert f"grafana/k6:{badges['k6']}" in (ROOT / "loadtests" / "Dockerfile").read_text()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert re.search(rf"version: v{re.escape(badges['Helm'])}\.\d+", ci)
    assert badges["License"] == "MIT" and "MIT License" in (ROOT / "LICENSE").read_text()
    assert "github.com/MAOFILHO/agentic-platform-advisor/actions/workflows/ci.yml/badge.svg" in README.read_text()
    assert re.search(r"^name: ci$", ci, re.MULTILINE)


def test_readme_has_the_portfolio_sections_in_order():
    text = README.read_text()
    positions = []
    for title in REQUIRED_SECTIONS:
        head = re.search(rf"^## (?:[^\w\s]+\s+)?{re.escape(title)}[ \t]*$", text, re.MULTILINE)
        assert head, title
        positions.append(head.start())
    assert positions == sorted(positions)
    assert "AWS services used" in text and "| Service | Purpose |" in _section("AWS services used")
    assert "| Challenge | Solution |" in _section("Key engineering decisions")
    assert text.rstrip().endswith(AUTHOR_LINE)


def test_readme_has_no_em_dash_except_the_authors_line():
    lines = [line for line in README.read_text().splitlines() if "—" in line]
    assert lines == [AUTHOR_LINE]


def test_readme_project_structure_lists_real_folders():
    block = re.search(r"```\n(.*?)```", _section("Project structure"), re.DOTALL)
    assert block
    paths = [line.split()[0] for line in block.group(1).splitlines() if line.strip()]
    assert paths
    for path in paths:
        assert (ROOT / path).is_dir(), path
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    tops = {name.split("/", 1)[0] for name in tracked if "/" in name} - {".claude", "web"}
    assert tops <= {path.split("/", 1)[0] for path in paths}, tops


def test_readme_aws_services_are_found_in_the_templates():
    cfn = "\n".join(path.read_text() for path in sorted((ROOT / "infra" / "cfn").glob("*.yaml")))
    for resource in ("AWS::ECS::Cluster", "AWS::EKS::Cluster", "AWS::EKS::FargateProfile", "AWS::EKS::Nodegroup",
                     "AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::EC2::NatGateway", "AWS::EC2::VPCEndpoint",
                     "AWS::ECR::Repository", "AWS::IAM::OIDCProvider", "AWS::EKS::PodIdentityAssociation",
                     "AWS::Budgets::Budget", "AWS::AutoScaling::AutoScalingGroup", "AWS::Logs::LogGroup",
                     "AWS::ApplicationAutoScaling::ScalableTarget", "containerInsights"):
        assert resource in cfn, resource
    assert "bedrock" in cfn.lower() and "ssm" in (ROOT / "scripts" / "deploy_eks.py").read_text()
    assert "pricing" in (ROOT / "advisor" / "pricing.py").read_text().lower()
    assert "resourcegroupstaggingapi" in (ROOT / "scripts" / "delete_run.py").read_text() + (ROOT / "advisor" / "teardown.py").read_text()


def test_readme_helm_and_cloudformation_claim_is_exact():
    # Issue #173: the EKS core add-ons come from CloudFormation, so not every Kubernetes object is Helm.
    text = " ".join(README.read_text().split())
    assert "all Kubernetes objects are Helm" not in text
    bullet = " ".join(_section("Why this is production-grade").split("\n- ", 2)[1].split())
    assert "add-ons" in bullet and "CloudFormation" in bullet and "Helm" in bullet


def test_readme_claims_no_automatic_janitor():
    # Issue #173: no janitor workflow exists in .github/workflows; the TTL tag only marks stacks.
    text = " ".join(README.read_text().split())
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if "janitor" in sentence.lower():
            assert "next step" in sentence and "docs/SPEC.md" in sentence, sentence
