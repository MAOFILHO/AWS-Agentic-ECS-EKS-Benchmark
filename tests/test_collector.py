"""Collector node with stub boto3 clients and a fake kubectl runner: no AWS (issue #95)."""

import json
import shutil
from datetime import UTC, datetime

import pytest
from test_load_runner import ACCOUNT, HARNESS, SENSITIVE, Client, aws_of

from advisor import deployer as d
from advisor import rawformats as rf
from advisor.analyst import analyse
from advisor.collector import config, make_collector, make_eks_snapshot, moving_parts
from advisor.cost import CellUsage, SharedUsage
from advisor.planner import PlanConfig
from advisor.state import Capacity, CellSpec, RunState

ALB = "alb.example.com"
ARN = f"arn:aws:elasticloadbalancing:us-east-2:{ACCOUNT}"
WINDOWS = {"ecs-fargate": ["2026-10-14T20:05:00+00:00", "2026-10-14T20:15:00+00:00"],
           "eks-ec2": ["2026-10-14T21:50:00+00:00", "2026-10-14T22:00:00+00:00"]}
READERS = {"windows.json": dict[str, rf.Window], "alb.json": rf.Alb, "events.json": list[rf.Event], "deploy.json": rf.Deploy, "config.json": rf.Config,
           "cost.json": rf.Cost, "shared-cost.json": list[rf.SharedCostLine]}
PRICES = {"eks_cluster_hour": 0.10, "fargate_vcpu_hour": 0.04, "fargate_gb_hour": 0.004, "ec2_instance_hour": 0.0416,
          "alb_hour": 0.0225, "alb_lcu_hour": 0.008, "cw_ingest_gb": 0.5, "nat_hour": 0.045, "nat_gb": 0.045,
          "vpce_hour": 0.01}
PLAN = PlanConfig(instance_type="t3.medium", duration_s=600, cell=CellUsage(replicas=2, nodes=1, lcus=1, log_gb=0.1),
                  shared=SharedUsage(nat_gb=1, endpoints=5, harness_vcpu=1, harness_memory_mib=2048, harness_tasks=1))


def at(h, m, s=0):
    return datetime(2026, 10, 14, h, m, s, tzinfo=UTC)


def outputs(**pairs):
    return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in pairs.items()]}]}


def stacks(StackName):
    return outputs(**{"col-01-ef-ecs-fargate": {"ClusterName": "ecs-c", "ServiceName": "svc"},
                      "col-01-ke-eks-ec2": {"ClusterName": "eks-c"}}.get(StackName, HARNESS))


def metric_data(MetricDataQueries, **_):
    points = {"up": ([at(20, 0), at(20, 1)], [0.0, 1.0])}
    return {"MetricDataResults": [
        {"Id": q["Id"], "Timestamps": points.get(q["Id"], ([at(20, 5)], [12.5]))[0],
         "Values": points.get(q["Id"], ([at(20, 5)], [12.5]))[1]}
        for q in MetricDataQueries if q.get("ReturnData", True)]}


def clients():
    history = [{"AlarmName": "TargetTracking-service/ecs-c/svc-AlarmHigh-1", "Timestamp": at(20, 10),
                "HistorySummary": "Alarm updated from OK to ALARM"},
               {"AlarmName": "other-alarm", "Timestamp": at(20, 9), "HistorySummary": "Alarm updated from OK to ALARM"}]
    events = [{"createdAt": at(20, 10, 31), "message": f"(service svc) has started 1 tasks: (task t) {ARN}:targetgroup/x"},
              {"createdAt": at(20, 11, 9), "message": "(service svc) has reached a steady state."},
              {"createdAt": at(19, 50), "message": "(service svc) has started 1 tasks: (task old)."}]
    return dict(
        cloudformation=Client(describe_stacks=stacks),
        cloudwatch=Client(get_metric_data=metric_data, describe_alarm_history={"AlarmHistoryItems": history}),
        ecs=Client(describe_services={"services": [{"events": events}]}),
        elbv2=Client(describe_load_balancers={"LoadBalancers": [{"DNSName": ALB, "LoadBalancerArn": f"{ARN}:loadbalancer/app/x/1"}]},
                     describe_target_groups={"TargetGroups": [{"TargetGroupArn": f"{ARN}:targetgroup/tg/2"}]}),
        **{"application-autoscaling": Client(describe_scaling_activities={"ScalingActivities": [
            {"StartTime": at(20, 10, 5), "Description": "Setting desired count to 2"}]})})


