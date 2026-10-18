"""Load runner node with stub boto3 clients: no AWS, no sleep (issue #95)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from advisor import rawformats as rf
from advisor.graph import build_graph
from advisor.load_runner import extract_summary, make_load_runner
from advisor.state import Capacity, CellSpec, RunState

ACCOUNT = "1234" * 3  # built at runtime: tracked files must hold no account id
EMAIL = "a" + chr(64) + "b.com"  # built at runtime: tracked files must hold no email
SENSITIVE = rf.SENSITIVE
K6 = Path(__file__).parent / "fixtures" / "raw" / "real-01" / "ecs-fargate" / "k6-steady.json"
TASK = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/harness/abc123"
HARNESS = {"ClusterName": "harness", "TaskDefinitionArn": f"arn:aws:ecs:us-east-2:{ACCOUNT}:task-definition/k6:1",
           "SubnetIds": "subnet-a,subnet-b", "SecurityGroupId": "sg-1", "LogGroupName": "/harness/k6"}


class Client:
    """A boto3 client stub: any method returns its entry in `responses` (called with the kwargs if callable)."""

    def __init__(self, **responses):
        self.responses, self.calls = responses, []

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            reply = self.responses[name]
            return reply(**kwargs) if callable(reply) else reply

        return call


def aws_of(**clients):
    return lambda service, region: clients[service]


def log_pages(messages):
    def get(**kwargs):
        first = "nextToken" not in kwargs
        return {"events": [{"message": m} for m in messages] if first else [], "nextForwardToken": "t1"}

    return get


def summary_lines():
    return ["k6 starting", "K6-SUMMARY-BEGIN", *json.dumps(json.loads(K6.read_text()), indent=1).splitlines(),
            "K6-SUMMARY-END", "bye"]


def harness(exit_code=0, lines=None, running=0):
    states = iter(["RUNNING"] * running)
    task = lambda **_: {"tasks": [{"lastStatus": next(states, "STOPPED"), "stoppedReason": "Essential exited",
                                   "containers": [{"name": "k6", "exitCode": exit_code}]}]}
    ecs = Client(run_task={"tasks": [{"taskArn": TASK}], "failures": []}, describe_tasks=task, stop_task={})
    logs = Client(get_log_events=log_pages(summary_lines() if lines is None else lines))
    cfn = Client(describe_stacks={"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in HARNESS.items()]}]})
    return ecs, logs, cfn


def state(cells=("ecs-fargate",), profiles=("steady",)):
    return RunState(run_id="load-01", cells=[CellSpec(name=c, profile=p) for c in cells for p in profiles],
                    capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4), region="us-east-2")


def node(tmp_path, ecs, logs, cfn, clock=lambda: 0, **kwargs):
    for cell in ("ecs-fargate", "eks-ec2"):
        rf.write(tmp_path / "results" / "load-01" / "raw" / cell / "deploy.json", {
            "stack_create_start": "2026-10-14T19:50:00+00:00", "stack_create_end": "2026-10-14T19:53:30+00:00",
            "alb_dns_name": "alb.example.com"}, rf.DeployStarted)
    aws = aws_of(ecs=ecs, logs=logs, cloudformation=cfn)
    return make_load_runner(aws, tmp_path, 600, 50, sleep=lambda s: None, clock=clock,
                            now=lambda: datetime(2026, 10, 14, 20, 0, tzinfo=UTC), **kwargs)


def raw(tmp_path, cell="ecs-fargate"):
    return tmp_path / "results" / "load-01" / "raw" / cell


def test_run_task_gets_the_four_overrides_and_the_harness_outputs(tmp_path):
    ecs, logs, cfn = harness()
    node(tmp_path, ecs, logs, cfn)(state())
    (_, call), = [c for c in ecs.calls if c[0] == "run_task"]
    env = {e["name"]: e["value"] for e in call["overrides"]["containerOverrides"][0]["environment"]}
    assert env == {"TARGET_URL": "http://alb.example.com", "PROFILE": "steady", "PEAK_RPS": "50", "DURATION_S": "600"}
    vpc = call["networkConfiguration"]["awsvpcConfiguration"]
    assert (call["cluster"], call["launchType"], vpc["subnets"], vpc["securityGroups"]) == (
        "harness", "FARGATE", ["subnet-a", "subnet-b"], ["sg-1"])
    assert call["taskDefinition"] == HARNESS["TaskDefinitionArn"]
    assert logs.calls[0][1]["logStreamName"] == "k6/k6/abc123"


def test_summary_is_extracted_between_markers_and_passes_the_reader(tmp_path):
    ecs, logs, cfn = harness()
    out = node(tmp_path, ecs, logs, cfn)(state())
    summary = rf.read(raw(tmp_path) / "k6-steady.json", rf.K6Summary)
    assert summary.metrics.http_reqs.count == json.loads(K6.read_text())["metrics"]["http_reqs"]["count"]
    assert out["gaps"] == [] and out["status"] == "loaded"
    assert out["load_windows"] == {"ecs-fargate": ["2026-10-14T20:00:00+00:00", "2026-10-14T20:00:00+00:00"]}


def test_extract_summary_needs_both_markers_and_valid_json():
    assert extract_summary(["K6-SUMMARY-BEGIN", '{"a": 1}', "K6-SUMMARY-END"]) == {"a": 1}
    assert extract_summary(["K6-SUMMARY-BEGIN", '{"a": 1}']) is None
    assert extract_summary(["K6-SUMMARY-BEGIN", "{oops", "K6-SUMMARY-END"]) is None


def test_missing_marker_or_failed_task_is_a_gap_never_a_zero(tmp_path):
    for kwargs, why in (({"lines": ["no markers here"]}, "no k6 summary"), ({"exit_code": 99}, "exit code 99"),
                        ({"lines": ["K6-SUMMARY-BEGIN", '{"metrics": {}}', "K6-SUMMARY-END"]}, "--summary-export")):
        ecs, logs, cfn = harness(**kwargs)
        out = node(tmp_path, ecs, logs, cfn)(state())
        assert len(out["gaps"]) == 1 and why in out["gaps"][0] and out["gaps"][0].startswith("ecs-fargate/steady load")
        assert not (raw(tmp_path) / "k6-steady.json").exists()
        assert out["status"] == "loaded"  # not "failed": the graph still collects and tears down


def test_a_task_that_never_stops_is_stopped_and_becomes_a_gap(tmp_path):
    ecs, logs, cfn = harness(running=10**6)
    clock = iter(range(0, 10**6, 400))
    out = node(tmp_path, ecs, logs, cfn, clock=lambda: next(clock))(state())
    assert "still running" in out["gaps"][0] and [c[0] for c in ecs.calls].count("stop_task") == 1


def test_a_failed_cell_does_not_stop_the_next_one_and_no_id_reaches_a_gap(tmp_path):
    ecs, logs, cfn = harness()
    ecs.responses["run_task"] = lambda **kw: (_ for _ in ()).throw(RuntimeError(f"denied for {ACCOUNT} by {EMAIL}"))
    out = node(tmp_path, ecs, logs, cfn)(state(("ecs-fargate", "eks-ec2"), ("steady", "spike")))
    assert len(out["gaps"]) == 4 and set(out["load_windows"]) == {"ecs-fargate", "eks-ec2"}
    assert not any(SENSITIVE.search(g) for g in out["gaps"])


def test_graph_goes_on_to_collector_and_teardown_after_a_load_gap(tmp_path):
    ecs, logs, cfn = harness(exit_code=1)
    seen = []
    spy = lambda name: lambda s: seen.append((name, s.status)) or {}
    graph = build_graph(spy("planner"), lambda s: {"approved": True}, load_runner=node(tmp_path, ecs, logs, cfn),
                        collector=spy("collector"), teardown=spy("teardown"))
    graph.invoke(state())
    assert seen == [("planner", "planned"), ("collector", "loaded"), ("teardown", "loaded")]


def test_raw_file_and_printed_gaps_hold_no_account_id_or_email(tmp_path, capsys):
    k6 = json.loads(K6.read_text()) | {"root_group": {"name": f"arn:aws:iam::{ACCOUNT}:role/x", "owner": EMAIL}}
    ecs, logs, cfn = harness(lines=["K6-SUMMARY-BEGIN", json.dumps(k6), "K6-SUMMARY-END"])
    node(tmp_path, ecs, logs, cfn)(state())
    written = (raw(tmp_path) / "k6-steady.json").read_text()
    assert not SENSITIVE.search(written) and rf.read(raw(tmp_path) / "k6-steady.json", rf.K6Summary)
    ecs.responses["run_task"] = {"tasks": [], "failures": [{"arn": TASK, "reason": f"denied for {EMAIL}"}]}
    out = node(tmp_path, ecs, logs, cfn)(state())
    assert out["gaps"] and not SENSITIVE.search(out["gaps"][0] + capsys.readouterr().out)


def test_each_profile_window_goes_to_windows_json(tmp_path):
    ecs, logs, cfn = harness()
    ticks = iter(range(0, 60, 5))
    now = lambda: datetime(2026, 10, 14, 20, next(ticks), tzinfo=UTC)
    node(tmp_path, ecs, logs, cfn)  # writes deploy.json
    make_load_runner(aws_of(ecs=ecs, logs=logs, cloudformation=cfn), tmp_path, 600, 50, sleep=lambda s: None,
                     clock=lambda: 0, now=now)(state(profiles=("spike", "scale-from-min")))
    windows = rf.read(raw(tmp_path) / "windows.json", dict[str, rf.Window])
    assert {p: (w.start.minute, w.end.minute) for p, w in windows.items()} == {"spike": (0, 5), "scale-from-min": (10, 15)}


def test_after_cell_runs_once_per_cell_and_its_error_is_a_gap(tmp_path):
    ecs, logs, cfn = harness()
    seen = []

    def after(state, cell, start):
        seen.append((cell, start.hour))
        if cell == "eks-ec2":
            raise RuntimeError(f"kubectl denied for {EMAIL}")

    out = node(tmp_path, ecs, logs, cfn, after_cell=after)(state(("ecs-fargate", "eks-ec2"), ("steady", "spike")))
    assert seen == [("ecs-fargate", 20), ("eks-ec2", 20)]
    assert len(out["gaps"]) == 1 and out["gaps"][0].startswith("eks-ec2 after load: RuntimeError")
    assert not SENSITIVE.search(out["gaps"][0])
