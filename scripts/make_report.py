"""Redo a report: results/<run-id>/metrics.json to results/<run-id>/report.md, with no AWS deploy.

Usage:
    python -m scripts.make_report --run-id <id> [--region REGION]

Runs only the reporter node. Bedrock is still called, in --region, AWS_REGION or AWS_DEFAULT_REGION.
Exits 1 when metrics.json is missing or bad, or the reporter fails after its tries.
"""

import argparse
import os
import sys
from pathlib import Path

import boto3

from advisor.reporter import LLM, BedrockLLM, make_reporter
from advisor.schemas import MetricsError
from advisor.state import Capacity, RunState

# make_reporter takes a RunState; only run_id and errors are read, so the rest is a placeholder.
PLACEHOLDER = Capacity(vcpu=1, memory_mib=1, min_replicas=1, max_replicas=1)


def main(argv=None, root: Path = Path("."), llm: LLM | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--region")
    args = parser.parse_args(argv)
    region = args.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if llm is None:
        if not region:
            print("error: no region. Pass --region or set AWS_REGION or AWS_DEFAULT_REGION.", file=sys.stderr)
            return 1
        llm = BedrockLLM(boto3.client("bedrock-runtime", region_name=region))
    state = RunState(run_id=args.run_id, cells=[], capacity=PLACEHOLDER, region=region or "none")
    if not (root / "results" / args.run_id / "metrics.json").is_file():
        print(f"error: no results/{args.run_id}/metrics.json", file=sys.stderr)
        return 1
    try:
        update = make_reporter(llm, root)(state)
    except MetricsError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if update["status"] != "reported":
        print(f"error: {update['errors'][-1]}", file=sys.stderr)
        return 1
    print(root / "results" / args.run_id / "report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
