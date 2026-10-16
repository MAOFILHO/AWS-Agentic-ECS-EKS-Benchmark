"""Collector node with stub boto3 clients and a fake kubectl runner: no AWS (issue #95)."""

import json
from datetime import UTC, datetime

from test_load_runner import ACCOUNT, HARNESS, SENSITIVE, Client, aws_of

from advisor import deployer as d
from advisor import rawformats as rf
from advisor.analyst import analyse
from advisor.collector import make_collector
from advisor.state import Capacity, CellSpec, RunState

ALB = "alb.example.com"
ARN = f"arn:aws:elasticloadbalancing:us-east-2:{ACCOUNT}"
WINDOWS = {"ecs-fargate": ["2026-10-14T20:05:00+00:00", "2026-10-14T20:15:00+00:00"],
           "eks-ec2": ["2026-10-14T21:50:00+00:00", "2026-10-14T22:00:00+00:00"]}
READERS = {"alb.json": rf.Alb, "events.json": list[rf.Event], "deploy.json": rf.Deploy, "config.json": rf.Config}


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
    cells = [CellSpec(name=c, profile="steady") for c in ("ecs-fargate", "eks-ec2")]
    return RunState(run_id="col-01", cells=cells, region="us-east-2", **{"load_windows": WINDOWS, **changes},
                    capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4))


def collect(tmp_path, aws=None, run=kubectl, **changes):
    for cell in ("ecs-fargate", "eks-ec2"):
        rf.write(tmp_path / "results" / "col-01" / "raw" / cell / "deploy.json", {
            "stack_create_start": "2026-10-14T19:50:00+00:00", "stack_create_end": "2026-10-14T19:53:30+00:00",
            "alb_dns_name": ALB})
    aws = aws or clients()
    out = make_collector(aws_of(**aws), "sso", tmp_path, run=run)(state(**changes))
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
    got = {(f.cell, f.name): f.value for f in metrics.figures}
    assert got[("ecs-fargate", "scale_out_s")] == 69.0  # alarm 20:10:00, steady state 20:11:09
    assert got[("eks-ec2", "scale_out_s")] == 22.0  # HPA 21:56:01, pod Ready 21:56:23
    assert got[("ecs-fargate", "cold_start_s")] == 38.0
    assert got[("ecs-fargate", "deploy_time_s")] == 660.0  # 19:50:00 to the first healthy minute, 20:01
    assert got[("eks-ec2", "moving_parts")] == 4 and got[("eks-ec2", "config_lines")] > 0


def test_config_counts_the_cells_template_lines(tmp_path):
    _, raw, _ = collect(tmp_path)
    lines = len((d.ROOT / "infra/cfn/30-ecs.yaml").read_text().splitlines())
    assert json.loads((raw / "ecs-fargate" / "config.json").read_text()) == {"config_lines": lines, "moving_parts": 4}


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
    assert len(out["gaps"]) == 2 and all("no load window" in g for g in out["gaps"])
    assert not (raw / "ecs-fargate" / "events.json").exists() and (raw / "ecs-fargate" / "config.json").is_file()


def test_a_failing_kubectl_is_a_gap(tmp_path):
    out, raw, _ = collect(tmp_path, run=lambda argv, env: (1, ""))
    assert any(g.startswith("eks-ec2/events.json") for g in out["gaps"])
    assert not (raw / "eks-ec2" / "events.json").exists()
