"""Command line: `python -m advisor run ...` and `python -m advisor resume <run-id>`.

Prints the estimate and the run matrix, then asks y/N before any spend. A yes deploys every cell,
loads it with k6, collects the raw files, then teardown deletes them on every path. Prints no account id.
"""

import argparse
import os
import signal
import sys
import time
from functools import partial
from pathlib import Path
from typing import get_args

import boto3
from langgraph.types import Command

from advisor import rawformats as rf
from advisor.analyst import analyst_node
from advisor.collector import make_collector
from advisor.deployer import make_deployer
from advisor.gate import make_gate
from advisor.graph import build_graph
from advisor.load_runner import make_load_runner
from advisor.planner import DEFAULT_CELLS, PROFILES, PlanConfig, make_planner
from advisor.reporter import BedrockLLM, make_reporter
from advisor.schemas import RunConfig
from advisor.state import Capacity, CellName, CellSpec, RunState
from advisor.store import create_run_dirs, open_checkpointer
from advisor.teardown import make_teardown

# Capacity and usage per run; the values match the 30-ecs and 40-eks template defaults.
RUN_CONFIG = Path(__file__).resolve().parent.parent / "runs" / "default.json"


def resolve_region(flag: str | None) -> str:
    # Same rule as scripts/delete_run.py: no default region.
    region = flag or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        sys.exit("error: no region. Pass --region or set AWS_REGION or AWS_DEFAULT_REGION.")
    return region


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m advisor")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="plan a run, show its estimate, ask before any spend")
    run.add_argument("--cells", default=",".join(DEFAULT_CELLS), help="comma-separated cells")
    run.add_argument("--profile", choices=PROFILES, help="one load profile (default: all)")
    run.add_argument("--region")
    run.add_argument("--auto-approve-under", type=float, metavar="USD")
    run.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    resume = sub.add_parser("resume", help="pick up a run from its checkpoint")
    resume.add_argument("run_id")
    args = parser.parse_args(argv)
    if args.command == "run":
        args.cells = [c.strip() for c in args.cells.split(",") if c.strip()]
        unknown = set(args.cells) - set(get_args(CellName))
        if unknown or not args.cells:
            parser.error(f"--cells: unknown {sorted(unknown)}; choose from {get_args(CellName)}")
    return args


class Terminated(KeyboardInterrupt):
    """SIGTERM, raised the way Ctrl+C is so both take one path."""


def raise_terminated(signum, frame):
    raise Terminated


def shielded(node):
    """Run a node with Ctrl+C and SIGTERM ignored (children inherit that): a stop must not leave stacks up."""

    def run(state):
        old = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)}
        print("teardown is running, please wait", flush=True)
        try:
            return node(state)
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)

    return run


def deploy_nodes(root: Path, config: dict) -> dict:
    """The nodes that touch AWS. They read AWS_PROFILE, IMAGE_URI and K6_IMAGE_URI (repo:tag in ECR) from the env."""
    profile = os.environ.get("AWS_PROFILE")

    def aws(service: str, region: str):  # the default session reads AWS_PROFILE
        return boto3.client(service, region_name=region)

    return {"deployer": make_deployer(profile, os.environ.get("IMAGE_URI"), root,
                                             k6_image_uri=os.environ.get("K6_IMAGE_URI")),
            "load_runner": make_load_runner(aws, root, config["plan"]["duration_s"], config["load"]["peak_rps"]),
            "collector": make_collector(aws, profile, root),
            "analyst": partial(analyst_node, root=root,
                               harness_cpu_threshold=config.get("harness_cpu_threshold", 70.0)),
            "reporter": make_bedrock_reporter(aws, root),
            "teardown": shielded(make_teardown(profile, root))}


def make_bedrock_reporter(aws, root: Path):
    """The reporter on Bedrock in the run's region. The client is made only when the node runs.

    Any error, such as no Bedrock access, becomes status "report_failed", so the run still reaches teardown.
    """

    def reporter(state: RunState) -> dict:
        try:
            return make_reporter(BedrockLLM(aws("bedrock-runtime", state.region)), root)(state)
        except Exception as err:  # noqa: BLE001 - a reporter error must not skip teardown
            return {"status": "report_failed", "errors": [*state.errors, rf.scrub(f"reporter: {type(err).__name__}: {err}")]}

    return reporter


