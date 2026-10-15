"""Teardown script: region guard, newest-first deletes, DELETE_FAILED, leftover check (issue #45)."""

import re
from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import Stubber

from scripts import delete_run

RUN = "r1"
ACCOUNT = "1234" * 3  # built at runtime: tracked files must hold no account id
NAT_ARN = f"arn:aws:ec2:us-east-1:{ACCOUNT}:natgateway/nat-0abc"
VPCE_ARN = f"arn:aws:ec2:us-east-1:{ACCOUNT}:vpc-endpoint/vpce-0abc"
BUCKET_ARN = "arn:aws:s3:::r1-results"
INSTANCE_ARN = f"arn:aws:ec2:us-east-1:{ACCOUNT}:instance/i-0abc"
ECS_PREFIX = f"arn:aws:ecs:us-east-1:{ACCOUNT}"
CLUSTER_ARN = f"{ECS_PREFIX}:cluster/r1-ecs"
SERVICE_ARN = f"{ECS_PREFIX}:service/r1-ecs/r1-svc"
TASKDEF_ARN = f"{ECS_PREFIX}:task-definition/r1-agent:3"
TASK_ARN = f"{ECS_PREFIX}:task/r1-ecs/abc123"


def _client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="stub",
        aws_secret_access_key="stub",
    )


@pytest.fixture
def clients():
    made = {s: _client(s) for s in ("cloudformation", "ec2", "ecs", "resourcegroupstaggingapi")}
    stubbers = {s: Stubber(c) for s, c in made.items()}
    for stubber in stubbers.values():
        stubber.activate()
    yield made, stubbers
    for stubber in stubbers.values():
        stubber.assert_no_pending_responses()
        stubber.deactivate()


def _summary(name, hour):
    return {
        "StackName": name,
        "StackId": f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:stack/{name}/x",
        "CreationTime": datetime(2026, 10, 1, hour, tzinfo=UTC),
        "StackStatus": "CREATE_COMPLETE",
    }


def _stub_delete(cfn_stub, name, final="DELETE_COMPLETE"):
    cfn_stub.add_response("delete_stack", {}, {"StackName": name})
    cfn_stub.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": name,
                    "CreationTime": datetime(2026, 10, 1, tzinfo=UTC),
                    "StackStatus": final,
                }
            ]
        },
        {"StackName": name},
    )


def _stub_tags(tag_stub, arns):
    tag_stub.add_response(
        "get_resources",
        {"ResourceTagMappingList": [{"ResourceARN": a} for a in arns]},
        {"TagFilters": [{"Key": "run-id", "Values": [RUN]}]},
    )


def _stub_nat(ec2_stub, state):
    gateways = [{"NatGatewayId": "nat-0abc", "State": state}] if state else []
    ec2_stub.add_response(
        "describe_nat_gateways",
        {"NatGateways": gateways},
        {"Filter": [{"Name": "nat-gateway-id", "Values": ["nat-0abc"]}]},
    )


def test_no_region_exits_naming_it_and_makes_no_aws_call(monkeypatch, capsys):
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)

    def no_session(*_a, **_k):
        raise AssertionError("AWS was called")

    monkeypatch.setattr(delete_run.boto3, "Session", no_session)
    with pytest.raises(SystemExit) as exc:
        delete_run.main(["--run-id", RUN, "--yes"])
    assert exc.value.code != 0
    assert "region" in str(exc.value.code).lower()
    assert "AWS_REGION" in str(exc.value.code)


def test_region_comes_from_flag_then_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert delete_run.resolve_region(None) == "us-west-2"
    assert delete_run.resolve_region("ca-central-1") == "ca-central-1"
    monkeypatch.delenv("AWS_REGION")
    assert delete_run.resolve_region(None) == "eu-west-1"


def test_finds_only_run_stacks_newest_first(clients):
    made, stubs = clients
    stubs["cloudformation"].add_response(
        "list_stacks",
        {
            "StackSummaries": [
                _summary("r1-network", 1),
                _summary("r1-ecs", 3),
                _summary("r10-network", 4),
                _summary("other", 5),
                _summary("r1-harness", 2),
            ]
        },
        {"StackStatusFilter": delete_run.live_statuses(made["cloudformation"])},
    )
    names = delete_run.run_stacks(made["cloudformation"], RUN)
    assert names == ["r1-ecs", "r1-harness", "r1-network"]


