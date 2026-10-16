"""Static checks on infra/cfn/20-harness.yaml. Nothing is deployed."""

from pathlib import Path

import yaml

from tests.test_bootstrap_oidc import CfnLoader

TEMPLATE = Path(__file__).resolve().parent.parent / "infra" / "cfn" / "20-harness.yaml"
BOUNDARY = {"ImportValue": "agentic-platform-advisor-StackRoleBoundaryArn"}
TEMPLATE_DOC = yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)
RESOURCES = TEMPLATE_DOC["Resources"]


def test_parameters_and_outputs():
    params = TEMPLATE_DOC["Parameters"]
    assert set(params) == {"RunId", "TtlHours", "NetworkStackName", "K6ImageUri", "Cpu", "Memory"}
    assert {n for n, p in params.items() if "Default" not in p} == {"RunId", "NetworkStackName", "K6ImageUri"}
    assert [str(params[n]["Default"]) for n in ("TtlHours", "Cpu", "Memory")] == ["4", "1024", "2048"]
    assert set(TEMPLATE_DOC["Outputs"]) == {
        "ClusterName", "TaskDefinitionArn", "SubnetIds", "SecurityGroupId", "LogGroupName"
    }
    subnets = TEMPLATE_DOC["Outputs"]["SubnetIds"]["Value"]
    assert subnets == {"ImportValue": {"Fn::Sub": "${NetworkStackName}-PrivateSubnetIds"}}


def test_resources_and_tags():
    assert {k: v["Type"] for k, v in RESOURCES.items()} == {
        "LogGroup": "AWS::Logs::LogGroup",
        "Cluster": "AWS::ECS::Cluster",
        "TaskSecurityGroup": "AWS::EC2::SecurityGroup",
        "ExecutionRole": "AWS::IAM::Role",
        "TaskRole": "AWS::IAM::Role",
        "TaskDefinition": "AWS::ECS::TaskDefinition",
    }
    for name, res in RESOURCES.items():
        tags = {t["Key"]: t["Value"] for t in res["Properties"]["Tags"]}
        assert tags["cell"] == "harness" and tags["project"] == "agentic-platform-advisor", name
        assert {"run-id", "ttl-hours"} <= set(tags), name


def test_roles_have_the_boundary_and_the_task_role_no_permissions():
    for name in ("ExecutionRole", "TaskRole"):
        assert RESOURCES[name]["Properties"]["PermissionsBoundary"] == BOUNDARY, name
    assert not {"Policies", "ManagedPolicyArns"} & set(RESOURCES["TaskRole"]["Properties"])


def test_security_group_has_egress_and_no_inbound_rule():
    props = RESOURCES["TaskSecurityGroup"]["Properties"]
    assert "SecurityGroupIngress" not in props and props["SecurityGroupEgress"]
    assert "${NetworkStackName}-VpcId" in TEMPLATE.read_text()


def test_log_group_keeps_logs_7_days():
    assert RESOURCES["LogGroup"]["Properties"]["RetentionInDays"] == 7


def test_task_is_fargate_x86_and_runs_k6_with_the_summary_markers():
    props = RESOURCES["TaskDefinition"]["Properties"]
    assert props["NetworkMode"] == "awsvpc" and props["RequiresCompatibilities"] == ["FARGATE"]
    assert props["RuntimePlatform"]["CpuArchitecture"] == "X86_64"
    (container,) = props["ContainerDefinitions"]
    assert container["Image"] == {"Ref": "K6ImageUri"} and container["EntryPoint"] == ["sh", "-c"]
    assert "Environment" not in container  # TARGET_URL, PROFILE, PEAK_RPS, DURATION_S arrive at RunTask
    assert container["LogConfiguration"]["Options"]["awslogs-group"] == {"Ref": "LogGroup"}
    (script,) = container["Command"]
    assert "k6 run --summary-export=/tmp/summary.json" in script
    assert '--summary-trend-stats="med,p(95),p(99)"' in script and "/scripts/${PROFILE" in script
    begin, end = script.index("K6-SUMMARY-BEGIN"), script.index("K6-SUMMARY-END")
    assert script.index("k6 run") < begin < script.index("cat /tmp/summary.json") < end
    assert "exit $rc" in script  # the task still fails when k6 fails


def test_cluster_turns_container_insights_on():
    settings = RESOURCES["Cluster"]["Properties"]["ClusterSettings"]
    assert settings == [{"Name": "containerInsights", "Value": "enabled"}]
