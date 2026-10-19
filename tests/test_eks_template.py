"""Static checks on infra/cfn/40-eks.yaml and 41-eks-lbc-iam.yaml. Nothing is deployed."""

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFN = ROOT / "infra" / "cfn"
EKS = CFN / "40-eks.yaml"
LBC = CFN / "41-eks-lbc-iam.yaml"

# Parameters with no default; the deploy must supply them.
EKS_REQUIRED = {"RunId", "NetworkStackName", "KubernetesVersion"}
LBC_REQUIRED = {"RunId", "EksStackName"}

TAG_KEYS = {"run-id", "ttl-hours", "project", "cell"}


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _tag)


def _load(path):
    return yaml.load(path.read_text(), Loader=_CfnLoader)


def _eks():
    return _load(EKS)


def _resources_for(compute_type):
    """Resources CloudFormation creates for one ComputeType (IsEc2 and IsFargate are the conditions)."""
    template = _eks()
    assert template["Conditions"] == {
        "IsEc2": {"Equals": [{"Ref": "ComputeType"}, "EC2"]},
        "IsFargate": {"Equals": [{"Ref": "ComputeType"}, "FARGATE"]},
    }
    keep = {None, "IsEc2" if compute_type == "EC2" else "IsFargate"}
    return {k: v for k, v in template["Resources"].items() if v.get("Condition") in keep}


def _of_type(template, type_name):
    return {k: v for k, v in template["Resources"].items() if v["Type"] == type_name}


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _statements(role):
    for policy in role["Properties"].get("Policies", []):
        yield from policy["PolicyDocument"]["Statement"]


def test_every_parameter_has_a_default_or_is_required():
    for path, required in ((EKS, EKS_REQUIRED), (LBC, LBC_REQUIRED)):
        params = _load(path)["Parameters"]
        assert {n for n, p in params.items() if "Default" not in p} == required, path.name


def test_eks_parameter_names_and_defaults():
    params = _eks()["Parameters"]
    assert set(params) == {
        "RunId", "TtlHours", "NetworkStackName", "ComputeType",
        "KubernetesVersion", "InstanceType", "MinNodes", "MaxNodes",
    }
    assert str(params["TtlHours"]["Default"]) == "4"
    assert params["ComputeType"]["Default"] == "EC2"
    assert params["ComputeType"]["AllowedValues"] == ["EC2", "FARGATE"]
    assert params["InstanceType"]["Default"] == "t3.medium"
    assert str(params["MinNodes"]["Default"]) == "1"
    assert str(params["MaxNodes"]["Default"]) == "3"


def test_kubernetes_version_has_no_default_in_the_template():
    assert "Default" not in _eks()["Parameters"]["KubernetesVersion"]


def test_ec2_has_a_node_group_and_no_fargate_profile():
    got = {k: v["Type"] for k, v in _resources_for("EC2").items()}
    assert "AWS::EKS::Nodegroup" in got.values()
    assert "AWS::EKS::FargateProfile" not in got.values()


def test_fargate_has_a_fargate_profile_and_no_node_group():
    got = {k: v["Type"] for k, v in _resources_for("FARGATE").items()}
    assert "AWS::EKS::FargateProfile" in got.values()
    assert "AWS::EKS::Nodegroup" not in got.values()


def test_each_mode_has_one_cluster_and_the_five_addons():
    for mode in ("EC2", "FARGATE"):
        got = _resources_for(mode)
        assert sum(v["Type"] == "AWS::EKS::Cluster" for v in got.values()) == 1
        addons = {v["Properties"]["AddonName"] for v in got.values() if v["Type"] == "AWS::EKS::Addon"}
        assert addons == {"vpc-cni", "kube-proxy", "coredns", "metrics-server", "eks-pod-identity-agent"}


def test_node_group_is_al2023_x86_in_private_subnets_sized_by_parameters():
    props = _eks()["Resources"]["NodeGroup"]["Properties"]
    assert props["AmiType"] == "AL2023_x86_64_STANDARD"
    assert props["InstanceTypes"] == [{"Ref": "InstanceType"}]
    assert "PrivateSubnetIds" in str(props["Subnets"])
    assert props["ScalingConfig"]["MinSize"] == {"Ref": "MinNodes"}
    assert props["ScalingConfig"]["MaxSize"] == {"Ref": "MaxNodes"}