def invoke(graph, cfg: dict, payload, teardown):
    """graph.invoke, but an interrupt after the yes runs teardown and records the status "interrupted".

    Before the yes nothing is deployed, so the interrupt passes through.
    """
    try:
        return graph.invoke(payload, cfg)
    except KeyboardInterrupt:
        values = graph.get_state(cfg).values
        if not values.get("approved"):
            raise
        print("interrupted: tearing down", flush=True)
        done = teardown(RunState(**{**values, "status": "interrupted"}))
        graph.update_state(cfg, {"status": "interrupted", **done}, as_node="teardown")
        return None


def show(values: dict) -> None:
    print(f"run {values['run_id']} in {values['region']}")
    print("matrix: " + ", ".join(f"{c.name}/{c.profile}" for c in values["cells"]))
    for line in values["estimate_lines"]:
        print(f"  {line.item}: ${line.usd:.4f}")
    usd = values.get("estimate_usd")
    print(f"estimate: ${usd:.2f}" if usd is not None else "estimate: unknown")
    for gap in values.get("gaps", []):
        print(f"  gap, not priced: {gap}")
    for error in values.get("errors", []):
        print(f"  error: {error}")


def finish(graph, cfg: dict, ask, teardown) -> int:
    """Answer a pending gate, if any, then print where the run ended."""
    if graph.get_state(cfg).interrupts:
        show(graph.get_state(cfg).values)
        try:
            answer = ask("Approve this spend? [y/N] ")
        except EOFError:
            answer = "n"
        invoke(graph, cfg, Command(resume=answer), teardown)
    values = graph.get_state(cfg).values
    approval = f" ({values['approval']})" if values.get("approval") else ""
    print(f"run {values['run_id']}: {values['status']}{approval}")
    if values["status"] == "interrupted":
        return 130
    return 1 if values["status"] in ("failed", "leftovers", "report_failed") else 0


def main(argv=None, *, pricing=None, ask=input, root: Path | None = None) -> int:
    old = signal.signal(signal.SIGTERM, raise_terminated)
    try:
        return run_command(argv, pricing, ask, root)
    finally:
        signal.signal(signal.SIGTERM, old)


def run_command(argv, pricing, ask, root) -> int:
    args = parse_args(argv)
    root = root or Path.cwd()
    config = RunConfig.model_validate_json(RUN_CONFIG.read_text()).model_dump()
    cfg = {"configurable": {"thread_id": args.run_id}}

    if args.command == "resume":
        if not (root / "runs" / args.run_id / "state.sqlite").is_file():
            print(f"error: no checkpoint for run {args.run_id}", file=sys.stderr)
            return 1
        with open_checkpointer(create_run_dirs(args.run_id, root)) as saver:
            # The planner already ran, so it needs no pricing client here.
            planner = make_planner(None, PlanConfig(**config["plan"]))
            nodes = deploy_nodes(root, config)
            graph = build_graph(planner, make_gate(), saver, **nodes)
            return finish(graph, cfg, ask, nodes["teardown"])

    region = resolve_region(args.region)
    profiles = [args.profile] if args.profile else list(PROFILES)
    state = RunState(
        run_id=args.run_id,
        cells=[CellSpec(name=n, profile=p) for n in args.cells for p in profiles],
        capacity=Capacity(**config["capacity"]),
        region=region,
    )
    # The Pricing API lives in us-east-1 whatever the run region is.
    pricing = pricing or boto3.client("pricing", region_name="us-east-1")
    paths = create_run_dirs(args.run_id, root)
    with open_checkpointer(paths) as saver:
        planner = make_planner(pricing, PlanConfig(**config["plan"]))
        nodes = deploy_nodes(root, config)
        graph = build_graph(planner, make_gate(args.auto_approve_under), saver, **nodes)
        result = invoke(graph, cfg, state, nodes["teardown"])
        if result and "__interrupt__" not in result:
            show(result)
        return finish(graph, cfg, ask, nodes["teardown"])
