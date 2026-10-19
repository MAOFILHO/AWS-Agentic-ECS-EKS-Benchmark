"""A full run of all four cells with fakes: plan, collect, report and chart all carry eks-fargate (issue #153). No AWS."""

import json
import re
import xml.etree.ElementTree as ET

from test_collector import ALB, PLAN, PRICES, clients, kubectl
from test_cost import StubPricing
from test_load_runner import HARNESS, Client, aws_of
from test_reporter_tables import StubLLM, tables

from advisor import rawformats as rf
from advisor.analyst import analyse
from advisor.collector import make_collector
from advisor.planner import make_planner
from advisor.reporter import make_reporter
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs
from scripts.aggregate_runs import aggregate
from scripts.make_chart import render_questions

CELLS = ("ecs-fargate", "ecs-ec2", "eks-ec2", "eks-fargate")
PROFILES = ("spike", "scale-from-min")
SUFFIX = {"ecs-fargate": "ef", "ecs-ec2": "ee", "eks-ec2": "ke", "eks-fargate": "kf"}
# The fakes put ECS scaling events at 20:10 and EKS ones at 21:56, so each spike window holds its platform's.
SPIKE = {"ecs": ("2026-10-14T20:05:00+00:00", "2026-10-14T20:15:00+00:00"),
         "eks": ("2026-10-14T21:50:00+00:00", "2026-10-14T22:00:00+00:00")}
CAPACITY = Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4)


def state(region="us-east-2"):
    windows = {c: list(SPIKE[c[:3]]) for c in CELLS}
    return RunState(run_id="four-01", cells=[CellSpec(name=c, profile=p) for c in CELLS for p in PROFILES],
                    region=region, capacity=CAPACITY, prices=PRICES, load_windows=windows)


def stacks(StackName):
    cell_stacks = {f"four-01-{SUFFIX[c]}-{c}": {"ClusterName": f"{c}-c", **({"ServiceName": "svc"} if c[:3] == "ecs" else {})}
                   for c in CELLS}
    return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v}
                                    for k, v in cell_stacks.get(StackName, HARNESS).items()]}]}


def run_four_cells(tmp_path):
    raw = create_run_dirs("four-01", tmp_path).raw
    for cell in CELLS:
        rf.write(raw / cell / "deploy.json", {"stack_create_start": "2026-10-14T19:50:00+00:00",
                                              "stack_create_end": "2026-10-14T19:53:30+00:00", "alb_dns_name": ALB},
                 rf.DeployStarted)
        start, end = SPIKE[cell[:3]]
        rf.write(raw / cell / "windows.json", {"spike": {"start": start, "end": end},
                                               "scale-from-min": {"start": "2026-10-14T23:00:00+00:00",
                                                                  "end": "2026-10-14T23:10:00+00:00"}},
                 dict[str, rf.Window])
    aws = {**clients(), "cloudformation": Client(describe_stacks=stacks)}
    out = make_collector(aws_of(**aws), "sso", tmp_path, run=kubectl, sleep=[].append, plan=PLAN)(state())
    for cell in CELLS:
        for profile in PROFILES:
            k6 = {"metrics": {"http_req_duration": {"med": 40.0, "p(95)": 90.0, "p(99)": 120.0},
                              "http_reqs": {"count": 1000}, "http_req_failed": {"passes": 0, "fails": 1000}}}
            (raw / cell / f"k6-{profile}.json").write_text(json.dumps(k6))
    return out, analyse("four-01", raw, state().cells)


def test_the_plan_prices_every_cell_and_profile_with_eks_fargate_lines():
    got = make_planner(StubPricing(), PLAN)(state(region="us-east-1"))
    assert [f"{c.name}/{c.profile}" for c in got["cells"]] == [f"{c}/{p}" for c in CELLS for p in PROFILES]
    items = [line.item for line in got["estimate_lines"]]
    for profile in PROFILES:
        for part in ("eks control plane", "fargate vcpu", "fargate memory", "alb"):
            assert f"eks-fargate/{profile} {part}" in items
    assert {"shared nat gateway", "shared vpc endpoints"} <= set(items)
    assert got["estimate_usd"] > 0 and got["gaps"] == []


def test_collect_report_and_chart_show_eks_fargate_in_every_table_and_chart(tmp_path):
    out, metrics = run_four_cells(tmp_path)
    assert not [g for g in out["gaps"] if g.startswith("eks-fargate")]
    names = {f.name for f in metrics.figures if f.cell == "eks-fargate"}
    assert {"p50_ms", "error_rate", "deploy_time_s", "moving_parts", "idle_usd_per_hour", "usd_per_1k_requests"} <= names

    paths = create_run_dirs("four-01", tmp_path)
    paths.metrics.write_text(metrics.model_dump_json())
    make_reporter(StubLLM(["## Verdict\nNo cell had errors.\n"] * 3), tmp_path)(state())
    found = tables(paths.report.read_text())
    assert found
    for title, rows in found.items():
        for label, by_cell in rows.items():
            assert list(by_cell) == list(CELLS), (title, label)

    charts = render_questions(aggregate([metrics]))
    assert len(charts) == 4
    for name, svg in charts.items():
        bars = {r.get("data-cell") for r in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}rect") if r.get("data-cell")}
        assert "eks-fargate" in bars, name
        assert re.search(r">eks-fargate<", svg), name  # the legend names it