def test_node_group_is_named_workload_for_the_helm_node_selector():
    assert _eks()["Resources"]["NodeGroup"]["Properties"]["NodegroupName"] == "workload"


def test_node_role_has_the_three_worker_policies():
    arns = str(_eks()["Resources"]["NodeRole"]["Properties"]["ManagedPolicyArns"])
    for name in ("AmazonEKSWorkerNodePolicy", "AmazonEKS_CNI_Policy", "AmazonEC2ContainerRegistryReadOnly"):
        assert name in arns


def test_cluster_uses_api_auth_and_public_and_private_endpoint():
    props = _eks()["Resources"]["Cluster"]["Properties"]
    assert props["AccessConfig"]["AuthenticationMode"] == "API"
    assert props["AccessConfig"]["BootstrapClusterCreatorAdminPermissions"] is True
    assert props["ResourcesVpcConfig"]["EndpointPublicAccess"] is True
    assert props["ResourcesVpcConfig"]["EndpointPrivateAccess"] is True
    assert props["Version"] == {"Ref": "KubernetesVersion"}


def test_control_plane_logging_is_off():
    assert "Logging" not in _eks()["Resources"]["Cluster"]["Properties"]


def test_us_east_1e_is_never_used():
    # Subnets come only from the network stack, and its AZ default skips us-east-1e.
    assert "us-east-1e" not in str(_eks()["Resources"]) + str(_load(LBC)["Resources"])
    network = _load(CFN / "10-network.yaml")
    assert "us-east-1e" not in network["Parameters"]["AvailabilityZones"]["Default"]
    for name, res in _eks()["Resources"].items():
        subnets = res["Properties"].get("Subnets") or res["Properties"].get("ResourcesVpcConfig", {}).get("SubnetIds")
        if subnets is not None:
            assert "PrivateSubnetIds" in str(subnets), name


def test_every_role_has_the_permissions_boundary():
    for path, names in ((EKS, {"ClusterRole", "NodeRole", "PodExecutionRole"}), (LBC, {"LbcRole"})):
        roles = _of_type(_load(path), "AWS::IAM::Role")
        assert set(roles) == names, path.name
        for name, role in roles.items():
            assert "ImportValue" in role["Properties"]["PermissionsBoundary"], name


def test_no_wildcard_action():
    for path in (EKS, LBC):
        for name, role in _of_type(_load(path), "AWS::IAM::Role").items():
            for statement in _statements(role):
                for action in _as_list(statement["Action"]):
                    assert action != "*" and not action.endswith(":*"), name


def test_pod_execution_role_trusts_only_fargate_pods():
    trust = _eks()["Resources"]["PodExecutionRole"]["Properties"]["AssumeRolePolicyDocument"]
    (statement,) = trust["Statement"]
    assert statement["Principal"] == {"Service": "eks-fargate-pods.amazonaws.com"}


def _tag_keys(props):
    tags = props.get("Tags")
    if isinstance(tags, dict):
        return set(tags)
    return {t["Key"] for t in tags if "If" not in t}  # a Fargate-only tag is checked on its own


def test_every_taggable_resource_carries_the_four_tags():
    for path in (EKS, LBC):
        for name, res in _load(path)["Resources"].items():
            assert _tag_keys(res["Properties"]) == TAG_KEYS, f"{path.name} {name}"


def test_cell_tag_names_the_cell():
    cluster = _eks()["Resources"]["Cluster"]["Properties"]
    cell = next(t for t in cluster["Tags"] if t["Key"] == "cell")["Value"]
    assert cell == {"If": ["IsEc2", "eks-ec2", "eks-fargate"]}


def test_coredns_runs_on_fargate_only_in_fargate_mode():
    value = _eks()["Resources"]["CoreDnsAddon"]["Properties"]["ConfigurationValues"]
    assert value == {"If": ["IsFargate", '{"computeType":"Fargate"}', {"Ref": "AWS::NoValue"}]}


