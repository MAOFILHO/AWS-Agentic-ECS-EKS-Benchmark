"""Deploy script: change set preview, y/N gate, env checks (issue #25)."""

import subprocess
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

from scripts import deploy_stack

ROOT = Path(__file__).resolve().parents[1]

STACK = "demo-stack"
CHANGES = [
    {
        "Type": "Resource",
        "ResourceChange": {
            "Action": "Add",
            "LogicalResourceId": "Repo",
            "ResourceType": "AWS::ECR::Repository",
        },
    }
]


@pytest.fixture
def cfn():
    client = boto3.client(
        "cloudformation",
        region_name="us-east-1",
        aws_access_key_id="stub",
        aws_secret_access_key="stub",
    )
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def _stub_new_stack_change_set(stubber):
    stubber.add_client_error(
        "describe_stacks",
        service_error_code="ValidationError",
        service_message=f"Stack with id {STACK} does not exist",
    )
    stubber.add_response(
        "create_change_set",
        {"Id": "cs-arn", "StackId": "stack-arn"},
        {
            "StackName": STACK,
            "ChangeSetName": ANY,
            "ChangeSetType": "CREATE",
            "TemplateBody": "body",
            "Parameters": [{"ParameterKey": "BudgetEmail", "ParameterValue": "a@b.co"}],
            "Capabilities": ["CAPABILITY_NAMED_IAM"],
        },
    )
    stubber.add_response(
        "describe_change_set",
        {"Status": "CREATE_COMPLETE", "Changes": CHANGES},
        {"StackName": STACK, "ChangeSetName": ANY},
    )


def _deploy(client, answer):
    return deploy_stack.deploy(
        client,
        STACK,
        "body",
        {"BudgetEmail": "a@b.co"},
        answer=lambda _prompt: answer,
        sleep=lambda _s: None,
    )


def test_calls_create_then_describe_then_execute(cfn, capsys):
    client, stubber = cfn
    _stub_new_stack_change_set(stubber)
    stubber.add_response(
        "execute_change_set", {}, {"StackName": STACK, "ChangeSetName": ANY}
    )
    stubber.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": STACK,
                    "CreationTime": "2026-10-13T00:00:00Z",
                    "StackStatus": "CREATE_COMPLETE",
                }
            ]
        },
        {"StackName": STACK},
    )

    _deploy(client, "y")

    assert "Add" in capsys.readouterr().out


def test_declined_answer_exits_non_zero_and_executes_nothing(cfn):
    client, stubber = cfn
    _stub_new_stack_change_set(stubber)
    stubber.add_response(
        "delete_change_set", {}, {"StackName": STACK, "ChangeSetName": ANY}
    )

    with pytest.raises(SystemExit) as exc:
        _deploy(client, "n")

    assert exc.value.code not in (0, None)


def test_missing_budget_email_exits_naming_it(monkeypatch, capsys):
    monkeypatch.setenv("AWS_PROFILE", "demo")
    monkeypatch.delenv("BUDGET_EMAIL", raising=False)

    with pytest.raises(SystemExit) as exc:
        deploy_stack.main(
            [
                "--stack",
                STACK,
                "--template",
                "infra/cfn/00-bootstrap.yaml",
                "--env-param",
                "BudgetEmail=BUDGET_EMAIL",
            ]
        )

    assert exc.value.code not in (0, None)
    assert "BUDGET_EMAIL" in str(exc.value.code)


