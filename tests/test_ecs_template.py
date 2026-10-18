"""Static checks on infra/cfn/30-ecs.yaml. Nothing is deployed."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "infra" / "cfn" / "30-ecs.yaml"
MAKEFILE = ROOT / "Makefile"

# Parameters with no default; the deploy must supply them.
REQUIRED_PARAMETERS = {"RunId", "NetworkStackName", "ImageUri"}

# Resource types CloudFormation cannot tag.
UNTAGGABLE = {
    "AWS::ElasticLoadBalancingV2::Listener",
    "AWS::ApplicationAutoScaling::ScalableTarget",
    "AWS::ApplicationAutoScaling::ScalingPolicy",
    "AWS::IAM::InstanceProfile",
    "AWS::ECS::ClusterCapacityProviderAssociations",
}

# The ecs-fargate cell before ComputeType existed. FARGATE must keep exactly these.
FARGATE_RESOURCES = {
    "LogGroup": "AWS::Logs::LogGroup",
    "Cluster": "AWS::ECS::Cluster",
    "AlbSecurityGroup": "AWS::EC2::SecurityGroup",
    "TaskSecurityGroup": "AWS::EC2::SecurityGroup",
    "ExecutionRole": "AWS::IAM::Role",
    "TaskRole": "AWS::IAM::Role",
    "TaskDefinition": "AWS::ECS::TaskDefinition",
    "LoadBalancer": "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "TargetGroup": "AWS::ElasticLoadBalancingV2::TargetGroup",
    "Listener": "AWS::ElasticLoadBalancingV2::Listener",
    "Service": "AWS::ECS::Service",
    "ScalableTarget": "AWS::ApplicationAutoScaling::ScalableTarget",
    "CpuScalingPolicy": "AWS::ApplicationAutoScaling::ScalingPolicy",
}


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _tag)


def _template():
    return yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)


def _resources():
    return _template()["Resources"]


def _resources_for(compute_type):
    """Resources CloudFormation creates for one ComputeType (IsEc2 is the only condition)."""
    assert _template()["Conditions"] == {"IsEc2": {"Equals": [{"Ref": "ComputeType"}, "EC2"]}}
    keep = {None, "IsEc2"} if compute_type == "EC2" else {None}
    return {k: v for k, v in _resources().items() if v.get("Condition") in keep}


def _of_type(type_name):
    return {k: v for k, v in _resources().items() if v["Type"] == type_name}


def _pick(value, compute_type):
    """Resolve a top-level !If [IsEc2, ...] for one ComputeType."""
    if isinstance(value, dict) and "If" in value:
        cond, if_ec2, if_fargate = value["If"]
        assert cond == "IsEc2"
        return if_ec2 if compute_type == "EC2" else if_fargate
    return value


def _as_list(value):
    return value if isinstance(value, list) else [value]


def test_every_parameter_has_a_default_or_is_required():
    params = _template()["Parameters"]
    no_default = {name for name, p in params.items() if "Default" not in p}
    assert no_default == REQUIRED_PARAMETERS
    assert {
        "RunId", "TtlHours", "NetworkStackName", "ImageUri", "Cpu", "Memory",
        "MinReplicas", "MaxReplicas", "LlmMode", "ComputeType", "InstanceType",
    } == set(params)


def test_parameter_defaults():
    params = _template()["Parameters"]
    assert str(params["TtlHours"]["Default"]) == "4"
    assert str(params["Cpu"]["Default"]) == "256"
    assert str(params["Memory"]["Default"]) == "512"
    assert str(params["MinReplicas"]["Default"]) == "1"
    assert str(params["MaxReplicas"]["Default"]) == "4"
    assert params["LlmMode"]["Default"] == "mock"
    assert params["ComputeType"]["Default"] == "FARGATE"
    assert params["ComputeType"]["AllowedValues"] == ["FARGATE", "EC2"]
    assert params["InstanceType"]["Default"] == "t3.medium"


def test_fargate_resource_list_is_unchanged():
    got = {k: v["Type"] for k, v in _resources_for("FARGATE").items()}
    assert got == FARGATE_RESOURCES


def test_ec2_adds_asg_capacity_provider_and_instance_role():
    added = {k: v["Type"] for k, v in _resources_for("EC2").items() if k not in FARGATE_RESOURCES}
    assert added == {
        "InstanceSecurityGroup": "AWS::EC2::SecurityGroup",
        "InstanceRole": "AWS::IAM::Role",
        "InstanceProfile": "AWS::IAM::InstanceProfile",
        "LaunchTemplate": "AWS::EC2::LaunchTemplate",
        "AutoScalingGroup": "AWS::AutoScaling::AutoScalingGroup",
        "CapacityProvider": "AWS::ECS::CapacityProvider",
        "CapacityProviderAssociation": "AWS::ECS::ClusterCapacityProviderAssociations",
    }


def test_ec2_asg_size_comes_from_replicas_in_private_subnets():
    props = _resources()["AutoScalingGroup"]["Properties"]
    assert props["MinSize"] == {"Ref": "MinReplicas"}
    assert props["MaxSize"] == {"Ref": "MaxReplicas"}
    assert "PrivateSubnetIds" in str(props["VPCZoneIdentifier"])


def test_ec2_capacity_provider_has_managed_scaling_and_service_uses_it():
    provider = _resources()["CapacityProvider"]["Properties"]["AutoScalingGroupProvider"]
    assert provider["AutoScalingGroupArn"] == {"Ref": "AutoScalingGroup"}
    assert provider["ManagedScaling"]["Status"] == "ENABLED"
    assoc = _resources()["CapacityProviderAssociation"]["Properties"]
    assert assoc["Cluster"] == {"Ref": "Cluster"}
    assert assoc["CapacityProviders"] == [{"Ref": "CapacityProvider"}]
    service = _resources()["Service"]
    props = service["Properties"]
    assert _pick(props["LaunchType"], "FARGATE") == "FARGATE"
    assert _pick(props["LaunchType"], "EC2") == {"Ref": "AWS::NoValue"}
    assert _pick(props["CapacityProviderStrategy"], "EC2") == [
        {"CapacityProvider": {"Ref": "CapacityProvider"}, "Weight": 1}
    ]
    assert _pick(props["CapacityProviderStrategy"], "FARGATE") == {"Ref": "AWS::NoValue"}
    # The service waits for the association, or it cannot use the capacity provider.
    assert "CapacityProviderAssociation" in str(service["Metadata"])


def test_ec2_launch_template_uses_ecs_al2023_x86_ami_and_joins_the_cluster():
    data = _resources()["LaunchTemplate"]["Properties"]["LaunchTemplateData"]
    assert data["ImageId"] == (
        "{{resolve:ssm:/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id}}"
    )
    assert data["InstanceType"] == {"Ref": "InstanceType"}
    assert data["MetadataOptions"]["HttpTokens"] == "required"
    assert data["SecurityGroupIds"] == [{"Ref": "InstanceSecurityGroup"}]
    assert "ECS_CLUSTER=${Cluster}" in str(data["UserData"])


def test_ec2_instance_security_group_has_no_ingress():
    group = _resources()["InstanceSecurityGroup"]
    assert "SecurityGroupIngress" not in group["Properties"]


def test_autoscaling_targets_60_percent_cpu():
    (policy,) = _of_type("AWS::ApplicationAutoScaling::ScalingPolicy").values()
    config = policy["Properties"]["TargetTrackingScalingPolicyConfiguration"]
    assert config["TargetValue"] == 60
    assert config["PredefinedMetricSpecification"]["PredefinedMetricType"] == (
        "ECSServiceAverageCPUUtilization"
    )


def test_scaling_bounds_come_from_parameters():
    (target,) = _of_type("AWS::ApplicationAutoScaling::ScalableTarget").values()
    props = target["Properties"]
    assert props["MinCapacity"] == {"Ref": "MinReplicas"}
    assert props["MaxCapacity"] == {"Ref": "MaxReplicas"}


def test_health_check_path_is_readyz():
    (group,) = _of_type("AWS::ElasticLoadBalancingV2::TargetGroup").values()
    assert group["Properties"]["HealthCheckPath"] == "/readyz"
    assert group["Properties"]["Port"] == 8080


def _open_ingress_rules(group):
    rules = _as_list(group["Properties"].get("SecurityGroupIngress", []))
    return [r for r in rules if r.get("CidrIp") == "0.0.0.0/0"]


def test_only_the_alb_security_group_is_open_and_only_on_port_80():
    groups = _of_type("AWS::EC2::SecurityGroup")
    open_groups = {name: _open_ingress_rules(g) for name, g in groups.items()}
    open_groups = {name: rules for name, rules in open_groups.items() if rules}
    assert list(open_groups) == ["AlbSecurityGroup"]
    for rule in open_groups["AlbSecurityGroup"]:
        assert (rule["FromPort"], rule["ToPort"]) == (80, 80)


def test_task_security_group_accepts_only_the_alb():
    group = _resources()["TaskSecurityGroup"]
    (rule,) = _as_list(group["Properties"]["SecurityGroupIngress"])
    assert rule["SourceSecurityGroupId"] == {"Ref": "AlbSecurityGroup"}
    assert (rule["FromPort"], rule["ToPort"]) == (8080, 8080)
    assert "CidrIp" not in rule


def test_listener_is_http_port_80_only():
    listeners = _of_type("AWS::ElasticLoadBalancingV2::Listener")
    assert [(v["Properties"]["Port"], v["Properties"]["Protocol"]) for v in listeners.values()] == [
        (80, "HTTP")
    ]


def test_alb_is_internet_facing():
    (alb,) = _of_type("AWS::ElasticLoadBalancingV2::LoadBalancer").values()
    assert alb["Properties"]["Scheme"] == "internet-facing"


def test_every_role_has_the_permissions_boundary():
    roles = _of_type("AWS::IAM::Role")
    assert set(roles) == {"ExecutionRole", "TaskRole", "InstanceRole"}
    for name, role in roles.items():
        boundary = role["Properties"]["PermissionsBoundary"]
        assert "ImportValue" in boundary, name


def _statements(role):
    for policy in role["Properties"].get("Policies", []):
        yield from policy["PolicyDocument"]["Statement"]


def test_no_wildcard_action():
    for name, role in _of_type("AWS::IAM::Role").items():
        for statement in _statements(role):
            for action in _as_list(statement["Action"]):
                assert action != "*", name


def test_task_role_only_invokes_nova_micro():
    statements = list(_statements(_resources()["TaskRole"]))
    assert len(statements) == 1
    assert sorted(_as_list(statements[0]["Action"])) == [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
    ]
    resource = statements[0]["Resource"]["Sub"]
    assert resource.endswith("foundation-model/amazon.nova-micro-v1:0")


def test_log_group_keeps_logs_7_days():
    (group,) = _of_type("AWS::Logs::LogGroup").values()
    assert group["Properties"]["RetentionInDays"] == 7


def test_task_definition_on_port_8080_with_llm_mode():
    (task,) = _of_type("AWS::ECS::TaskDefinition").values()
    props = task["Properties"]
    assert _pick(props["RequiresCompatibilities"], "FARGATE") == ["FARGATE"]
    assert _pick(props["RequiresCompatibilities"], "EC2") == ["EC2"]
    assert props["NetworkMode"] == "awsvpc"
    # Task size is one value for both modes: no !If on Cpu or Memory.
    assert props["Cpu"] == {"Ref": "Cpu"}
    assert props["Memory"] == {"Ref": "Memory"}
    (container,) = props["ContainerDefinitions"]
    assert container["PortMappings"][0]["ContainerPort"] == 8080
    assert {"Name": "LLM_MODE", "Value": {"Ref": "LlmMode"}} in container["Environment"]
    assert container["LogConfiguration"]["LogDriver"] == "awslogs"


def test_service_runs_in_private_subnets_without_public_ip():
    (service,) = _of_type("AWS::ECS::Service").values()
    vpc = service["Properties"]["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert vpc["AssignPublicIp"] == "DISABLED"
    assert "PrivateSubnetIds" in str(vpc["Subnets"])


def _tag_lists(res):
    props = res["Properties"]
    if res["Type"] == "AWS::EC2::LaunchTemplate":
        specs = props["TagSpecifications"] + props["LaunchTemplateData"]["TagSpecifications"]
        return [s["Tags"] for s in specs]
    return [props["Tags"]]


def test_every_taggable_resource_has_run_tags_and_cell():
    for compute_type, cell in (("FARGATE", "ecs-fargate"), ("EC2", "ecs-ec2")):
        for name, res in _resources_for(compute_type).items():
            if res["Type"] in UNTAGGABLE:
                continue
            for tag_list in _tag_lists(res):
                tags = {t["Key"]: _pick(t["Value"], compute_type) for t in tag_list}
                assert {"run-id", "ttl-hours", "project", "cell"} <= set(tags), name
                assert tags["cell"] == cell, (compute_type, name)


def test_outputs():
    assert set(_template()["Outputs"]) == {"AlbDnsName", "ClusterName", "ServiceName"}


def test_fargate_size_note_names_the_real_deploy_card():
    text = TEMPLATE.read_text()
    assert "Decision 2" in text
    assert "real deploy" in text


def test_makefile_target_deploys_network_then_ecs():
    text = MAKEFILE.read_text()
    match = re.search(r"^ecs-fargate:.*?(?=^\S)", text, re.DOTALL | re.MULTILINE)
    assert match, "no ecs-fargate target"
    body = match.group(0)
    assert body.index("10-network.yaml") < body.index("30-ecs.yaml")
    assert "RUN_ID" in body
    assert "ImageUri=IMAGE_URI" in body
    assert "scripts/deploy_stack.py" in body


def test_makefile_ecs_ec2_target_deploys_network_then_ecs_with_ec2():
    text = MAKEFILE.read_text()
    match = re.search(r"^ecs-ec2:.*?(?=^\S)", text, re.DOTALL | re.MULTILINE)
    assert match, "no ecs-ec2 target"
    body = match.group(0)
    assert body.index("10-network.yaml") < body.index("30-ecs.yaml")
    assert "$(RUN_ID)-ecs-ec2" in body
    assert "ComputeType=COMPUTE_TYPE" in body
    assert "COMPUTE_TYPE=EC2" in body


def test_new_files_hold_no_account_id_or_email():
    for path in (TEMPLATE, MAKEFILE, Path(__file__)):
        text = path.read_text()
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", text), path
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), path