def kubectl(argv, env):
    if argv[0] == "aws":
        return 0, ""
    if "events" in argv:
        item = lambda kind, reason, when, msg: {"involvedObject": {"kind": kind}, "reason": reason,
                                                 "lastTimestamp": when, "message": msg}
        return 0, json.dumps({"items": [
            item("HorizontalPodAutoscaler", "SuccessfulRescale", "2026-10-14T21:56:01Z", "New size: 2; cpu above target a@b.com"),
            item("Pod", "Scheduled", "2026-10-14T21:56:20Z", "Successfully assigned default/workload-x"),
            item("Pod", "Scheduled", "2026-10-14T21:00:00Z", "before the load")]})
    ready = {"type": "Ready", "status": "True", "lastTransitionTime": "2026-10-14T21:56:23Z"}
    return 0, json.dumps({"items": [{"metadata": {"name": "workload-x"}, "status": {"conditions": [ready]}}]})


def state(**changes):
    cells = [CellSpec(name=c, profile="spike") for c in ("ecs-fargate", "eks-ec2")]
    return RunState(run_id="col-01", cells=cells, region="us-east-2", **{"load_windows": WINDOWS, "prices": PRICES, **changes},
                    capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4))


def collect(tmp_path, aws=None, run=kubectl, sleeps=None, **changes):
    for cell in ("ecs-fargate", "eks-ec2"):
        rf.write(tmp_path / "results" / "col-01" / "raw" / cell / "deploy.json", {
            "stack_create_start": "2026-10-14T19:50:00+00:00", "stack_create_end": "2026-10-14T19:53:30+00:00",
            "alb_dns_name": ALB}, rf.DeployStarted)
        start, end = WINDOWS[cell]
        rf.write(tmp_path / "results" / "col-01" / "raw" / cell / "windows.json",
                 {"spike": {"start": start, "end": end}}, dict[str, rf.Window])
    aws = aws or clients()
    out = make_collector(aws_of(**aws), "sso", tmp_path, run=run,
                         sleep=[].append if sleeps is None else sleeps.append, plan=PLAN)(state(**changes))
    return out, tmp_path / "results" / "col-01" / "raw", aws


def test_every_raw_file_passes_its_reader(tmp_path):
    out, raw, _ = collect(tmp_path)
    assert out["gaps"] == [] and out["status"] == "collected"
    files = sorted(p for p in raw.rglob("*.json"))
    assert {p.name for p in files} >= {"alb.json", "events.json", "deploy.json", "config.json", "cloudwatch-harness-cpu.json",
                                         "cloudwatch-cpu.json", "cloudwatch-memory.json"}
    for path in files:
        rf.read(path, READERS.get(path.name, list[rf.Point]))


def test_analyst_yields_the_scale_out_time_for_both_fixtures(tmp_path):
    _, raw, _ = collect(tmp_path)
    metrics = analyse("col-01", raw, state().cells)
    got = {(f.cell, f.name): f.value for f in metrics.figures if f.profile in (None, "spike")}
    assert got[("ecs-fargate", "scale_out_s")] == 69.0  # alarm 20:10:00, steady state 20:11:09
    assert got[("eks-ec2", "scale_out_s")] == 22.0  # HPA 21:56:01, pod Ready 21:56:23
    assert got[("ecs-fargate", "cold_start_s")] == 38.0
    assert got[("ecs-fargate", "deploy_time_s")] == 660.0  # 19:50:00 to the first healthy minute, 20:01
    assert got[("eks-ec2", "moving_parts")] == len(moving_parts("eks-ec2")) and got[("eks-ec2", "config_lines")] > 0


def test_config_counts_the_cells_template_lines(tmp_path):
    _, raw, _ = collect(tmp_path)
    lines = len((d.ROOT / "infra/cfn/30-ecs.yaml").read_text().splitlines())
    assert json.loads((raw / "ecs-fargate" / "config.json").read_text()) == {
        "config_lines": lines, "moving_parts": 2, "moving_parts_list": ["LoadBalancer", "ScalableTarget"]}


