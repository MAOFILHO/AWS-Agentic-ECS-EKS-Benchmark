"""Deployer node with a fake subprocess runner: no AWS, no real command (issue #85)."""

import json
import re
from datetime import UTC, datetime

from langgraph.checkpoint.memory import InMemorySaver

from advisor import cli
from advisor.deployer import make_deployer
from advisor.gate import make_gate
from advisor.graph import build_graph
from advisor.planner import DEFAULT_CELLS, PROFILES
from advisor.state import Capacity, CellSpec, RunState
from advisor.teardown import make_teardown

ACCOUNT = "1234" * 3  # built at runtime: tracked files must hold no account id
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
IMAGE = f"{ACCOUNT}.dkr.ecr.us-east-2.amazonaws.com/triage-workload:abc1234"
K6 = f"{ACCOUNT}.dkr.ecr.us-east-2.amazonaws.com/triage-workload:k6-abc1234"
REGION = "us-east-2"
INSTANCE = json.loads(cli.RUN_CONFIG.read_text())["plan"]["instance_type"]


def state(cells=DEFAULT_CELLS):
    return RunState(run_id="dep-01", cells=[CellSpec(name=c, profile=p) for c in cells for p in PROFILES],
                    capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4), region=REGION)


class FakeRunner:
    """Records every command; `fail` picks the commands that exit 1, `output` what they print."""

    def __init__(self, fail=lambda argv: False, output=lambda argv: ""):
        self.calls, self.fail, self.output = [], fail, output

    def __call__(self, argv, env):
        self.calls.append((argv, env))
        return (1 if self.fail(argv) else 0), self.output(argv)

    def scripts(self):
        return [next(a for a in argv if a.startswith(("scripts", "cloudformation"))) for argv, _ in self.calls]


def alb_output(argv):
    if "scripts.deploy_eks" in argv:
        return f"stack done for {ACCOUNT}\nALB: http://k8s-workload-abc.{REGION}.elb.amazonaws.com\n"
    if "describe-stacks" in argv:
        return f"run-alb-1.{REGION}.elb.amazonaws.com\n"
    return f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/x: done.\n"


def test_default_matrix_deploys_ecs_fargate_then_eks_ec2_once_each(tmp_path):
    run = FakeRunner()
    assert make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run, instance_type=INSTANCE)(state()) == {"status": "deployed"}
    assert run.scripts() == ["scripts/deploy_stack.py"] * 3 + ["cloudformation"] + ["scripts/deploy_stack.py"] * 2 + [
        "scripts.deploy_eks"]
    stacks = [argv[argv.index("--stack") + 1] for argv, _ in run.calls if "--stack" in argv]
    assert stacks == ["dep-01-ef-network", "dep-01-ef-harness", "dep-01-ef-ecs-fargate",
                      "dep-01-ke-network", "dep-01-ke-harness"]


def test_harness_stack_gets_the_network_and_the_k6_image(tmp_path):
    run = FakeRunner()
    make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run)(state(("ecs-fargate",)))
    argv, env = next((a, e) for a, e in run.calls if "dep-01-ef-harness" in a)
    assert argv[argv.index("--template") + 1] == "infra/cfn/20-harness.yaml"
    assert {"RunId=RUN_ID", "NetworkStackName=NETWORK_STACK", "K6ImageUri=K6_IMAGE_URI"} <= set(argv)
    assert env["K6_IMAGE_URI"] == K6 and env["NETWORK_STACK"] == "dep-01-ef-network"


def test_unset_k6_image_fails_before_any_stack_is_created(tmp_path):
    run = FakeRunner()
    out = make_deployer("sso", IMAGE, tmp_path, k6_image_uri=None, run=run)(state())
    assert run.calls == [] and out["status"] == "failed"
    assert "K6_IMAGE_URI" in out["errors"][0] and "make push-k6" in out["errors"][0]


def test_every_command_carries_the_region_and_the_cell_run_id(tmp_path):
    run = FakeRunner()
    make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run, instance_type=INSTANCE)(state(("ecs-fargate", "ecs-ec2", "eks-ec2", "eks-fargate")))
    for argv, env in run.calls:
        assert argv[argv.index("--region") + 1] == REGION and env["AWS_PROFILE"] == "sso"
        assert re.fullmatch(r"dep-01-[a-z]{2}", env["RUN_ID"]) and any(a.startswith(env["RUN_ID"]) for a in argv)
        assert "--yes" in argv or "describe-stacks" in argv
    assert len({env["RUN_ID"] for _, env in run.calls}) == 4  # one run id per cell
    ec2 = [(argv, env) for argv, env in run.calls if "dep-01-ee-ecs-ec2" in argv and "--stack" in argv]
    assert ec2[0][1]["COMPUTE_TYPE"] == "EC2" and "ComputeType=COMPUTE_TYPE" in ec2[0][0]


def test_deploy_json_records_start_end_and_alb(tmp_path):
    times = iter(datetime(2026, 10, 15, 12, m, tzinfo=UTC) for m in range(4))
    make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=FakeRunner(output=alb_output), now=lambda: next(times), instance_type=INSTANCE)(state())
    raw = tmp_path / "results" / "dep-01" / "raw"
    ecs = json.loads((raw / "ecs-fargate" / "deploy.json").read_text())
    assert ecs == {"stack_create_start": "2026-10-15T12:00:00+00:00", "stack_create_end": "2026-10-15T12:01:00+00:00",
                   "alb_dns_name": f"run-alb-1.{REGION}.elb.amazonaws.com"}
    eks = json.loads((raw / "eks-ec2" / "deploy.json").read_text())
    assert eks["alb_dns_name"] == f"k8s-workload-abc.{REGION}.elb.amazonaws.com"


