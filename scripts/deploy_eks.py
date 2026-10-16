"""Deploy one EKS cell: network, cluster and controller role stacks, then the controller and the workload with Helm.

Usage:
    AWS_PROFILE=<sso-profile> AWS_REGION=<region> IMAGE_URI=<ecr-image-uri> \
    python -m scripts.deploy_eks --cell eks-ec2|eks-fargate --run-id <id> [--region <region>] [--yes]

Stacks go through deploy_stack.py (change sets, y/N per stack; --yes skips the prompts). The kubeconfig is a temp file,
never ~/.kube/config. Credentials come from the AWS_PROFILE profile through boto3. Output masks
12-digit account ids.
"""

import argparse
import functools
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from scripts import deploy_stack

ROOT = Path(__file__).resolve().parents[1]
CFN = ROOT / "infra" / "cfn"
WORKLOAD_CHART = ROOT / "infra" / "helm" / "workload"
CELLS = {"eks-ec2": ("EC2", "values-ec2.yaml"), "eks-fargate": ("FARGATE", "values-fargate.yaml")}
NODEGROUP = "workload"  # NodegroupName in 40-eks.yaml
WORKLOAD_RELEASE = "workload"  # delete_run.py uninstalls these two release names
LBC_RELEASE = "aws-load-balancer-controller"
LBC_NAMESPACE = "kube-system"
LBC_CHART_REPO = "https://aws.github.io/eks-charts"
LBC_CHART_VERSION = "3.6.0"
# Kubernetes minors are probed from here up through the EKS AMI public parameters (SPEC section 10).
SSM_AMI = "/aws/service/eks/optimized-ami/1.{}/amazon-linux-2023/x86_64/standard/recommended/image_id"
FIRST_MINOR = 30
ALB_INTERVAL = 15


def mask(text):
    return re.sub(r"\b\d{12}\b", "*" * 12, str(text))


def missing_inputs(run_id, image_uri, region, profile):
    named = {"RUN_ID": run_id, "IMAGE_URI": image_uri, "region (AWS_REGION or --region)": region,
             "AWS_PROFILE": profile}
    return [name for name, value in named.items() if not value]


def split_image(uri):
    """`repo:tag` → (repo, tag). The tag is after the last `:` past the last `/`."""
    repo, sep, tag = uri.rpartition(":")
    if not sep or "/" in tag:
        raise SystemExit("error: IMAGE_URI has no tag. Use the <repo>:<tag> that push-image printed.")
    return repo, tag


def kubernetes_version(ssm):
    """Newest Kubernetes minor that has an EKS AL2023 x86_64 AMI public parameter."""
    newest, minor = None, FIRST_MINOR
    while params := ssm.get_parameters(Names=[SSM_AMI.format(m) for m in range(minor, minor + 10)])[
        "Parameters"
    ]:
        newest = max(int(p["Name"].split("/")[5].split(".")[1]) for p in params)
        minor += 10
    if newest is None:
        raise SystemExit("error: no EKS AMI public parameter found in SSM for this region.")
    return f"1.{newest}"


def stack_output(cfn, stack, key):
    for out in cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", []):
        if out["OutputKey"] == key:
            return out["OutputValue"]
    raise SystemExit(f"error: {stack} has no {key} output.")


