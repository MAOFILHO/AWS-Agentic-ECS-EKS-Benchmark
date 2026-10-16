"""Push script and base image mirror: stub ECR client, stub docker runner (issue #43)."""

import base64
import re
import subprocess
from pathlib import Path

import boto3
import pytest
import yaml
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from scripts import push_image
from tests.test_bootstrap_oidc import CfnLoader

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "9" * 12  # built at runtime: no tracked file may hold an account id
REGISTRY = f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com"
TOKEN = "s3cr3t-ecr-password"
ACCOUNT_ID = re.compile(r"\b\d{12}\b")


@pytest.fixture
def ecr():
    client = boto3.client(
        "ecr",
        region_name="us-east-1",
        aws_access_key_id="stub",
        aws_secret_access_key="stub",
    )
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def _stub_token(stubber):
    token = base64.b64encode(f"AWS:{TOKEN}".encode()).decode()
    data = {"authorizationToken": token, "proxyEndpoint": f"https://{REGISTRY}"}
    stubber.add_response("get_authorization_token", {"authorizationData": [data]})


class Docker:
    """Stub docker runner: records each call, echoes the registry like the real CLI."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, args, input=None):
        self.calls.append((args, input))
        if args[0] == self.fail_on:
            return 1, f"denied: {REGISTRY} password {TOKEN}"
        if args[0] == "image":
            return 0, "PYTHON_VERSION=3.12.13\nPATH=/usr/bin\n"
        return 0, f"pushed to {REGISTRY}"


@pytest.fixture
def repo(tmp_path):
    def git(cmd):
        out = subprocess.run(["git", *cmd.split()], cwd=tmp_path, capture_output=True, check=True)
        return out.stdout.decode().strip()

    git("init -q")
    git("-c user.email=a@b.co -c user.name=t commit -q --allow-empty -m x")
    return tmp_path, git("rev-parse --short HEAD")


def _assert_clean(text):
    assert TOKEN not in text
    assert not ACCOUNT_ID.search(text), text


def test_no_region_exits_and_names_it(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(SystemExit) as exc:
        push_image.resolve_region(None)
    assert "region" in str(exc.value.code)


def test_cli_without_region_exits_non_zero_and_names_it(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setenv("AWS_PROFILE", "stub")
    with pytest.raises(SystemExit) as exc:
        push_image.main([])
    assert exc.value.code not in (0, None)
    assert "region" in str(exc.value.code)


def test_ecr_access_denied_does_not_print_account_id(ecr, capsys):
    # [qa] witness: a role without ecr:GetAuthorizationToken. AWS puts the caller ARN,
    # and so the account id, in the error message; it must not reach the output.
    client, stubber = ecr
    stubber.add_client_error(
        "get_authorization_token",
        service_error_code="AccessDeniedException",
        service_message=f"User: arn:aws:sts::{ACCOUNT}:assumed-role/dev/me is not authorized",
    )
    try:
        push_image.push(client, Docker(), "abc1234")
    except SystemExit as exc:
        _assert_clean(str(exc.code))
    except ClientError as exc:  # uncaught, its message is printed in the traceback
        _assert_clean(str(exc))
    out = capsys.readouterr()
    _assert_clean(out.out + out.err)


def test_mirror_describe_denied_exits_without_account_id(ecr):
    client, stubber = ecr
    _stub_token(stubber)
    stubber.add_client_error(
        "describe_images",
        service_error_code="AccessDeniedException",
        service_message=f"User: arn:aws:sts::{ACCOUNT}:assumed-role/dev/me is not authorized",
    )
    with pytest.raises(SystemExit) as exc:
        push_image.mirror(client, Docker())
    _assert_clean(str(exc.value.code))


def test_region_flag_then_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    assert push_image.resolve_region(None) == "eu-west-1"
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert push_image.resolve_region(None) == "us-west-2"
    assert push_image.resolve_region("us-east-1") == "us-east-1"


def test_tag_is_short_git_sha(repo):
    path, sha = repo
    assert push_image.image_tag(path, allow_dirty=False) == sha


def test_dirty_tree_exits_unless_allowed(repo):
    path, sha = repo
    (path / "new.txt").write_text("x")
    with pytest.raises(SystemExit) as exc:
        push_image.image_tag(path, allow_dirty=False)
    assert "--allow-dirty" in str(exc.value.code)
    assert push_image.image_tag(path, allow_dirty=True) == sha


def test_push_logs_in_builds_tags_and_pushes(ecr, capsys):
    client, stubber = ecr
    _stub_token(stubber)
    docker = Docker()
    push_image.push(client, docker, "abc1234")

    login, build, push = docker.calls
    assert login == (["login", "--username", "AWS", "--password-stdin", REGISTRY], TOKEN)
    image = f"{REGISTRY}/triage-workload:abc1234"
    assert build[0] == ["build", "--platform", "linux/amd64", "-t", image, "workload/"]
    assert push[0] == ["push", image]
    out = capsys.readouterr().out
    assert "<account>.dkr.ecr.us-east-1.amazonaws.com/triage-workload:abc1234" in out
    _assert_clean(out)


def test_push_from_mirrored_base(ecr):
    client, stubber = ecr
    _stub_token(stubber)
    docker = Docker()
    push_image.push(client, docker, "abc1234", base_tag="python-3.12.13-slim")
    build = docker.calls[1][0]
    assert f"BASE_IMAGE={REGISTRY}/base-images:python-3.12.13-slim" in build


def test_k6_builds_loadtests_from_the_mirrored_k6_base_for_amd64(ecr, capsys):
    client, stubber = ecr
    _stub_token(stubber)
    docker = Docker()
    push_image.push_k6(client, docker, "abc1234")
    _, build, push = docker.calls
    image = f"{REGISTRY}/triage-workload:k6-abc1234"
    assert build[0] == ["build", "--platform", "linux/amd64", "-t", image,
                        "--build-arg", f"BASE_IMAGE={REGISTRY}/base-images:k6-1.3.0", "loadtests/"]
    assert push[0] == ["push", image]
    out = capsys.readouterr().out
    assert "<account>.dkr.ecr.us-east-1.amazonaws.com/triage-workload:k6-abc1234" in out
    _assert_clean(out)


def test_cli_k6_mode_uses_the_git_sha_and_the_platform(monkeypatch):
    seen = []
    monkeypatch.setenv("AWS_PROFILE", "stub")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(push_image.boto3, "Session", lambda **kw: type("S", (), {"client": lambda *a: None})())
    monkeypatch.setattr(push_image, "image_tag", lambda *a: "abc1234")
    monkeypatch.setattr(push_image, "push_k6", lambda *a: seen.append(a[2:]))
    push_image.main(["--k6"])
    push_image.main(["--k6", "--platform", "linux/arm64"])
    assert seen == [("abc1234", "linux/amd64"), ("abc1234", "linux/arm64")]


def test_make_push_k6_runs_the_k6_mode():
    out = subprocess.run(["make", "-n", "push-k6"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert "scripts/push_image.py --k6" in out


def test_k6_dockerfile_takes_the_base_image_argument():
    text = (ROOT / "loadtests/Dockerfile").read_text()
    assert f"ARG BASE_IMAGE={push_image.K6_BASE}" in text and "FROM ${BASE_IMAGE}" in text


def test_docker_failure_hides_token_and_account(ecr, capsys):
    client, stubber = ecr
    _stub_token(stubber)
    with pytest.raises(SystemExit) as exc:
        push_image.push(client, Docker(fail_on="login"), "abc1234")
    _assert_clean(str(exc.value.code) + capsys.readouterr().out)


def test_mirror_pushes_exact_versions_and_skips_existing(ecr, capsys):
    client, stubber = ecr
    _stub_token(stubber)
    stubber.add_client_error("describe_images", service_error_code="ImageNotFoundException")
    stubber.add_response("describe_images", {"imageDetails": [{"imageTags": ["uv-0.11.33"]}]})
    stubber.add_client_error("describe_images", service_error_code="ImageNotFoundException")
    docker = Docker()
    push_image.mirror(client, docker)

    cmds = [args for args, _ in docker.calls]
    python = f"{REGISTRY}/base-images:python-3.12.13-slim"
    amd64 = ["pull", "--platform", "linux/amd64"]
    assert [*amd64, "python:3.12-slim"] in cmds and [*amd64, push_image.uv_image()] in cmds
    assert ["tag", "python:3.12-slim", python] in cmds
    assert ["push", python] in cmds
    # uv tag already in the immutable repository: no second push.
    assert not any(c[0] == "push" and "uv-" in c[1] for c in cmds)
    k6 = f"{REGISTRY}/base-images:k6-1.3.0"
    assert [*amd64, "grafana/k6:1.3.0"] in cmds
    assert ["tag", "grafana/k6:1.3.0", k6] in cmds
    assert ["push", k6] in cmds
    _assert_clean(capsys.readouterr().out)


def test_platform_flag_overrides_build_and_mirror_pull(ecr):
    client, stubber = ecr
    _stub_token(stubber)
    docker = Docker()
    push_image.push(client, docker, "abc1234", platform="linux/arm64")
    assert docker.calls[1][0][:3] == ["build", "--platform", "linux/arm64"]

    _stub_token(stubber)
    for _ in range(3):
        stubber.add_response("describe_images", {"imageDetails": [{"imageTags": ["x"]}]})
    docker = Docker()
    push_image.mirror(client, docker, platform="linux/arm64")
    pulls = [args for args, _ in docker.calls if args[0] == "pull"]
    assert pulls and all(p[:3] == ["pull", "--platform", "linux/arm64"] for p in pulls)


def test_cli_platform_defaults_to_amd64_and_overrides(monkeypatch):
    seen = []
    monkeypatch.setenv("AWS_PROFILE", "stub")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(
        push_image.boto3,
        "Session",
        lambda **kw: type("S", (), {"client": lambda *a: None})(),
    )
    monkeypatch.setattr(push_image, "image_tag", lambda *a: "abc1234")
    monkeypatch.setattr(push_image, "push", lambda *a: seen.append(a[4]))
    monkeypatch.setattr(push_image, "mirror", lambda *a: seen.append(a[2]))
    push_image.main([])
    push_image.main(["--platform", "linux/arm64"])
    push_image.main(["--mirror-base"])
    assert seen == ["linux/amd64", "linux/arm64", "linux/amd64"]


def test_dockerfile_base_image_argument_defaults_to_docker_hub_name():
    text = (ROOT / "workload/Dockerfile").read_text()
    assert "ARG BASE_IMAGE=python:3.12-slim" in text
    assert "FROM ${BASE_IMAGE}" in text


def test_base_images_repository_matches_workload_repository():
    template = yaml.load((ROOT / "infra/cfn/00-bootstrap.yaml").read_text(), Loader=CfnLoader)
    res = template["Resources"]
    base, workload = res["BaseImagesRepository"], res["WorkloadRepository"]
    assert base["Type"] == "AWS::ECR::Repository"
    assert base["Properties"]["RepositoryName"] == "base-images"
    assert base["Properties"]["ImageTagMutability"] == "IMMUTABLE"
    assert {k: v for k, v in base["Properties"].items() if k != "RepositoryName"} == {
        k: v for k, v in workload["Properties"].items() if k != "RepositoryName"
    }
    assert "BaseImagesRepositoryUri" in template["Outputs"]


def test_guard_immutable_rule_selects_every_ecr_repository():
    rules = (ROOT / "infra/cfn-guard/rules.guard").read_text()
    assert "let ecr_repos = Resources.*[ Type == 'AWS::ECR::Repository' ]" in rules
    assert "rule ecr_immutable_tags when %ecr_repos !empty" in rules