def test_missing_profile_or_image_fails_before_any_command(tmp_path):
    run = FakeRunner()
    out = make_deployer(None, "", tmp_path, run=run)(state())
    assert run.calls == [] and out["status"] == "failed"
    assert "AWS_PROFILE" in out["errors"][0] and "IMAGE_URI" in out["errors"][0]


def test_run_record_and_output_hold_no_account_id_or_profile(tmp_path, capsys):
    run = FakeRunner(fail=lambda argv: "scripts.deploy_eks" in argv, output=alb_output)
    out = make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run, instance_type=INSTANCE)(state())
    printed = capsys.readouterr()
    assert not ACCOUNT_ID.search(printed.out + printed.err + " ".join(out["errors"]))
    lines = (tmp_path / "runs" / "dep-01" / "commands.jsonl").read_text()
    entries = [json.loads(line) for line in lines.splitlines()]
    assert [e["exit"] for e in entries] == [0, 0, 0, 0, 0, 0, 1] and all(e["seconds"] >= 0 for e in entries)
    assert "IMAGE_URI" in entries[0]["env"] and ACCOUNT not in lines and "sso" not in lines


def test_second_cell_failure_still_reaches_teardown_for_every_cell(tmp_path):
    run = FakeRunner(fail=lambda argv: "scripts.deploy_eks" in argv)
    nodes = {"deployer": make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run, instance_type=INSTANCE), "teardown": make_teardown("sso", tmp_path, run=run)}
    graph = build_graph(lambda s: {}, make_gate(auto_approve_under=100.0), InMemorySaver(), **nodes)
    out = graph.invoke(state().model_copy(update={"estimate_usd": 1.0}), {"configurable": {"thread_id": "dep-01"}})
    assert out["status"] == "failed" and "eks-ec2" in out["errors"][0]
    deletes = [argv[argv.index("--run-id") + 1] for argv, _ in run.calls if "scripts/delete_run.py" in argv]
    assert deletes == ["dep-01-ef", "dep-01-ke"]
    assert run.scripts().index("scripts.deploy_eks") < run.scripts().index("scripts/delete_run.py")


def test_a_runner_that_raises_mid_deploy_still_reaches_teardown(tmp_path):
    """SPEC section 10: teardown runs in a `finally` path. A missing `aws` binary raises after two stacks exist."""

    class Raising(FakeRunner):
        def __call__(self, argv, env):
            if argv[0] == "aws":
                self.calls.append((argv, env))
                raise FileNotFoundError("aws")
            return super().__call__(argv, env)

    run = Raising()
    nodes = {"deployer": make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run, instance_type=INSTANCE), "teardown": make_teardown("sso", tmp_path, run=run)}
    graph = build_graph(lambda s: {}, make_gate(auto_approve_under=100.0), InMemorySaver(), **nodes)
    out = graph.invoke(state().model_copy(update={"estimate_usd": 1.0}), {"configurable": {"thread_id": "dep-01"}})
    assert out["status"] == "failed" and "ecs-fargate" in out["errors"][0]
    deletes = [argv[argv.index("--run-id") + 1] for argv, _ in run.calls if "scripts/delete_run.py" in argv]
    assert deletes == ["dep-01-ef", "dep-01-ke"]


def test_both_ec2_cells_deploy_the_run_config_instance_type(tmp_path):
    """SPEC section 4: ecs-ec2 and eks-ec2 run on the same instance type, the one the cost model prices."""
    run = FakeRunner()
    make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run, instance_type=INSTANCE)(state(("ecs-ec2", "eks-ec2")))
    ecs = next((a, e) for a, e in run.calls if "dep-01-ee-ecs-ec2" in a and "--stack" in a)
    eks = next((a, e) for a, e in run.calls if "scripts.deploy_eks" in a)
    assert "InstanceType=INSTANCE_TYPE" in ecs[0]
    assert ecs[1]["INSTANCE_TYPE"] == eks[1]["INSTANCE_TYPE"] == INSTANCE


def test_fargate_cells_get_no_instance_type(tmp_path):
    run = FakeRunner()
    make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run, instance_type=INSTANCE)(state(("ecs-fargate", "eks-fargate")))
    assert all("INSTANCE_TYPE" not in env and "InstanceType=INSTANCE_TYPE" not in argv for argv, env in run.calls)


def test_unset_instance_type_fails_an_ec2_cell_before_any_command(tmp_path):
    run = FakeRunner()
    out = make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run)(state(("ecs-ec2",)))
    assert run.calls == [] and out["status"] == "failed" and "instance_type" in out["errors"][0]


def test_cli_hands_the_plan_instance_type_to_the_deployer(tmp_path, monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "make_deployer", lambda *a, **kw: got.update(kw))
    config = json.loads(cli.RUN_CONFIG.read_text())
    cli.deploy_nodes(tmp_path, config)
    assert got["instance_type"] == config["plan"]["instance_type"]


def test_eks_cell_gets_a_diagnostics_path_in_the_run_raw_folder(tmp_path):
    # Issue #155: deploy_eks writes why the ALB never came there, before the graph reaches teardown.
    run = FakeRunner()
    make_deployer("sso", IMAGE, tmp_path, k6_image_uri=K6, run=run)(state(("eks-fargate",)))
    argv = next(a for a, _ in run.calls if "scripts.deploy_eks" in a)
    assert argv[argv.index("--diagnostics") + 1] == str(tmp_path / "results" / "dep-01" / "raw" / "eks-diagnostics.txt")
