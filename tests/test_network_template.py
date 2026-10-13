"""Static checks on infra/cfn/10-network.yaml. Nothing is deployed."""

from pathlib import Path

import yaml

TEMPLATE = Path(__file__).resolve().parent.parent / "infra" / "cfn" / "10-network.yaml"

# Resource types CloudFormation cannot tag.
UNTAGGABLE = {
    "AWS::EC2::VPCGatewayAttachment",
    "AWS::EC2::Route",
    "AWS::EC2::SubnetRouteTableAssociation",
}


class _CfnLoader(yaml.SafeLoader):
    pass


# Keep short-form intrinsics (!Ref, !Sub ...) loadable; their values do not matter here.
_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _resources():
    return yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)["Resources"]


def test_exactly_one_nat_gateway():
    nats = [r for r in _resources().values() if r["Type"] == "AWS::EC2::NatGateway"]
    assert len(nats) == 1


def test_defines_required_endpoints():
    text = TEMPLATE.read_text()
    endpoints = [r for r in _resources().values() if r["Type"] == "AWS::EC2::VPCEndpoint"]
    types = sorted(r["Properties"].get("VpcEndpointType", "Gateway") for r in endpoints)
    assert types == ["Gateway", "Interface", "Interface", "Interface", "Interface"]
    for service in ("s3", "ecr.api", "ecr.dkr", "logs", "sts"):
        assert f"com.amazonaws.${{AWS::Region}}.{service}" in text


def test_us_east_1e_only_in_exclusion_comment():
    lines = [ln for ln in TEMPLATE.read_text().splitlines() if "us-east-1e" in ln]
    assert lines
    for line in lines:
        assert line.lstrip().startswith("#")
        assert "exclude" in line.lower()


def test_every_taggable_resource_has_run_tags():
    for name, res in _resources().items():
        if res["Type"] in UNTAGGABLE:
            continue
        keys = {t["Key"] for t in res["Properties"].get("Tags", [])}
        assert {"run-id", "ttl-hours", "project"} <= keys, name
