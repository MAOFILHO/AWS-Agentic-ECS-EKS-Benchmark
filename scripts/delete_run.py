"""Delete every stack of one benchmark run, newest first, then check nothing tagged with the run is left.

Usage:
    AWS_PROFILE=<sso-profile> AWS_REGION=<region> \
    python scripts/delete_run.py --run-id <id> [--region <region>] [--yes] [--leftover-timeout 900]

Only stacks whose name starts with `<id>-` are touched. Credentials come from the AWS_PROFILE
profile through boto3. Output masks 12-digit account ids.
"""

import argparse
import os
import re
import sys
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError, WaiterError

# The tag index keeps these for minutes after delete; trust EC2's and ECS's own state instead.
LAGGING = {
    "natgateway": ("describe_nat_gateways", "Filter", "nat-gateway-id", "NatGateways"),
    "vpc-endpoint": ("describe_vpc_endpoints", "Filters", "vpc-endpoint-id", "VpcEndpoints"),
}
ECS_KINDS = {"cluster", "service", "task-definition", "task"}
CHECK_INTERVAL = 30


def mask(text):
    return re.sub(r"\b\d{12}\b", "*" * 12, str(text))


def resolve_region(flag):
    region = flag or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit(
            "error: no region. Pass --region or set AWS_REGION or AWS_DEFAULT_REGION."
        )
    return region


def live_statuses(cfn):
    """Every stack status the installed API knows, except DELETE_COMPLETE."""
    statuses = cfn.meta.service_model.shape_for("StackStatus").enum
    return [s for s in statuses if s != "DELETE_COMPLETE"]


def run_stacks(cfn, run_id):
    """Names of the run's live stacks, newest first."""
    pages = cfn.get_paginator("list_stacks").paginate(StackStatusFilter=live_statuses(cfn))
    stacks = [
        s
        for page in pages
        for s in page["StackSummaries"]
        if s["StackName"].startswith(f"{run_id}-")
    ]
    stacks.sort(key=lambda s: s["CreationTime"], reverse=True)
    return [s["StackName"] for s in stacks]


def delete_stacks(cfn, names):
    waiter = cfn.get_waiter("stack_delete_complete")
    for name in names:
        print(f"{name}: deleting...")
        cfn.delete_stack(StackName=name)
        try:
            waiter.wait(StackName=name)
        except WaiterError:
            resources = cfn.describe_stack_resources(StackName=name)["StackResources"]
            for r in resources:
                if r["ResourceStatus"] == "DELETE_FAILED":
                    print(
                        mask(
                            f"  DELETE_FAILED {r['ResourceType']} {r.get('PhysicalResourceId', '')}"
                            f" ({r['LogicalResourceId']}): {r.get('ResourceStatusReason', '')}"
                        ),
                        file=sys.stderr,
                    )
            raise SystemExit(f"error: {name} ended in DELETE_FAILED.") from None
        print(f"{name}: deleted.")


def _ecs_stale(ecs, kind, arn, resource_id):
    """True when ECS reports the item gone: INACTIVE, STOPPED, DELETE_IN_PROGRESS or unknown."""
    cluster = resource_id.split("/")[0]
    try:
        if kind == "cluster":
            items = ecs.describe_clusters(clusters=[arn])["clusters"]
            key, dead = "status", {"INACTIVE"}
        elif kind == "service":
            items = ecs.describe_services(cluster=cluster, services=[arn])["services"]
            key, dead = "status", {"INACTIVE"}
        elif kind == "task-definition":
            items = [ecs.describe_task_definition(taskDefinition=arn)["taskDefinition"]]
            key, dead = "status", {"INACTIVE", "DELETE_IN_PROGRESS"}
        else:
            items = ecs.describe_tasks(cluster=cluster, tasks=[arn])["tasks"]
            key, dead = "lastStatus", {"STOPPED"}
    except ClientError as err:
        # A gone cluster fails service and task lookups; ECS rejects a gone task definition.
        gone = "ClusterNotFoundException" if kind != "task-definition" else "ClientException"
        if err.response["Error"]["Code"] == gone:
            return True
        raise
    return all(item[key] in dead for item in items)


def _gone(ec2, ecs, arn):
    kind, _, resource_id = arn.split(":", 5)[5].partition("/")
    if kind in ECS_KINDS:
        return _ecs_stale(ecs, kind, arn, resource_id)
    if kind not in LAGGING:
        return False
    method, filter_param, filter_name, key = LAGGING[kind]
    found = getattr(ec2, method)(
        **{filter_param: [{"Name": filter_name, "Values": [resource_id]}]}
    )[key]
    return all(item["State"].lower() == "deleted" for item in found)


def leftovers(tagging, ec2, ecs, run_id):
    pages = tagging.get_paginator("get_resources").paginate(
        TagFilters=[{"Key": "run-id", "Values": [run_id]}]
    )
    arns = [m["ResourceARN"] for page in pages for m in page["ResourceTagMappingList"]]
    return [arn for arn in arns if not _gone(ec2, ecs, arn)]


def check_leftovers(tagging, ec2, ecs, run_id, timeout, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    attempt = 0
    while found := leftovers(tagging, ec2, ecs, run_id):
        attempt += 1
        if clock() >= deadline:
            for arn in found:
                print(f"  leftover {mask(arn)}", file=sys.stderr)
            raise SystemExit(f"error: {len(found)} resource(s) tagged run-id={run_id} still live.")
        print(
            f"checking leftovers (attempt {attempt}, {len(found)} left,"
            f" next check in {CHECK_INTERVAL} s)",
            flush=True,
        )
        sleep(CHECK_INTERVAL)
    print(f"run-id={run_id}: no leftovers.")


def teardown(cfn, ec2, ecs, tagging, run_id, timeout, answer=input):
    names = run_stacks(cfn, run_id)
    print(f"Stacks to delete, newest first: {', '.join(names) or 'none'}")
    if names and answer("Delete these stacks? [y/N] ").strip() != "y":
        raise SystemExit("Aborted. Nothing was deleted.")
    delete_stacks(cfn, names)
    check_leftovers(tagging, ec2, ecs, run_id, timeout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--region")
    parser.add_argument("--yes", action="store_true", help="Skip the y/N prompt.")
    parser.add_argument(
        "--leftover-timeout",
        type=int,
        default=900,
        metavar="SECONDS",
        help="How long to retry the leftover check (default 900).",
    )
    args = parser.parse_args(argv)
    if not args.run_id.strip():
        raise SystemExit("error: --run-id is empty.")

    region = resolve_region(args.region)
    session = boto3.Session(region_name=region)
    try:
        teardown(
            session.client("cloudformation"),
            session.client("ec2"),
            session.client("ecs"),
            session.client("resourcegroupstaggingapi"),
            args.run_id,
            args.leftover_timeout,
            answer=(lambda _prompt: "y") if args.yes else input,
        )
    except (BotoCoreError, ClientError) as err:
        raise SystemExit(mask(f"error: {err}")) from None


if __name__ == "__main__":
    sys.exit(main())