def test_deletes_in_given_order_and_waits_for_each(clients, capsys):
    made, stubs = clients
    for name in ("r1-ecs", "r1-harness", "r1-network"):
        _stub_delete(stubs["cloudformation"], name)
    delete_run.delete_stacks(made["cloudformation"], ["r1-ecs", "r1-harness", "r1-network"])
    out = capsys.readouterr().out
    assert out.index("r1-ecs") < out.index("r1-harness") < out.index("r1-network")


def test_delete_failed_exits_and_prints_failed_resources_masked(clients, capsys):
    made, stubs = clients
    _stub_delete(stubs["cloudformation"], "r1-ecs", final="DELETE_FAILED")
    stubs["cloudformation"].add_response(
        "describe_stack_resources",
        {
            "StackResources": [
                {
                    "LogicalResourceId": "Bucket",
                    "PhysicalResourceId": "r1-bucket",
                    "ResourceType": "AWS::S3::Bucket",
                    "ResourceStatus": "DELETE_FAILED",
                    "ResourceStatusReason": f"not empty in {ACCOUNT}",
                    "Timestamp": datetime(2026, 10, 1, tzinfo=UTC),
                },
                {
                    "LogicalResourceId": "Role",
                    "PhysicalResourceId": "r1-role",
                    "ResourceType": "AWS::IAM::Role",
                    "ResourceStatus": "DELETE_COMPLETE",
                    "Timestamp": datetime(2026, 10, 1, tzinfo=UTC),
                },
            ]
        },
        {"StackName": "r1-ecs"},
    )
    with pytest.raises(SystemExit) as exc:
        delete_run.delete_stacks(made["cloudformation"], ["r1-ecs", "r1-network"])
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "r1-bucket" in err
    assert "r1-role" not in err
    assert ACCOUNT not in err + str(exc.value.code)


def test_deleted_nat_gateway_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [NAT_ARN])
    _stub_nat(stubs["ec2"], "deleted")
    assert delete_run.leftovers(
        made["resourcegroupstaggingapi"], made["ec2"], made["ecs"], RUN
    ) == []


def test_absent_vpc_endpoint_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [VPCE_ARN])
    stubs["ec2"].add_response(
        "describe_vpc_endpoints",
        {"VpcEndpoints": []},
        {"Filters": [{"Name": "vpc-endpoint-id", "Values": ["vpce-0abc"]}]},
    )
    assert delete_run.leftovers(
        made["resourcegroupstaggingapi"], made["ec2"], made["ecs"], RUN
    ) == []


def test_available_nat_gateway_and_other_resources_are_leftovers(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [NAT_ARN, BUCKET_ARN])
    _stub_nat(stubs["ec2"], "available")
    found = delete_run.leftovers(
        made["resourcegroupstaggingapi"], made["ec2"], made["ecs"], RUN
    )
    assert found == [NAT_ARN, BUCKET_ARN]


def test_leftover_check_retries_until_clean(clients, capsys):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [BUCKET_ARN])
    _stub_tags(stubs["resourcegroupstaggingapi"], [])
    delete_run.check_leftovers(
        made["resourcegroupstaggingapi"],
        made["ec2"],
        made["ecs"],
        RUN,
        timeout=900,
        sleep=lambda _s: None,
    )
    assert "no leftovers" in capsys.readouterr().out


def test_live_leftover_fails_after_timeout_with_masked_arns(clients, capsys):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [NAT_ARN])
    _stub_nat(stubs["ec2"], "available")
    with pytest.raises(SystemExit) as exc:
        delete_run.check_leftovers(
            made["resourcegroupstaggingapi"],
            made["ec2"],
            made["ecs"],
            RUN,
            timeout=0,
            sleep=lambda _s: None,
        )
    assert exc.value.code != 0
    output = capsys.readouterr()
    text = output.out + output.err + str(exc.value.code)
    assert "natgateway/nat-0abc" in text
    assert not re.search(r"\d{12}", text)