def sh(run, args):
    try:
        return run(args, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as err:
        reason = (err.stderr or str(err)).strip().splitlines()[0]
        raise SystemExit(mask(f"error: {args[0]} {args[1]} failed: {reason}")) from None


def wait_for_alb(run, kubeconfig, timeout, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while not (host := sh(run, ["kubectl", "get", "ingress", WORKLOAD_RELEASE, "--kubeconfig", kubeconfig,
                                "-o", "jsonpath={.status.loadBalancer.ingress[0].hostname}"]).strip()):
        if clock() >= deadline:
            raise SystemExit(f"error: Ingress {WORKLOAD_RELEASE} has no ALB hostname after {timeout} s.")
        print("waiting for the ALB hostname...", flush=True)
        sleep(ALB_INTERVAL)
    return host


def deploy(cfn, ssm, eks, run_id, cell, region, image_uri, alb_timeout=900,
           deploy_one=deploy_stack.deploy, run=subprocess.run, sleep=time.sleep):
    compute_type, values_file = CELLS[cell]
    repo, tag = split_image(image_uri)
    version = kubernetes_version(ssm)
    print(f"Kubernetes version {version} (SSM public parameters).")

    network, eks_stack, lbc_stack = f"{run_id}-network", f"{run_id}-{cell}", f"{run_id}-eks-lbc-iam"
    deploy_one(cfn, network, (CFN / "10-network.yaml").read_text(), {"RunId": run_id})
    deploy_one(cfn, eks_stack, (CFN / "40-eks.yaml").read_text(), {
        "RunId": run_id, "NetworkStackName": network, "ComputeType": compute_type,
        "KubernetesVersion": version})
    deploy_one(cfn, lbc_stack, (CFN / "41-eks-lbc-iam.yaml").read_text(), {
        "RunId": run_id, "EksStackName": eks_stack, "Cell": cell})

    cluster = stack_output(cfn, eks_stack, "ClusterName")
    vpc_id = stack_output(cfn, network, "VpcId")
    if compute_type == "EC2":
        eks.get_waiter("nodegroup_active").wait(clusterName=cluster, nodegroupName=NODEGROUP)
        print(f"{cluster}: node group {NODEGROUP} ACTIVE.")

    with tempfile.TemporaryDirectory() as tmp:
        kubeconfig = os.path.join(tmp, "config")  # never ~/.kube/config (SPEC risk table)
        sh(run, ["aws", "eks", "update-kubeconfig", "--name", cluster, "--region", region,
                 "--kubeconfig", kubeconfig])
        # vpcId and region are set because pods cannot read them from instance metadata.
        # Shield and WAF stay off: 41-eks-lbc-iam does not grant them.
        sh(run, ["helm", "upgrade", "--install", LBC_RELEASE, LBC_RELEASE, "--repo", LBC_CHART_REPO,
                 "--version", LBC_CHART_VERSION, "--namespace", LBC_NAMESPACE, "--wait",
                 "--set", f"clusterName={cluster}", "--set", f"region={region}", "--set", f"vpcId={vpc_id}",
                 "--set", "serviceAccount.create=true", "--set", f"serviceAccount.name={LBC_RELEASE}",
                 "--set", "enableShield=false", "--set", "enableWaf=false", "--set", "enableWafv2=false",
                 "--kubeconfig", kubeconfig])
        print(f"{cluster}: {LBC_RELEASE} {LBC_CHART_VERSION} installed.")
        sh(run, ["helm", "upgrade", "--install", WORKLOAD_RELEASE, str(WORKLOAD_CHART), "--wait",
                 "--values", str(WORKLOAD_CHART / values_file),
                 "--set", f"image.repository={repo}", "--set", f"image.tag={tag}",
                 "--kubeconfig", kubeconfig])
        print(f"{cluster}: {WORKLOAD_RELEASE} installed, image tag {tag}.")
        host = wait_for_alb(run, kubeconfig, alb_timeout, sleep=sleep)
    print(mask(f"ALB: http://{host}"))
    return host


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cell", required=True, choices=sorted(CELLS))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--region")
    parser.add_argument("--yes", action="store_true", help="Execute every change set without the y/N prompt.")
    args = parser.parse_args(argv)

    region = args.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    image_uri, profile = os.environ.get("IMAGE_URI"), os.environ.get("AWS_PROFILE")
    if missing := missing_inputs(args.run_id, image_uri, region, profile):
        raise SystemExit(f"error: not set: {', '.join(missing)}. Nothing was deployed.")

    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        deploy_one = deploy_stack.deploy
        if args.yes:
            deploy_one = functools.partial(deploy_stack.deploy, answer=lambda _prompt: "y")
        deploy(session.client("cloudformation"), session.client("ssm"), session.client("eks"),
               args.run_id, args.cell, region, image_uri, deploy_one=deploy_one)
    except (BotoCoreError, ClientError) as err:
        raise SystemExit(mask(f"error: {err}")) from None


if __name__ == "__main__":
    sys.exit(main())
