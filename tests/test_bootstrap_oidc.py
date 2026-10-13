"""Bootstrap stack: GitHub OIDC trust and deploy role stay narrow (issue #23)."""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infra/cfn/00-bootstrap.yaml"
REPO = "MAOFILHO/agentic-platform-advisor"


class CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


CfnLoader.add_multi_constructor("!", _tag)


def _resources():
    return yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)["Resources"]


def _as_list(value):
    return value if isinstance(value, list) else [value]


def test_deploy_role_trusts_only_this_repository():
    roles = [r for r in _resources().values() if r["Type"] == "AWS::IAM::Role"]
    assert len(roles) == 1
    statements = roles[0]["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    for st in statements:
        assert st["Action"] == "sts:AssumeRoleWithWebIdentity"
        subjects = []
        for condition in st.get("Condition", {}).values():
            subjects += _as_list(condition.get("token.actions.githubusercontent.com:sub", []))
        # A trust statement with no sub condition trusts every repository.
        assert subjects, "trust statement without a sub condition"
        for sub in subjects:
            assert sub.startswith(f"repo:{REPO}:"), sub


def test_no_statement_allows_every_action_on_every_resource():
    for name, res in _resources().items():
        if res["Type"] != "AWS::IAM::Role":
            continue
        for policy in res["Properties"].get("Policies", []):
            for st in policy["PolicyDocument"]["Statement"]:
                both = "*" in _as_list(st["Action"]) and "*" in _as_list(st["Resource"])
                assert not both, f"{name}/{st.get('Sid')}"


def test_no_tracked_text_file_holds_an_account_id():
    account_id = re.compile(rb"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])")
    files = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    hits = []
    for name in filter(None, files):
        data = (ROOT / name.decode()).read_bytes()
        if b"\0" in data:
            continue  # binary file
        if account_id.search(data):
            hits.append(name.decode())
    assert hits == []