def test_each_retry_prints_one_progress_line_without_account_id(clients, capsys):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [NAT_ARN, BUCKET_ARN])
    _stub_nat(stubs["ec2"], "available")
    _stub_tags(stubs["resourcegroupstaggingapi"], [BUCKET_ARN])
    _stub_tags(stubs["resourcegroupstaggingapi"], [])
    delete_run.check_leftovers(
        made["resourcegroupstaggingapi"],
        made["ec2"],
        made["ecs"],
        RUN,
        timeout=900,
        sleep=lambda _s: None,
    )
    out = capsys.readouterr().out
    progress = [line for line in out.splitlines() if line.startswith("checking leftovers")]
    assert progress == [
        f"checking leftovers (attempt 1, 2 left, next check in {delete_run.CHECK_INTERVAL} s)",
        f"checking leftovers (attempt 2, 1 left, next check in {delete_run.CHECK_INTERVAL} s)",
    ]
    assert not re.search(r"\d{12}", out)


def test_mask_hides_account_ids():
    assert delete_run.mask(NAT_ARN) == "arn:aws:ec2:us-east-1:************:natgateway/nat-0abc"


def test_aws_error_output_masks_account_id(clients, monkeypatch, capsys):
    made, stubs = clients
    stubs["cloudformation"].add_client_error(
        "list_stacks",
        "AccessDenied",
        f"User: arn:aws:sts::{ACCOUNT}:assumed-role/AWSReservedSSO_Admin/me is not authorized",
    )

    class Session:
        def __init__(self, region_name):
            pass

        def client(self, service):
            return made.get(service)  # eks and elbv2 are unused: the run fails at list_stacks

    monkeypatch.setattr(delete_run.boto3, "Session", Session)
    with pytest.raises(SystemExit) as exc:
        delete_run.main(["--run-id", RUN, "--region", "us-east-1", "--yes"])
    assert exc.value.code != 0
    output = capsys.readouterr()
    text = output.out + output.err + str(exc.value.code)
    assert "AccessDenied" in text
    assert not re.search(r"\d{12}", text)


def test_answer_other_than_y_aborts_before_deleting(clients, monkeypatch):
    made, stubs = clients
    stubs["cloudformation"].add_response(
        "list_stacks",
        {"StackSummaries": [_summary("r1-ecs", 1)]},
        {"StackStatusFilter": delete_run.live_statuses(made["cloudformation"])},
    )
    with pytest.raises(SystemExit) as exc:
        delete_run.teardown(
            made["cloudformation"],
            made["ec2"],
            made["ecs"],
            made["resourcegroupstaggingapi"],
            RUN,
            timeout=900,
            answer=lambda _p: "n",
        )
    assert "Aborted" in str(exc.value.code)


def _leftovers(made):
    return delete_run.leftovers(made["resourcegroupstaggingapi"], made["ec2"], made["ecs"], RUN)


def test_inactive_cluster_task_definition_and_stopped_task_are_not_leftovers(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [CLUSTER_ARN, TASKDEF_ARN, TASK_ARN])
    ecs = stubs["ecs"]
    ecs.add_response(
        "describe_clusters",
        {"clusters": [{"clusterArn": CLUSTER_ARN, "status": "INACTIVE"}]},
        {"clusters": [CLUSTER_ARN]},
    )
    ecs.add_response(
        "describe_task_definition",
        {"taskDefinition": {"taskDefinitionArn": TASKDEF_ARN, "status": "INACTIVE"}},
        {"taskDefinition": TASKDEF_ARN},
    )
    ecs.add_response(
        "describe_tasks",
        {"tasks": [{"taskArn": TASK_ARN, "lastStatus": "STOPPED"}]},
        {"cluster": "r1-ecs", "tasks": [TASK_ARN]},
    )
    assert _leftovers(made) == []


