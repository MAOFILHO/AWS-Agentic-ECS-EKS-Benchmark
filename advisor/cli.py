"""Command line: `python -m advisor run ...` and `python -m advisor resume <run-id>`.

Prints the estimate and the run matrix, then asks y/N before any spend. Prints no account id.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import get_args

import boto3
from langgraph.types import Command

from advisor.gate import make_gate
from advisor.graph import build_graph
from advisor.planner import DEFAULT_CELLS, PROFILES, PlanConfig, make_planner
from advisor.state import Capacity, CellName, CellSpec, RunState
from advisor.store import create_run_dirs, open_checkpointer

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


def finish(graph, cfg: dict, ask) -> int:
    """Answer a pending gate, if any, then print where the run ended."""
    if graph.get_state(cfg).interrupts:
        show(graph.get_state(cfg).values)
        try:
            answer = ask("Approve this spend? [y/N] ")
        except EOFError:
            answer = "n"
        graph.invoke(Command(resume=answer), cfg)
    values = graph.get_state(cfg).values
    approval = f" ({values['approval']})" if values.get("approval") else ""
    print(f"run {values['run_id']}: {values['status']}{approval}")
    return 1 if values["status"] == "failed" else 0


def main(argv=None, *, pricing=None, ask=input, root: Path | None = None) -> int:
    args = parse_args(argv)
    root = root or Path.cwd()
    config = json.loads(RUN_CONFIG.read_text())
    cfg = {"configurable": {"thread_id": args.run_id}}

    if args.command == "resume":
        if not (root / "runs" / args.run_id / "state.sqlite").is_file():
            print(f"error: no checkpoint for run {args.run_id}", file=sys.stderr)
            return 1
        with open_checkpointer(create_run_dirs(args.run_id, root)) as saver:
            # The planner already ran, so it needs no pricing client here.
            graph = build_graph(make_planner(None, PlanConfig(**config["plan"])), make_gate(), saver)
            return finish(graph, cfg, ask)

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
        graph = build_graph(planner, make_gate(args.auto_approve_under), saver)
        result = graph.invoke(state, cfg)
        if "__interrupt__" not in result:
            show(result)
        return finish(graph, cfg, ask)
