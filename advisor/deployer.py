"""Deployer node (SPEC section 10): deploy each cell of the run matrix by subprocess, no prompts.

Each cell gets its own run id, `<run-id>-<suffix>`, so its stack names, its run-id tag and its
delete_run.py call never touch another cell. Every command carries the region; AWS_PROFILE and
the other inputs go in its env. Every command lands in runs/<run-id>/commands.jsonl.
"""

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from advisor import rawformats as rf
from advisor.state import RunState
from advisor.store import create_run_dirs

ROOT = Path(__file__).resolve().parent.parent
SUFFIX = {"ecs-fargate": "ef", "ecs-ec2": "ee", "eks-ec2": "ke", "eks-fargate": "kf"}
ALB_LINE = re.compile(r"ALB: http://(\S+)")  # what deploy_eks.py prints last

# (argv, extra env) -> (exit code, stdout + stderr)
Runner = Callable[[list[str], dict[str, str]], tuple[int, str]]


def mask(text: str) -> str:
    return re.sub(r"(?<!\d)\d{12}(?!\d)", "*" * 12, text)


def cell_run_id(run_id: str, cell: str) -> str:
    return f"{run_id}-{SUFFIX[cell]}"


def cells_of(state: RunState) -> list[str]:
    """Each platform cell once, in matrix order; every profile of a cell shares one deploy."""
    return list(dict.fromkeys(c.name for c in state.cells))


def subprocess_runner(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run one command. An interrupt waits for the child to end (never kills it mid change set), then re-raises."""
    proc = subprocess.Popen(argv, cwd=ROOT, env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    interrupt = None
    while True:
        try:
            out, err = proc.communicate()
            break
        except BaseException as exc:  # noqa: BLE001 - Ctrl+C, SIGTERM: keep waiting for the child
            interrupt = interrupt or exc
    if interrupt:
        raise interrupt
    return proc.returncode, out + err


def run_logged(run: Runner, root: Path, run_id: str, argv: list[str], env: dict[str, str]):
    """Run one command, print its output masked, and record argv, env names, exit and duration."""
    start = time.monotonic()
    interrupt = None
    try:
        code, out = run(argv, env)
    except Exception as exc:  # noqa: BLE001 - a missing binary must not skip teardown; treat it as exit 127
        code, out = 127, f"{type(exc).__name__}: {exc}"
    except BaseException as exc:  # noqa: BLE001 - Ctrl+C, SIGTERM: record the command as exit 130, then re-raise
        code, out, interrupt = 130, "", exc
    seconds = round(time.monotonic() - start, 3)
    if out.strip():
        print(mask(out.rstrip()), flush=True)
    log = root / "runs" / run_id / "commands.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    # Env values (profile, image URI) stay out of the record; only their names go in.
    entry = {"argv": [mask(a) for a in argv], "env": sorted(env), "exit": code, "seconds": seconds}
    with log.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    if interrupt:
        raise interrupt
    return code, out


def deploy_commands(cell: str, rid: str, region: str) -> list[list[str]]:
    """The Makefile targets for one cell, with --region and --yes added. An ECS cell then reads its ALB."""
    py = sys.executable
    if cell.startswith("eks-"):
        return [[py, "-m", "scripts.deploy_eks", "--cell", cell, "--run-id", rid, "--region", region, "--yes"]]
    stack = [py, "scripts/deploy_stack.py", "--region", region, "--yes"]
    ecs = [*stack, "--stack", f"{rid}-{cell}", "--template", "infra/cfn/30-ecs.yaml", "--env-param", "RunId=RUN_ID",
           "--env-param", "NetworkStackName=NETWORK_STACK", "--env-param", "ImageUri=IMAGE_URI"]
    if cell == "ecs-ec2":
        ecs += ["--env-param", "ComputeType=COMPUTE_TYPE"]
    return [[*stack, "--stack", f"{rid}-network", "--template", "infra/cfn/10-network.yaml",
             "--env-param", "RunId=RUN_ID"], ecs,
            ["aws", "cloudformation", "describe-stacks", "--stack-name", f"{rid}-{cell}", "--region", region,
             "--query", "Stacks[0].Outputs[?OutputKey=='AlbDnsName'].OutputValue", "--output", "text"]]


def make_deployer(profile: str | None, image_uri: str | None, root: Path, run: Runner | None = None,
                  now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Callable[[RunState], dict]:
    """Build the node. A failed command stops it with status "failed"; the graph then runs teardown."""

    def deployer(state: RunState) -> dict:
        runner = run or subprocess_runner

        def failed(why: str) -> dict:
            return {"status": "failed", "errors": [*state.errors, mask(f"deployer: {why}")]}

        if missing := [name for name, value in (("AWS_PROFILE", profile), ("IMAGE_URI", image_uri)) if not value]:
            return failed(f"not set: {', '.join(missing)}; nothing was deployed")
        raw = create_run_dirs(state.run_id, root).raw
        for cell in cells_of(state):
            rid = cell_run_id(state.run_id, cell)
            env = {"AWS_PROFILE": profile, "IMAGE_URI": image_uri, "RUN_ID": rid, "NETWORK_STACK": f"{rid}-network"}
            if cell == "ecs-ec2":
                env["COMPUTE_TYPE"] = "EC2"
            start = now()
            for argv in deploy_commands(cell, rid, state.region):
                code, out = run_logged(runner, root, state.run_id, argv, env)
                if code:
                    return failed(f"{cell}: exit {code} from {' '.join(argv[1:])}" + (f" ({out.strip()})" if code == 127 else ""))
            if cell.startswith("eks-"):
                alb = match.group(1) if (match := ALB_LINE.search(out)) else None
            else:
                alb = out.strip() or None
            record = {"stack_create_start": start.isoformat(), "stack_create_end": now().isoformat(), "alb_dns_name": alb}
            rf.write(raw / cell / "deploy.json", record, rf.DeployStarted)
        return {"status": "deployed"}

    return deployer