def test_ecs_ec2_counts_its_capacity_parts_and_eks_counts_each_add_on():
    assert moving_parts("ecs-ec2") == ["LoadBalancer", "LaunchTemplate", "AutoScalingGroup", "CapacityProvider",
                                       "ScalableTarget"]
    assert len(moving_parts("ecs-ec2")) > len(moving_parts("ecs-fargate"))
    assert moving_parts("eks-ec2") == ["vpc-cni", "kube-proxy", "coredns", "metrics-server", "eks-pod-identity-agent",
                                       "NodeGroup", "LbcPodIdentity", "aws-load-balancer-controller"]
    assert "NodeGroup" not in moving_parts("eks-fargate")


def test_eks_fargate_counts_its_fargate_profile_and_irsa_provider_in_place_of_node_group_and_pod_identity():
    # Issue #153: the Fargate profile places pods and the OIDC provider gives the controller its role.
    assert moving_parts("eks-fargate") == ["vpc-cni", "kube-proxy", "coredns", "metrics-server", "eks-pod-identity-agent",
                                           "FargateProfile", "LbcOidcProvider", "aws-load-balancer-controller"]


@pytest.mark.parametrize("cell", ["ecs-fargate", "ecs-ec2", "eks-ec2", "eks-fargate"])
def test_moving_parts_equals_the_length_of_the_recorded_list(cell):
    got = config(cell)
    assert got["moving_parts"] == len(got["moving_parts_list"]) > 0


def test_moving_parts_are_read_from_the_template_files(tmp_path):
    shutil.copytree(d.ROOT / "infra", tmp_path / "infra")
    eks = tmp_path / "infra/cfn/40-eks.yaml"
    text = eks.read_text().replace("\n  # eks-ec2 only.", "\n  ExtraAddon:\n    Type: AWS::EKS::Addon\n    Properties:\n"
                                   "      AddonName: aws-ebs-csi-driver\n\n  # eks-ec2 only.", 1)
    eks.write_text(text)
    got = moving_parts("eks-ec2", tmp_path)
    assert len(got) == len(moving_parts("eks-ec2")) + 1 and "aws-ebs-csi-driver" in got


def test_the_helm_controller_is_the_one_deploy_eks_installs():
    from scripts.deploy_eks import LBC_RELEASE

    assert LBC_RELEASE in moving_parts("eks-ec2")


def test_ec2_cell_config_records_the_instance_type(tmp_path):
    _, raw, _ = collect(tmp_path)
    assert json.loads((raw / "eks-ec2" / "config.json").read_text())["instance_type"] == PLAN.instance_type
    assert "instance_type" not in json.loads((raw / "ecs-fargate" / "config.json").read_text())


def test_no_file_holds_an_account_id_or_an_email(tmp_path):
    _, raw, _ = collect(tmp_path)
    text = "".join(p.read_text() for p in raw.rglob("*.json"))
    assert "targetgroup/x" in text and not SENSITIVE.search(text)


def test_the_collector_only_reads(tmp_path):
    _, _, aws = collect(tmp_path)
    called = {name for client in aws.values() for name, _ in client.calls}
    assert called and all(n.startswith(("describe_", "get_")) for n in called)


def test_a_failed_section_is_a_gap_and_the_others_still_run(tmp_path):
    aws = clients()
    aws["elbv2"].responses["describe_load_balancers"] = lambda **_: (_ for _ in ()).throw(
        RuntimeError(f"AccessDenied {ACCOUNT}"))
    out, raw, _ = collect(tmp_path, aws)
    assert [g.split(":")[0] for g in out["gaps"]] == ["ecs-fargate/alb.json + deploy.json", "eks-ec2/alb.json + deploy.json"]
    assert not any(SENSITIVE.search(g) for g in out["gaps"])
    assert not (raw / "ecs-fargate" / "alb.json").exists() and (raw / "ecs-fargate" / "events.json").is_file()
    assert "target_healthy" not in json.loads((raw / "ecs-fargate" / "deploy.json").read_text())


