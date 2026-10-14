"""Every action a stack role grants must fit under the StackRoleBoundary. Nothing is deployed."""

from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "infra" / "cfn" / "00-bootstrap.yaml"
ROLE_TEMPLATES = [ROOT / "infra" / "cfn" / "30-ecs.yaml"]

ECS_AGENT_ACTIONS = {
    "ecs:DeregisterContainerInstance",
    "ecs:DiscoverPollEndpoint",
    "ecs:Poll",
    "ecs:RegisterContainerInstance",
    "ecs:StartTelemetrySession",
    "ecs:Submit*",
    "ecs:UpdateContainerInstancesState",
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


def _load(path):
    return yaml.load(path.read_text(), Loader=_CfnLoader)


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _allowed(statements):
    return [a for s in statements if s["Effect"] == "Allow" for a in _as_list(s["Action"])]


def boundary_actions(template):
    doc = template["Resources"]["StackRoleBoundary"]["Properties"]["PolicyDocument"]
    return _allowed(doc["Statement"])


def role_actions(template):
    """Map 'Role/PolicyName' to the actions its inline policy allows."""
    found = {}
    for name, res in template["Resources"].items():
        if res["Type"] != "AWS::IAM::Role":
            continue
        for policy in res["Properties"].get("Policies", []):
            statements = policy["PolicyDocument"]["Statement"]
            found[f"{name}/{policy['PolicyName']}"] = _allowed(statements)
    return found


def uncovered(roles, boundary):
    """Role actions that no boundary action matches (wildcards match by prefix)."""
    return sorted(
        f"{where}: {action}"
        for where, actions in roles.items()
        for action in actions
        if not any(fnmatchcase(action.lower(), b.lower()) for b in boundary)
    )


def test_uncovered_flags_an_action_the_boundary_lacks():
    roles = {"InstanceRole/ecs-agent": ["ecs:Poll", "ecs:Submit*", "s3:GetObject"]}
    boundary = ["ecs:Poll", "ecs:Submit*"]
    assert uncovered(roles, boundary) == ["InstanceRole/ecs-agent: s3:GetObject"]


def test_uncovered_accepts_a_boundary_wildcard():
    roles = {"R/p": ["ecs:Poll", "ecs:Submit*"]}
    assert uncovered(roles, ["ecs:*"]) == []


def test_boundary_covers_every_stack_role_policy():
    boundary = boundary_actions(_load(BOOTSTRAP))
    missing = []
    for path in ROLE_TEMPLATES:
        missing += uncovered(role_actions(_load(path)), boundary)
    assert missing == []


def test_boundary_allows_the_ecs_agent_actions_and_no_ecs_wildcard():
    ecs = {a for a in boundary_actions(_load(BOOTSTRAP)) if a.startswith("ecs:")}
    assert ecs == ECS_AGENT_ACTIONS
