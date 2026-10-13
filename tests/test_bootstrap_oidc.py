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


def _statements():
    """Every (resource name, statement) in role inline policies and managed policies."""
    for name, res in _resources().items():
        props = res.get("Properties", {})
        if res["Type"] == "AWS::IAM::Role":
            docs = [p["PolicyDocument"] for p in props.get("Policies", [])]
        elif res["Type"] == "AWS::IAM::ManagedPolicy":
            docs = [props["PolicyDocument"]]
        else:
            continue
        for doc in docs:
            for st in doc["Statement"]:
                yield name, st


def _deploy_statements():
    return [st for name, st in _statements() if name == "DeployRole"]


BOUNDARY = {"Ref": "StackRoleBoundary"}
BOUNDARY_ACTIONS = {
    "iam:CreateRole",
    "iam:PutRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:AttachRolePolicy",
    "iam:DetachRolePolicy",
}


def test_no_statement_allows_every_action_on_every_resource():
    statements = list(_statements())
    assert any(name == "StackRoleBoundary" for name, _ in statements)
    for name, st in statements:
        both = "*" in _as_list(st["Action"]) and "*" in _as_list(st["Resource"])
        assert not both, f"{name}/{st.get('Sid')}"


def test_stack_role_writes_require_the_boundary():
    allows = [st for st in _deploy_statements() if st["Effect"] == "Allow"]
    seen = set()
    for st in allows:
        actions = BOUNDARY_ACTIONS & set(_as_list(st["Action"]))
        if not actions:
            continue
        seen |= actions
        boundary = st.get("Condition", {}).get("ArnEquals", {}).get("iam:PermissionsBoundary")
        assert boundary == BOUNDARY, f"{st.get('Sid')} has no boundary condition"
    assert seen == BOUNDARY_ACTIONS


def test_attach_role_policy_is_limited_to_named_aws_policies():
    attach = [
        st
        for st in _deploy_statements()
        if st["Effect"] == "Allow" and "iam:AttachRolePolicy" in _as_list(st["Action"])
    ]
    assert attach
    for st in attach:
        arns = _as_list(st["Condition"]["ArnEquals"]["iam:PolicyARN"])
        assert arns
        for arn in arns:
            text = arn["Sub"] if isinstance(arn, dict) else arn
            assert ":iam::aws:policy/" in text and "*" not in text, text


def test_deploy_role_iam_rights_stay_on_the_project_path():
    for st in _deploy_statements():
        if st["Effect"] != "Allow":
            continue
        if not any(a.startswith("iam:") for a in _as_list(st["Action"])):
            continue
        if _as_list(st["Action"]) == ["iam:CreateServiceLinkedRole"]:
            continue
        for res in _as_list(st["Resource"]):
            text = res["Sub"] if isinstance(res, dict) else res
            assert "/agentic-platform-advisor/" in text, f"{st.get('Sid')}: {text}"


def test_deploy_role_cannot_edit_or_drop_the_boundary():
    denies = [st for st in _deploy_statements() if st["Effect"] == "Deny"]
    assert any(st["Resource"] == BOUNDARY and st["Action"] == "iam:*" for st in denies)
    removal = {
        a
        for st in denies
        for a in _as_list(st["Action"])
        if a in {"iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary"}
    }
    assert removal == {"iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary"}


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