def test_eks_outputs():
    outputs = _eks()["Outputs"]
    assert set(outputs) == {"ClusterName", "ClusterEndpoint", "OidcIssuerUrl"}
    assert outputs["ClusterName"]["Export"] == {"Name": {"Sub": "${AWS::StackName}-ClusterName"}}
    assert outputs["OidcIssuerUrl"]["Value"] == {"GetAtt": "Cluster.OpenIdConnectIssuerUrl"}
    assert outputs["OidcIssuerUrl"]["Export"] == {"Name": {"Sub": "${AWS::StackName}-OidcIssuerUrl"}}


ISSUER = "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE0123456789"
PROVIDER_ARN = f"arn:aws:iam::{'1' * 12}:oidc-provider/{ISSUER}"


def _lbc_for(cell):
    """41-eks-lbc-iam as CloudFormation creates it for one Cell: resources kept by Condition, trust policy resolved."""
    template = _load(LBC)
    assert template["Conditions"] == {
        "IsEc2": {"Equals": [{"Ref": "Cell"}, "eks-ec2"]},
        "IsFargate": {"Equals": [{"Ref": "Cell"}, "eks-fargate"]},
    }
    keep = {None, "IsEc2" if cell == "eks-ec2" else "IsFargate"}
    resources = {k: v for k, v in template["Resources"].items() if v.get("Condition") in keep}
    condition, fargate, ec2 = resources["LbcRole"]["Properties"]["AssumeRolePolicyDocument"]["If"]
    assert condition == "IsFargate"
    if cell == "eks-ec2":
        return resources, ec2
    body, variables = fargate["Sub"]
    # Issuer is the OidcIssuerUrl export without its https:// scheme.
    assert variables["Issuer"] == {"Select": [1, {"Split": ["//", {"ImportValue": {"Fn::Sub": "${EksStackName}-OidcIssuerUrl"}}]}]}
    return resources, json.loads(body.replace("${Issuer}", ISSUER).replace("${LbcOidcProvider}", PROVIDER_ARN))


def test_lbc_role_on_ec2_is_assumed_through_pod_identity_only():
    resources, trust = _lbc_for("eks-ec2")
    (statement,) = trust["Statement"]
    assert statement["Principal"] == {"Service": "pods.eks.amazonaws.com"}
    assert sorted(statement["Action"]) == ["sts:AssumeRole", "sts:TagSession"]
    assert "LbcPodIdentity" in resources and "LbcOidcProvider" not in resources


def test_lbc_role_on_fargate_trusts_only_the_controller_service_account_through_irsa():
    # Issue #153: Pod Identity does not run on Fargate, so the controller gets its role through IRSA.
    resources, trust = _lbc_for("eks-fargate")
    (statement,) = trust["Statement"]
    assert statement == {
        "Effect": "Allow",
        "Principal": {"Federated": PROVIDER_ARN},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {
            f"{ISSUER}:sub": "system:serviceaccount:kube-system:aws-load-balancer-controller",
            f"{ISSUER}:aud": "sts.amazonaws.com",
        }},
    }
    assert "LbcPodIdentity" not in resources
    provider = resources["LbcOidcProvider"]
    assert provider["Type"] == "AWS::IAM::OIDCProvider"
    assert provider["Properties"]["Url"] == {"ImportValue": {"Fn::Sub": "${EksStackName}-OidcIssuerUrl"}}
    assert provider["Properties"]["ClientIdList"] == ["sts.amazonaws.com"]


def test_lbc_association_binds_the_controller_service_account():
    props = _load(LBC)["Resources"]["LbcPodIdentity"]["Properties"]
    assert props["Namespace"] == "kube-system"
    assert props["ServiceAccount"] == "aws-load-balancer-controller"
    assert props["RoleArn"] == {"GetAtt": "LbcRole.Arn"}
    assert "ClusterName" in str(props["ClusterName"])


def test_lbc_outputs_the_role_arn():
    assert set(_load(LBC)["Outputs"]) == {"LbcRoleArn"}


def test_no_account_id_or_email_in_the_templates():
    for path in (EKS, LBC):
        text = path.read_text()
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", text), path.name
        assert not re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", text), path.name


