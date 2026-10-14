"""Deploy one CloudFormation template through a change set: preview, ask y/N, execute, wait.

Usage:
    AWS_PROFILE=<sso-profile> BUDGET_EMAIL=<address> \
    python scripts/deploy_stack.py --stack <name> --template <file> \
        [--env-param <CfnParameter>=<ENV_VAR> ...] \
        [--optional-env-param <CfnParameter>=<ENV_VAR> ...]

Credentials come from the AWS_PROFILE profile through boto3. This script never reads or prints them.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

PENDING = ("CREATE_PENDING", "CREATE_IN_PROGRESS")
NO_CHANGES = ("didn't contain changes", "No updates are to be performed")


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"error: {name} is not set. Export it and run again.")
    return value


def change_set_type(cfn, stack):
    try:
        status = cfn.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
    except ClientError as err:
        if "does not exist" in str(err):
            return "CREATE"
        raise
    # A declined first deploy leaves an empty stack in REVIEW_IN_PROGRESS; it still needs CREATE.
    return "CREATE" if status == "REVIEW_IN_PROGRESS" else "UPDATE"


def deploy(cfn, stack, template_body, parameters, answer=input, sleep=time.sleep):
    kind = change_set_type(cfn, stack)
    name = f"deploy-{int(time.time())}"
    cfn.create_change_set(
        StackName=stack,
        ChangeSetName=name,
        ChangeSetType=kind,
        TemplateBody=template_body,
        Parameters=[
            {"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()
        ],
        Capabilities=["CAPABILITY_NAMED_IAM"],
    )
    while (cs := cfn.describe_change_set(StackName=stack, ChangeSetName=name))[
        "Status"
    ] in PENDING:
        sleep(5)

    if cs["Status"] == "FAILED":
        reason = cs.get("StatusReason", "")
        no_changes = any(text in reason for text in NO_CHANGES)
        if not no_changes:
            # Show the detail before the delete call, so a failed delete cannot hide it.
            print(f"error: change set failed: {reason}", file=sys.stderr)
        cfn.delete_change_set(StackName=stack, ChangeSetName=name)
        if no_changes:
            print(f"{stack}: no changes.")
            return
        raise SystemExit(f"error: change set failed: {reason}")

    print(f"{stack}: {kind} change set {name}")
    for change in cs.get("Changes", []):
        rc = change["ResourceChange"]
        print(f"  {rc['Action']:<8} {rc['ResourceType']:<36} {rc['LogicalResourceId']}")

    if answer("Execute these changes? [y/N] ").strip() != "y":
        cfn.delete_change_set(StackName=stack, ChangeSetName=name)
        raise SystemExit("Aborted. Change set deleted; nothing was applied.")

    cfn.execute_change_set(StackName=stack, ChangeSetName=name)
    waiter = cfn.get_waiter(
        "stack_create_complete" if kind == "CREATE" else "stack_update_complete"
    )
    try:
        waiter.wait(StackName=stack)
    except WaiterError as err:
        raise SystemExit(f"error: {stack} did not finish: {err}") from None
    print(f"{stack}: done.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument(
        "--env-param",
        action="append",
        default=[],
        metavar="PARAM=ENV_VAR",
        help="Pass the value of ENV_VAR as CloudFormation parameter PARAM.",
    )
    parser.add_argument(
        "--optional-env-param",
        action="append",
        default=[],
        metavar="PARAM=ENV_VAR",
        help="Like --env-param, but leave PARAM out when ENV_VAR is unset.",
    )
    args = parser.parse_args(argv)

    profile = required_env("AWS_PROFILE")
    parameters = {}
    for pair in args.env_param:
        param, _, env_var = pair.partition("=")
        parameters[param] = required_env(env_var)
    for pair in args.optional_env_param:
        param, _, env_var = pair.partition("=")
        if os.environ.get(env_var):
            parameters[param] = os.environ[env_var]

    cfn = boto3.Session(profile_name=profile).client("cloudformation")
    deploy(cfn, args.stack, args.template.read_text(), parameters)


if __name__ == "__main__":
    sys.exit(main())
