"""Guard (issue #128): both EC2 cells deploy one instance type, the one the cost model prices. Nothing is deployed."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFN = ROOT / "infra" / "cfn"


def default_instance_type(template: str) -> str:
    """The InstanceType parameter's Default, read as text: the templates use CloudFormation tags."""
    text = (CFN / template).read_text()
    match = re.search(r"^  InstanceType:\n(?:    .*\n)*?    Default: (\S+)$", text, re.MULTILINE)
    assert match, f"{template} has no InstanceType default"
    return match.group(1)


def test_ecs_and_eks_templates_default_to_the_same_instance_type():
    assert default_instance_type("30-ecs.yaml") == default_instance_type("40-eks.yaml")


def test_template_default_is_the_run_config_instance_type():
    plan = json.loads((ROOT / "runs" / "default.json").read_text())["plan"]
    assert default_instance_type("30-ecs.yaml") == plan["instance_type"]