# The EC2 and Elastic Load Balancing statements of the AWS Load Balancer Controller reference
# IAM policy for v3.6.0, the app version of the chart deploy_eks.py pins (LBC_CHART_VERSION).
# Source: https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/
# v3.6.0/docs/install/iam_policy.json. Each entry is (actions, resource, condition); arn:aws
# stands for arn:${AWS::Partition}. WAF, Shield and certificate statements are left out on
# purpose (see the template header).
_ELB = "arn:aws:elasticloadbalancing:*:*:"
_SG = "arn:aws:ec2:*:*:security-group/*"
_LB_TG = [f"{_ELB}targetgroup/*/*", f"{_ELB}loadbalancer/net/*/*", f"{_ELB}loadbalancer/app/*/*"]
_OWN = {"Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}}
_NEW = {"Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"}}
_RETAG = {
    "Null": {
        "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
        "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
    }
}
LBC_REFERENCE = [
    (
        [
            "ec2:DescribeAccountAttributes", "ec2:DescribeAddresses",
            "ec2:DescribeAvailabilityZones", "ec2:DescribeInternetGateways", "ec2:DescribeVpcs",
            "ec2:DescribeVpcPeeringConnections", "ec2:DescribeSubnets",
            "ec2:DescribeSecurityGroups", "ec2:DescribeInstances",
            "ec2:DescribeNetworkInterfaces", "ec2:DescribeTags", "ec2:GetCoipPoolUsage",
            "ec2:DescribeCoipPools", "ec2:GetSecurityGroupsForVpc", "ec2:DescribeIpamPools",
            "ec2:DescribeRouteTables",
            "elasticloadbalancing:DescribeLoadBalancers",
            "elasticloadbalancing:DescribeLoadBalancerAttributes",
            "elasticloadbalancing:DescribeListeners",
            "elasticloadbalancing:DescribeListenerCertificates",
            "elasticloadbalancing:DescribeSSLPolicies", "elasticloadbalancing:DescribeRules",
            "elasticloadbalancing:DescribeTargetGroups",
            "elasticloadbalancing:DescribeTargetGroupAttributes",
            "elasticloadbalancing:DescribeTargetHealth", "elasticloadbalancing:DescribeTags",
            "elasticloadbalancing:DescribeTrustStores",
            "elasticloadbalancing:DescribeListenerAttributes",
            "elasticloadbalancing:DescribeCapacityReservation",
        ],
        "*",
        None,
    ),
    # Rules on any group, the node (cluster) security group included: EKS does not tag it.
    (["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"], "*", None),
    (["ec2:CreateSecurityGroup"], "*", None),
    (
        ["ec2:CreateTags"],
        _SG,
        {
            "StringEquals": {"ec2:CreateAction": "CreateSecurityGroup"},
            "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
        },
    ),
    (["ec2:CreateTags", "ec2:DeleteTags"], _SG, _RETAG),
    (
        [
            "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
            "ec2:DeleteSecurityGroup",
        ],
        "*",
        _OWN,
    ),
    (
        ["elasticloadbalancing:CreateLoadBalancer", "elasticloadbalancing:CreateTargetGroup"],
        "*",
        _NEW,
    ),
    (
        [
            "elasticloadbalancing:CreateListener", "elasticloadbalancing:DeleteListener",
            "elasticloadbalancing:CreateRule", "elasticloadbalancing:DeleteRule",
        ],
        "*",
        None,
    ),
    (["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"], _LB_TG, _RETAG),
    (
        ["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"],
        [
            f"{_ELB}listener/net/*/*/*", f"{_ELB}listener/app/*/*/*",
            f"{_ELB}listener-rule/net/*/*/*", f"{_ELB}listener-rule/app/*/*/*",
        ],
        None,
    ),
    (
        [
            "elasticloadbalancing:ModifyLoadBalancerAttributes",
            "elasticloadbalancing:SetIpAddressType", "elasticloadbalancing:SetSecurityGroups",
            "elasticloadbalancing:SetSubnets", "elasticloadbalancing:DeleteLoadBalancer",
            "elasticloadbalancing:ModifyTargetGroup",
            "elasticloadbalancing:ModifyTargetGroupAttributes",
            "elasticloadbalancing:DeleteTargetGroup",
            "elasticloadbalancing:ModifyListenerAttributes",
            "elasticloadbalancing:ModifyCapacityReservation",
            "elasticloadbalancing:ModifyIpPools",
        ],
        "*",
        _OWN,
    ),
    (
        ["elasticloadbalancing:AddTags"],
        _LB_TG,
        {
            "StringEquals": {
                "elasticloadbalancing:CreateAction": ["CreateTargetGroup", "CreateLoadBalancer"]
            },
            "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
        },
    ),
    (
        ["elasticloadbalancing:RegisterTargets", "elasticloadbalancing:DeregisterTargets"],
        f"{_ELB}targetgroup/*/*",
        None,
    ),
    (
        [
            "elasticloadbalancing:SetWebAcl", "elasticloadbalancing:ModifyListener",
            "elasticloadbalancing:AddListenerCertificates",
            "elasticloadbalancing:RemoveListenerCertificates",
            "elasticloadbalancing:ModifyRule", "elasticloadbalancing:SetRulePriorities",
        ],
        "*",
        None,
    ),
]