def test_inactive_service_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [SERVICE_ARN])
    stubs["ecs"].add_response(
        "describe_services",
        {"services": [{"serviceArn": SERVICE_ARN, "status": "INACTIVE"}]},
        {"cluster": "r1-ecs", "services": [SERVICE_ARN]},
    )
    assert _leftovers(made) == []


def test_missing_ecs_item_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [CLUSTER_ARN])
    stubs["ecs"].add_response("describe_clusters", {"clusters": []}, {"clusters": [CLUSTER_ARN]})
    assert _leftovers(made) == []


@pytest.mark.parametrize("arn,method", [(TASK_ARN, "describe_tasks"), (SERVICE_ARN, "describe_services")])
def test_service_or_task_in_a_gone_cluster_is_not_a_leftover(clients, arn, method):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [arn])
    stubs["ecs"].add_client_error(method, "ClusterNotFoundException")
    assert _leftovers(made) == []


def test_unknown_task_definition_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [TASKDEF_ARN])
    stubs["ecs"].add_client_error("describe_task_definition", "ClientException")
    assert _leftovers(made) == []


def test_delete_in_progress_task_definition_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [TASKDEF_ARN])
    stubs["ecs"].add_response(
        "describe_task_definition",
        {"taskDefinition": {"taskDefinitionArn": TASKDEF_ARN, "status": "DELETE_IN_PROGRESS"}},
        {"taskDefinition": TASKDEF_ARN},
    )
    assert _leftovers(made) == []


def test_active_cluster_service_task_definition_and_running_task_are_leftovers(clients):
    made, stubs = clients
    arns = [CLUSTER_ARN, SERVICE_ARN, TASKDEF_ARN, TASK_ARN]
    _stub_tags(stubs["resourcegroupstaggingapi"], arns)
    ecs = stubs["ecs"]
    ecs.add_response(
        "describe_clusters",
        {"clusters": [{"clusterArn": CLUSTER_ARN, "status": "ACTIVE"}]},
        {"clusters": [CLUSTER_ARN]},
    )
    ecs.add_response(
        "describe_services",
        {"services": [{"serviceArn": SERVICE_ARN, "status": "ACTIVE"}]},
        {"cluster": "r1-ecs", "services": [SERVICE_ARN]},
    )
    ecs.add_response(
        "describe_task_definition",
        {"taskDefinition": {"taskDefinitionArn": TASKDEF_ARN, "status": "ACTIVE"}},
        {"taskDefinition": TASKDEF_ARN},
    )
    ecs.add_response(
        "describe_tasks",
        {"tasks": [{"taskArn": TASK_ARN, "lastStatus": "RUNNING"}]},
        {"cluster": "r1-ecs", "tasks": [TASK_ARN]},
    )
    assert _leftovers(made) == arns


def _stub_instance(ec2_stub, state):
    instances = [{"InstanceId": "i-0abc", "State": {"Name": state}}] if state else []
    ec2_stub.add_response(
        "describe_instances",
        {"Reservations": [{"Instances": instances}] if instances else []},
        {"InstanceIds": ["i-0abc"]},
    )


@pytest.mark.parametrize("state", ["terminated", "shutting-down"])
def test_terminated_ec2_instance_is_not_a_leftover(clients, state):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [INSTANCE_ARN])
    _stub_instance(stubs["ec2"], state)
    assert _leftovers(made) == []


@pytest.mark.parametrize("state", ["running", "stopped"])
def test_running_or_stopped_ec2_instance_is_a_leftover(clients, state):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [INSTANCE_ARN])
    _stub_instance(stubs["ec2"], state)
    assert _leftovers(made) == [INSTANCE_ARN]


def test_purged_ec2_instance_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [INSTANCE_ARN])
    stubs["ec2"].add_client_error("describe_instances", "InvalidInstanceID.NotFound")
    assert _leftovers(made) == []


# --- EKS: Helm releases and ALBs go before the stacks (issue #69) ---

CLUSTER = "r1-cluster"
ALB_ARN = f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT}:loadbalancer/app/k8s-x/abc"
OTHER_ALB_ARN = f"arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT}:loadbalancer/app/other/def"


