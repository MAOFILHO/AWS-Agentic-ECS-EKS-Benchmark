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


def _client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="stub",
        aws_secret_access_key="stub",
    )


@pytest.fixture
def clients():
    made = {s: _client(s) for s in ("cloudformation", "ec2", "resourcegroupstaggingapi")}
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
    assert delete_run.leftovers(made["resourcegroupstaggingapi"], made["ec2"], RUN) == []


def test_absent_vpc_endpoint_is_not_a_leftover(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [VPCE_ARN])
    stubs["ec2"].add_response(
        "describe_vpc_endpoints",
        {"VpcEndpoints": []},
        {"Filters": [{"Name": "vpc-endpoint-id", "Values": ["vpce-0abc"]}]},
    )
    assert delete_run.leftovers(made["resourcegroupstaggingapi"], made["ec2"], RUN) == []


def test_available_nat_gateway_and_other_resources_are_leftovers(clients):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [NAT_ARN, BUCKET_ARN])
    _stub_nat(stubs["ec2"], "available")
    found = delete_run.leftovers(made["resourcegroupstaggingapi"], made["ec2"], RUN)
    assert found == [NAT_ARN, BUCKET_ARN]


def test_leftover_check_retries_until_clean(clients, capsys):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [BUCKET_ARN])
    _stub_tags(stubs["resourcegroupstaggingapi"], [])
    delete_run.check_leftovers(
        made["resourcegroupstaggingapi"], made["ec2"], RUN, timeout=900, sleep=lambda _s: None
    )
    assert "no leftovers" in capsys.readouterr().out


def test_live_leftover_fails_after_timeout_with_masked_arns(clients, capsys):
    made, stubs = clients
    _stub_tags(stubs["resourcegroupstaggingapi"], [NAT_ARN])
    _stub_nat(stubs["ec2"], "available")
    with pytest.raises(SystemExit) as exc:
        delete_run.check_leftovers(
            made["resourcegroupstaggingapi"], made["ec2"], RUN, timeout=0, sleep=lambda _s: None
        )
    assert exc.value.code != 0
    output = capsys.readouterr()
    text = output.out + output.err + str(exc.value.code)
    assert "natgateway/nat-0abc" in text
    assert not re.search(r"\d{12}", text)


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
            return made[service]

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
            made["resourcegroupstaggingapi"],
            RUN,
            timeout=900,
            answer=lambda _p: "n",
        )
    assert "Aborted" in str(exc.value.code)