def test_a_cell_without_a_load_window_is_a_gap_not_empty_files(tmp_path):
    out, raw, _ = collect(tmp_path, load_windows={})
    assert len(out["gaps"]) == 3 and all("no load window" in g for g in out["gaps"])
    assert not (raw / "ecs-fargate" / "events.json").exists() and (raw / "ecs-fargate" / "config.json").is_file()


def test_a_failing_kubectl_is_a_gap(tmp_path):
    out, raw, _ = collect(tmp_path, run=lambda argv, env: (1, ""))
    assert any(g.startswith("eks-ec2/events.json") for g in out["gaps"])
    assert not (raw / "eks-ec2" / "events.json").exists()


def empty_series(*ids):
    """A get_metric_data stub that returns no datapoints for the given query ids."""
    def stub(MetricDataQueries, **_):
        results = metric_data(MetricDataQueries)["MetricDataResults"]
        return {"MetricDataResults": [{**r, "Timestamps": [], "Values": []} if r["Id"] in ids else r for r in results]}
    return stub


def test_an_empty_series_is_a_gap_and_no_file_not_a_zero(tmp_path):
    aws = clients()
    aws["cloudwatch"].responses["get_metric_data"] = empty_series("cpu", "memory", "count", "time")
    out, raw, _ = collect(tmp_path, aws)
    names = {g.split(":")[0] for g in out["gaps"]}
    assert {"ecs-fargate/cloudwatch-cpu/memory", "ecs-fargate/cloudwatch-harness-cpu.json",
            "ecs-fargate/alb.json + deploy.json", "eks-ec2/cloudwatch-harness-cpu.json"} <= names
    for cell in ("ecs-fargate", "eks-ec2"):
        assert not (raw / cell / "cloudwatch-harness-cpu.json").exists() and not (raw / cell / "alb.json").exists()
    assert not (raw / "ecs-fargate" / "cloudwatch-cpu.json").exists()


def test_no_5xx_datapoints_is_a_real_zero_when_requests_were_counted(tmp_path):
    aws = clients()
    aws["cloudwatch"].responses["get_metric_data"] = empty_series("e5xx")
    out, raw, _ = collect(tmp_path, aws)
    assert out["gaps"] == [] and json.loads((raw / "ecs-fargate" / "alb.json").read_text())["http_5xx"] == []


def test_harness_cpu_waits_for_the_metric_lag_then_writes_the_file(tmp_path):
    aws, calls = clients(), []
    def lagging(MetricDataQueries, **kw):
        harness = any(q["Id"] == "reserved" for q in MetricDataQueries)
        calls.append(harness)
        stub = empty_series("cpu") if harness and calls.count(True) % 3 else metric_data
        return stub(MetricDataQueries)
    aws["cloudwatch"].responses["get_metric_data"] = lagging
    sleeps = []
    out, raw, _ = collect(tmp_path, aws, sleeps=sleeps)
    assert out["gaps"] == [] and (raw / "ecs-fargate" / "cloudwatch-harness-cpu.json").is_file()
    assert sleeps == [60, 60, 60, 60]  # two empty reads for each of the two cells


def test_a_harness_cpu_series_that_stays_empty_is_one_gap_after_the_waits(tmp_path):
    aws = clients()
    harness_only = lambda MetricDataQueries, **kw: (empty_series("cpu") if any(q["Id"] == "reserved" for q in MetricDataQueries)
                                                    else metric_data)(MetricDataQueries)
    aws["cloudwatch"].responses["get_metric_data"] = harness_only
    sleeps = []
    out, raw, _ = collect(tmp_path, aws, sleeps=sleeps)
    assert [g.split(":")[0] for g in out["gaps"] if "harness-cpu" in g] == [
        "ecs-fargate/cloudwatch-harness-cpu.json", "eks-ec2/cloudwatch-harness-cpu.json"]
    assert (raw / "ecs-fargate" / "alb.json").is_file() and len(sleeps) == 4


def cost_of(raw, cell):
    return json.loads((raw / cell / "cost.json").read_text())