@pytest.fixture
def eks_clients():
    made = {s: _client(s) for s in ("cloudformation", "eks", "elbv2")}
    stubbers = {s: Stubber(c) for s, c in made.items()}
    for stubber in stubbers.values():
        stubber.activate()
    yield made, stubbers
    for stubber in stubbers.values():
        stubber.assert_no_pending_responses()
        stubber.deactivate()


def _stub_cluster_output(cfn_stub, stack="r1-eks-ec2"):
    cfn_stub.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": stack,
                    "CreationTime": datetime(2026, 10, 1, tzinfo=UTC),
                    "StackStatus": "CREATE_COMPLETE",
                    "Outputs": [{"OutputKey": "ClusterName", "OutputValue": CLUSTER}],
                }
            ]
        },
        {"StackName": stack},
    )


def _stub_albs(elbv2_stub, tagged):
    """One describe round: an ALB of this cluster when `tagged`, plus one of another cluster."""
    arns = ([ALB_ARN] if tagged else []) + [OTHER_ALB_ARN]
    elbv2_stub.add_response(
        "describe_load_balancers",
        {"LoadBalancers": [{"LoadBalancerArn": a} for a in arns]},
        {},
    )
    descriptions = [{"ResourceArn": OTHER_ALB_ARN, "Tags": [{"Key": "elbv2.k8s.aws/cluster", "Value": "x"}]}]
    if tagged:
        descriptions.insert(
            0, {"ResourceArn": ALB_ARN, "Tags": [{"Key": "elbv2.k8s.aws/cluster", "Value": CLUSTER}]}
        )
    elbv2_stub.add_response("describe_tags", {"TagDescriptions": descriptions}, {"ResourceArns": arns})


class Runner:
    def __init__(self, fail_on=None, stderr=""):
        self.calls, self.fail_on, self.stderr = [], fail_on, stderr

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if self.fail_on and self.fail_on in argv:
            raise delete_run.subprocess.CalledProcessError(1, argv, stderr=self.stderr)


def _helm_releases(runner):
    return [argv[2] for argv in runner.calls if argv[:2] == ["helm", "uninstall"]]


def test_eks_run_uninstalls_helm_and_waits_for_alb_before_first_stack_delete(eks_clients, monkeypatch, capsys):
    made, stubs = eks_clients
    stubs["cloudformation"].add_response(
        "list_stacks",
        {"StackSummaries": [_summary("r1-network", 1), _summary("r1-eks-ec2", 2)]},
        {"StackStatusFilter": delete_run.live_statuses(made["cloudformation"])},
    )
    _stub_cluster_output(stubs["cloudformation"])
    stubs["eks"].add_response("describe_cluster", {"cluster": {"name": CLUSTER}}, {"name": CLUSTER})
    _stub_albs(stubs["elbv2"], tagged=True)
    _stub_albs(stubs["elbv2"], tagged=False)
    events, runner = [], Runner()

    def run(argv, **kwargs):
        events.append(("run",))
        return runner(argv, **kwargs)

    monkeypatch.setattr(delete_run, "delete_stacks", lambda _c, names: events.append(("delete", names)))
    monkeypatch.setattr(delete_run, "check_leftovers", lambda *_a: None)
    delete_run.teardown(
        made["cloudformation"], None, None, None, RUN, timeout=900, answer=lambda _p: "y",
        eks=made["eks"], elbv2=made["elbv2"], run=run, sleep=lambda _s: events.append(("sleep",)),
        region="us-east-1",
    )
    kinds = [e[0] for e in events]
    assert kinds.index("delete") == len(kinds) - 1
    assert _helm_releases(runner) == [delete_run.WORKLOAD_RELEASE, delete_run.LBC_RELEASE]
    # the ALB wait sits between the two uninstalls
    assert kinds == ["run", "run", "sleep", "run", "delete"]
    assert events[-1] == ("delete", ["r1-eks-ec2", "r1-network"])
    kubeconfigs = {argv[argv.index("--kubeconfig") + 1] for argv in runner.calls}
    assert len(kubeconfigs) == 1
    assert "/.kube/" not in kubeconfigs.pop()
    assert not re.search(r"\d{12}", capsys.readouterr().out)


