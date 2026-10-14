"""Build the workload image and push it to ECR, or mirror its base images into ECR.

Usage:
    AWS_PROFILE=<sso-profile> python scripts/push_image.py [--region R] [--allow-dirty] \
        [--base-image <base-images tag>] [--platform linux/amd64]
    AWS_PROFILE=<sso-profile> python scripts/push_image.py --mirror-base [--region R] \
        [--platform linux/amd64]

The image tag is the short git sha. Images are built and pulled for linux/amd64 by default (Fargate
x86_64), whatever the host CPU; pass --platform linux/arm64 for Graviton. Credentials come from the
AWS_PROFILE profile through boto3. The ECR token goes to `docker login` on stdin only. Output masks the account id.
"""

import argparse
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
WORKLOAD_REPO = "triage-workload"
BASE_REPO = "base-images"
PYTHON_BASE = "python:3.12-slim"
DEFAULT_PLATFORM = "linux/amd64"
ENV_FORMAT = "{{range .Config.Env}}{{println .}}{{end}}"
ACCOUNT_ID = re.compile(r"\b\d{12}\b")


def redact(text):
    return ACCOUNT_ID.sub("<account>", text)


def say(text):
    print(redact(text))


def aws_error(err):
    """An AWS error as a clean exit: its message can carry the caller ARN, so the account id."""
    return SystemExit(f"error: AWS call failed: {redact(str(err))}")


def run_docker(args, input=None):
    cmd = ["docker", *args]
    proc = subprocess.run(cmd, input=input, capture_output=True, text=True, cwd=ROOT, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def docker(runner, args, input=None, secret=None):
    code, output = runner(args, input=input)
    if code:
        if secret:
            output = output.replace(secret, "<token>")
        raise SystemExit(f"error: docker {args[0]} failed:\n{redact(output[-2000:])}")
    return output


def resolve_region(flag):
    region = flag or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("error: no AWS region. Pass --region or set AWS_REGION or AWS_DEFAULT_REGION.")
    return region


def image_tag(cwd, allow_dirty):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    if git("status", "--porcelain") and not allow_dirty:
        raise SystemExit("error: uncommitted changes. Commit them or pass --allow-dirty.")
    return git("rev-parse", "--short", "HEAD")


def login(ecr, runner):
    """Log docker in to the registry; return the registry host."""
    try:
        data = ecr.get_authorization_token()["authorizationData"][0]
    except (BotoCoreError, ClientError) as err:
        raise aws_error(err) from None
    user, password = base64.b64decode(data["authorizationToken"]).decode().split(":", 1)
    registry = data["proxyEndpoint"].removeprefix("https://")
    login_args = ["login", "--username", user, "--password-stdin", registry]
    docker(runner, login_args, input=password, secret=password)
    return registry


def push(ecr, runner, tag, base_tag=None, platform=DEFAULT_PLATFORM):
    registry = login(ecr, runner)
    image = f"{registry}/{WORKLOAD_REPO}:{tag}"
    build = ["build", "--platform", platform, "-t", image]
    if base_tag:
        build += ["--build-arg", f"BASE_IMAGE={registry}/{BASE_REPO}:{base_tag}"]
    docker(runner, [*build, "workload/"])
    docker(runner, ["push", image])
    say(image)


def uv_image():
    """The uv image the Dockerfile pins, so the mirror and the build never drift."""
    text = (ROOT / "workload/Dockerfile").read_text()
    return re.search(r"COPY --from=(ghcr\.io/astral-sh/uv:\S+)", text).group(1)


def tag_exists(ecr, tag):
    try:
        ecr.describe_images(repositoryName=BASE_REPO, imageIds=[{"imageTag": tag}])
    except ClientError as err:
        if err.response["Error"]["Code"] == "ImageNotFoundException":
            return False
        raise aws_error(err) from None
    except BotoCoreError as err:
        raise aws_error(err) from None
    return True


def mirror(ecr, runner, platform=DEFAULT_PLATFORM):
    registry = login(ecr, runner)
    for source in (PYTHON_BASE, uv_image()):
        docker(runner, ["pull", "--platform", platform, source])
        if source == PYTHON_BASE:
            # 3.12-slim moves; tag the exact patch release the pull resolved to.
            env = docker(runner, ["image", "inspect", "--format", ENV_FORMAT, source])
            version = re.search(r"^PYTHON_VERSION=(\S+)$", env, re.MULTILINE).group(1)
            tag = f"python-{version}-slim"
        else:
            tag = "uv-" + source.rsplit(":", 1)[1]
        target = f"{registry}/{BASE_REPO}:{tag}"
        if tag_exists(ecr, tag):
            say(f"{target} already mirrored")
            continue
        docker(runner, ["tag", source, target])
        docker(runner, ["push", target])
        say(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--base-image", metavar="TAG", help="Build FROM this base-images tag.")
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, help="Target platform (default: %(default)s).")
    parser.add_argument("--mirror-base", action="store_true", help="Mirror base images only.")
    args = parser.parse_args(argv)

    region = resolve_region(args.region)
    profile = os.environ.get("AWS_PROFILE")
    if not profile:
        raise SystemExit("error: AWS_PROFILE is not set. Export it and run again.")
    ecr = boto3.Session(profile_name=profile, region_name=region).client("ecr")
    if args.mirror_base:
        mirror(ecr, run_docker, args.platform)
    else:
        push(ecr, run_docker, image_tag(ROOT, args.allow_dirty), args.base_image, args.platform)


if __name__ == "__main__":
    sys.exit(main())