def test_missing_aws_profile_exits_naming_it(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("BUDGET_EMAIL", "a@b.co")

    with pytest.raises(SystemExit) as exc:
        deploy_stack.main(
            ["--stack", STACK, "--template", "infra/cfn/00-bootstrap.yaml"]
        )

    assert "AWS_PROFILE" in str(exc.value.code)


def test_make_bootstrap_runs_the_deploy_script():
    out = subprocess.run(
        ["make", "-n", "bootstrap"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout

    assert "not implemented yet" not in out
    assert "scripts/deploy_stack.py" in out
    assert "infra/cfn/00-bootstrap.yaml" in out


# Optional env parameters (issue #33).


def _run_main(monkeypatch, argv, seen=None):
    sent, seen = {}, {} if seen is None else seen
    monkeypatch.setattr(deploy_stack.boto3, "Session", lambda **kw: seen.update(kw) or _FakeSession())

    def fake_deploy(_cfn, _stack, _body, parameters, answer=input):
        seen["answer"] = answer
        sent.update(parameters)

    monkeypatch.setattr(deploy_stack, "deploy", fake_deploy)
    deploy_stack.main(argv)
    return sent


def test_yes_and_region_skip_the_prompt_and_set_the_client_region(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "demo")
    monkeypatch.setenv("BUDGET_EMAIL", "a@b.co")
    plain, yes = {}, {}
    _run_main(monkeypatch, BOOTSTRAP_ARGS, plain)
    _run_main(monkeypatch, [*BOOTSTRAP_ARGS, "--region", "us-east-2", "--yes"], yes)
    assert plain["answer"] is input and plain["region_name"] is None
    assert yes["answer"]("Execute? ") == "y" and yes["region_name"] == "us-east-2"


class _FakeSession:
    def client(self, _name):
        return object()


BOOTSTRAP_ARGS = [
    "--stack",
    STACK,
    "--template",
    "infra/cfn/00-bootstrap.yaml",
    "--env-param",
    "BudgetEmail=BUDGET_EMAIL",
    "--optional-env-param",
    "ExistingOidcProviderArn=OIDC_PROVIDER_ARN",
]


def test_unset_optional_parameter_is_left_out(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "demo")
    monkeypatch.setenv("BUDGET_EMAIL", "a@b.co")
    monkeypatch.delenv("OIDC_PROVIDER_ARN", raising=False)

    sent = _run_main(monkeypatch, BOOTSTRAP_ARGS)

    assert sent == {"BudgetEmail": "a@b.co"}


def test_set_optional_parameter_is_sent(monkeypatch):
    arn = "arn:aws:iam::ACCOUNT:oidc-provider/token.actions.githubusercontent.com"
    monkeypatch.setenv("AWS_PROFILE", "demo")
    monkeypatch.setenv("BUDGET_EMAIL", "a@b.co")
    monkeypatch.setenv("OIDC_PROVIDER_ARN", arn)

    sent = _run_main(monkeypatch, BOOTSTRAP_ARGS)

    assert sent == {"BudgetEmail": "a@b.co", "ExistingOidcProviderArn": arn}


def test_missing_budget_email_still_fails_with_optional_unset(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "demo")
    monkeypatch.delenv("BUDGET_EMAIL", raising=False)
    monkeypatch.delenv("OIDC_PROVIDER_ARN", raising=False)

    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, BOOTSTRAP_ARGS)

    assert "BUDGET_EMAIL" in str(exc.value.code)


def test_make_bootstrap_passes_the_provider_arn_as_optional():
    out = subprocess.run(
        ["make", "-n", "bootstrap"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout

    assert "--optional-env-param ExistingOidcProviderArn=OIDC_PROVIDER_ARN" in out


# Failed change set detail (issue #37).

LONG_REASON = (
    "Template format error: resource Repo references an unknown parameter; "
    "see the AWS::ECR::Repository docs for the full list of required properties."
)


def _stub_failed_change_set(stubber, reason):
    stubber.add_client_error(
        "describe_stacks",
        service_error_code="ValidationError",
        service_message=f"Stack with id {STACK} does not exist",
    )
    stubber.add_response(
        "create_change_set",
        {"Id": "cs-arn", "StackId": "stack-arn"},
        {
            "StackName": STACK,
            "ChangeSetName": ANY,
            "ChangeSetType": "CREATE",
            "TemplateBody": "body",
            "Parameters": [{"ParameterKey": "BudgetEmail", "ParameterValue": "a@b.co"}],
            "Capabilities": ["CAPABILITY_NAMED_IAM"],
        },
    )
    stubber.add_response(
        "describe_change_set",
        {"Status": "FAILED", "StatusReason": reason},
        {"StackName": STACK, "ChangeSetName": ANY},
    )


def test_failed_change_set_exits_with_full_reason_and_deletes_it(cfn):
    client, stubber = cfn
    _stub_failed_change_set(stubber, LONG_REASON)
    stubber.add_response(
        "delete_change_set", {}, {"StackName": STACK, "ChangeSetName": ANY}
    )

    with pytest.raises(SystemExit) as exc:
        _deploy(client, "y")

    assert exc.value.code not in (0, None)
    assert LONG_REASON in str(exc.value.code)


def test_failed_change_set_reason_is_shown_even_if_delete_fails(cfn, capsys):
    client, stubber = cfn
    _stub_failed_change_set(stubber, LONG_REASON)
    stubber.add_client_error("delete_change_set", service_error_code="Throttling")

    with pytest.raises(ClientError):
        _deploy(client, "y")

    assert LONG_REASON in capsys.readouterr().err


def test_no_changes_change_set_exits_zero_with_message(cfn, capsys):
    client, stubber = cfn
    _stub_failed_change_set(
        stubber, "The submitted information didn't contain changes."
    )
    stubber.add_response(
        "delete_change_set", {}, {"StackName": STACK, "ChangeSetName": ANY}
    )

    _deploy(client, "y")

    assert "no changes." in capsys.readouterr().out