def test_cost_json_for_an_ecs_and_an_eks_cell(tmp_path):
    out, raw, _ = collect(tmp_path)
    assert out["gaps"] == []
    # 10 min load: 2 replicas x (0.25 vCPU x 0.04 + 0.5 GB x 0.004) + ALB hour + 1 LCU, over 1/6 h, + 0.1 GB logs x 0.5.
    ecs = cost_of(raw, "ecs-fargate")
    assert ecs["load_usd"] == pytest.approx((2 * (0.25 * 0.04 + 0.5 * 0.004) + 0.0225 + 0.008) / 6 + 0.05)
    assert ecs["idle_usd_per_hour"] == pytest.approx(0.25 * 0.04 + 0.5 * 0.004 + 0.0225)  # min_replicas=1, no LCU, no logs
    eks = cost_of(raw, "eks-ec2")
    assert eks["idle_usd_per_hour"] == pytest.approx(0.10 + 0.0416 + 0.0225)  # control plane + 1 node + ALB
    assert eks["load_usd"] == pytest.approx((0.10 + 0.0416 + 0.0225 + 0.008) / 6 + 0.05)


@pytest.mark.parametrize("key", ["eks_cluster_hour", "alb_hour"])
def test_a_missing_price_leaves_out_cost_json_and_is_a_gap_not_zero(tmp_path, key):
    out, raw, _ = collect(tmp_path, prices={k: v for k, v in PRICES.items() if k != key})
    assert not (raw / "eks-ec2" / "cost.json").exists()
    assert any(g.startswith("eks-ec2/cost.json") and key in g for g in out["gaps"])
    metrics = analyse("col-01", raw, state().cells)
    assert not [f for f in metrics.figures if f.cell == "eks-ec2" and "usd" in f.unit]
    assert "eks-ec2/cost.json: missing" in metrics.gaps


def test_analyst_makes_cost_figures_from_the_collected_files(tmp_path):
    _, raw, _ = collect(tmp_path)
    for cell in ("ecs-fargate", "eks-ec2"):
        k6 = {"metrics": {"http_reqs": {"count": 1000}, "http_req_failed": {"passes": 0, "fails": 1000}}}
        (raw / cell / "k6-spike.json").write_text(json.dumps(k6))
    metrics = analyse("col-01", raw, state().cells)
    got = {(f.cell, f.name): f.value for f in metrics.figures}
    for cell in ("ecs-fargate", "eks-ec2"):
        assert got[(cell, "idle_usd_per_hour")] == pytest.approx(cost_of(raw, cell)["idle_usd_per_hour"])
        assert got[(cell, "usd_per_1k_requests")] == pytest.approx(cost_of(raw, cell)["load_usd"])  # 1000 requests
    assert [c.item for c in metrics.shared_costs][:1] == ["shared nat gateway"]


def test_shared_costs_are_their_own_file_and_never_in_a_cell(tmp_path):
    _, raw, _ = collect(tmp_path)
    shared = json.loads((raw / "shared-cost.json").read_text())
    nat = next(line["usd"] for line in shared if line["item"] == "shared nat gateway")
    assert nat == pytest.approx(0.045 * 20 / 60)  # two 10 min load windows
    assert {line["item"] for line in shared} >= {"shared vpc endpoints", "shared harness vcpu"}
    ecs_cell_only = (2 * (0.25 * 0.04 + 0.5 * 0.004) + 0.0225 + 0.008) / 6 + 0.05
    assert cost_of(raw, "ecs-fargate")["load_usd"] == pytest.approx(ecs_cell_only)


def test_the_eks_snapshot_writes_events_before_they_expire_and_the_collector_keeps_it(tmp_path):
    calls = []
    run = lambda argv, env: calls.append(argv[0]) or kubectl(argv, env)
    snapshot = make_eks_snapshot(aws_of(**clients()), "sso", tmp_path, run=run)
    snapshot(state(), "ecs-fargate", at(20, 5))  # an ECS cell keeps its events in AWS: nothing to snapshot
    assert calls == []
    snapshot(state(), "eks-ec2", at(21, 50))
    raw = tmp_path / "results" / "col-01" / "raw" / "eks-ec2"
    taken = rf.read(raw / "events.json", list[rf.Event])
    assert [e.kind for e in taken] == ["alarm", "scheduled", "healthy"]
    calls.clear()
    out, _, _ = collect(tmp_path, run=run)
    assert calls == [] and out["gaps"] == []  # no second kubectl read over the snapshot
    assert rf.read(raw / "events.json", list[rf.Event]) == taken