def test_gone_cluster_skips_helm_in_one_line_and_still_deletes(eks_clients, monkeypatch, capsys):
    made, stubs = eks_clients
    stubs["cloudformation"].add_response(
        "list_stacks",
        {"StackSummaries": [_summary("r1-eks-ec2", 2)]},
        {"StackStatusFilter": delete_run.live_statuses(made["cloudformation"])},
    )
    _stub_cluster_output(stubs["cloudformation"])
    stubs["eks"].add_client_error("describe_cluster", "ResourceNotFoundException")
    deleted, runner = [], Runner()
    monkeypatch.setattr(delete_run, "delete_stacks", lambda _c, names: deleted.extend(names))
    monkeypatch.setattr(delete_run, "check_leftovers", lambda *_a: None)
    delete_run.teardown(
        made["cloudformation"], None, None, None, RUN, timeout=900, answer=lambda _p: "y",
        eks=made["eks"], elbv2=made["elbv2"], run=runner, region="us-east-1",
    )
    assert runner.calls == []
    assert deleted == ["r1-eks-ec2"]
    skips = [line for line in capsys.readouterr().out.splitlines() if "skipping Helm" in line]
    assert len(skips) == 1


def test_unreachable_cluster_skips_helm_masked_and_still_deletes(eks_clients, monkeypatch, capsys):
    made, stubs = eks_clients
    stubs["cloudformation"].add_response(
        "list_stacks",
        {"StackSummaries": [_summary("r1-eks-ec2", 2)]},
        {"StackStatusFilter": delete_run.live_statuses(made["cloudformation"])},
    )
    _stub_cluster_output(stubs["cloudformation"])
    stubs["eks"].add_response("describe_cluster", {"cluster": {"name": CLUSTER}}, {"name": CLUSTER})
    runner = Runner(fail_on="uninstall", stderr=f"Kubernetes cluster unreachable: arn:aws:eks::{ACCOUNT}:x\nmore")
    deleted = []
    monkeypatch.setattr(delete_run, "delete_stacks", lambda _c, names: deleted.extend(names))
    monkeypatch.setattr(delete_run, "check_leftovers", lambda *_a: None)
    delete_run.teardown(
        made["cloudformation"], None, None, None, RUN, timeout=900, answer=lambda _p: "y",
        eks=made["eks"], elbv2=made["elbv2"], run=runner, region="us-east-1",
    )
    assert deleted == ["r1-eks-ec2"]
    out = capsys.readouterr().out
    assert len([line for line in out.splitlines() if "skipping Helm" in line]) == 1
    assert "unreachable" in out
    assert not re.search(r"\d{12}", out)


def test_alb_still_there_after_timeout_exits_before_any_stack_delete(eks_clients, capsys):
    made, stubs = eks_clients
    _stub_albs(stubs["elbv2"], tagged=True)
    with pytest.raises(SystemExit) as exc:
        delete_run.wait_for_albs(made["elbv2"], CLUSTER, timeout=0, sleep=lambda _s: None)
    text = capsys.readouterr().err + str(exc.value.code)
    assert "k8s-x" in text
    assert not re.search(r"\d{12}", text)


def test_ecs_only_run_makes_no_eks_or_helm_call(clients, monkeypatch):
    made, stubs = clients
    stubs["cloudformation"].add_response(
        "list_stacks",
        {"StackSummaries": [_summary("r1-ecs", 2)]},
        {"StackStatusFilter": delete_run.live_statuses(made["cloudformation"])},
    )
    deleted, runner = [], Runner()
    monkeypatch.setattr(delete_run, "delete_stacks", lambda _c, names: deleted.extend(names))
    monkeypatch.setattr(delete_run, "check_leftovers", lambda *_a: None)
    delete_run.teardown(
        made["cloudformation"], made["ec2"], made["ecs"], made["resourcegroupstaggingapi"],
        RUN, timeout=900, answer=lambda _p: "y", run=runner,
    )
    assert runner.calls == []
    assert deleted == ["r1-ecs"]