def _resources(resource):
    """Resource list with !Sub "arn:${AWS::Partition}:" read as "arn:aws:"."""
    items = resource if isinstance(resource, list) else [resource]
    return sorted(
        (r["Sub"] if isinstance(r, dict) else r).replace("${AWS::Partition}", "aws")
        for r in items
    )


def missing_reference_grants(statements):
    """Reference (action, resource, condition) grants no Allow statement makes with the same scope."""
    missing = []
    for actions, resource, condition in LBC_REFERENCE:
        for action in actions:
            if not any(
                s["Effect"] == "Allow"
                and action in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
                and _resources(s["Resource"]) == _resources(resource)
                and s.get("Condition") == condition
                for s in statements
            ):
                missing.append((action, str(resource), str(condition)))
    return missing


def _lbc_statements():
    (policy,) = _load(LBC)["Resources"]["LbcRole"]["Properties"]["Policies"]
    return policy["PolicyDocument"]["Statement"]


def test_missing_reference_grants_flags_a_dropped_action_and_a_narrowed_scope():
    statements = [
        {"Effect": "Allow", "Action": list(actions), "Resource": resource, **(
            {"Condition": condition} if condition else {}
        )}
        for actions, resource, condition in LBC_REFERENCE
    ]
    assert missing_reference_grants(statements) == []
    statements[1]["Action"] = ["ec2:AuthorizeSecurityGroupIngress"]
    statements[13]["Action"].remove("elasticloadbalancing:SetRulePriorities")
    statements[2]["Condition"] = _OWN
    assert missing_reference_grants(statements) == [
        ("ec2:RevokeSecurityGroupIngress", "*", "None"),
        ("ec2:CreateSecurityGroup", "*", "None"),
        ("elasticloadbalancing:SetRulePriorities", "*", "None"),
    ]


def test_lbc_role_grants_the_reference_ec2_and_elb_policy():
    assert missing_reference_grants(_lbc_statements()) == []


def test_boundary_allows_every_reference_ec2_and_elb_action():
    doc = _load(CFN / "00-bootstrap.yaml")["Resources"]["StackRoleBoundary"]["Properties"]
    boundary = [
        a
        for s in doc["PolicyDocument"]["Statement"]
        if s["Effect"] == "Allow"
        for a in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
    ]
    actions = {a for acts, _, _ in LBC_REFERENCE for a in acts}
    assert [a for a in sorted(actions) if not any(fnmatchcase(a, b) for b in boundary)] == []


def _depends_on_profile(resource):
    """True when the resource refs FargateProfile in a Fargate-only tag (an implicit, conditional DependsOn)."""
    return any(t == {"If": ["IsFargate", {"Key": "fargate-profile", "Value": {"GetAtt": "FargateProfile.Arn"}},
                            {"Ref": "AWS::NoValue"}]} for t in resource["Properties"]["Tags"])


def test_fargate_add_ons_wait_for_the_fargate_profile_and_ec2_order_is_unchanged():
    # Issue #157: CoreDNS and metrics-server pods made before the profile stay Pending on Fargate.
    resources = _eks()["Resources"]
    for name in ("CoreDnsAddon", "MetricsServerAddon"):
        assert _depends_on_profile(resources[name]), name
        assert "DependsOn" not in resources[name], name  # a plain DependsOn would break the EC2 render
    others = [n for n, r in resources.items() if n not in ("CoreDnsAddon", "MetricsServerAddon")]
    assert not [n for n in others if _depends_on_profile(resources[n])]
