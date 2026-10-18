"""Load runner node (SPEC section 6): k6 as an ECS RunTask in the harness cluster, per cell and profile.

The harness stack is `<cell run id>-harness`. It must output ClusterName, TaskDefinitionArn,
SubnetIds (comma separated), SecurityGroupId and LogGroupName. The k6 container is named `k6`, logs
with the awslogs prefix `k6`, and prints its --summary-export JSON between two marker lines.

A task that fails to start, stops non-zero, times out or prints no summary is a gap in state.gaps,
never a zero. The graph still goes on to the collector and then teardown.
"""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from advisor import deployer as d
from advisor import rawformats as rf
from advisor.state import RunState
from advisor.store import create_run_dirs

BEGIN, END = "K6-SUMMARY-BEGIN", "K6-SUMMARY-END"
POLL_S, GRACE_S = 10, 300

# (service name, region) -> boto3 client, or a stub in tests
Aws = Callable[[str, str], Any]


class LoadError(Exception):
    pass


def stack_outputs(cfn, stack: str) -> dict[str, str]:
    outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def extract_summary(messages: list[str]) -> dict | None:
    """The JSON between the marker lines, or None when a marker or the JSON is missing."""
    try:
        begin = next(i for i, m in enumerate(messages) if BEGIN in m)
        end = next(i for i, m in enumerate(messages) if END in m and i > begin)
        return json.loads("\n".join(messages[begin + 1 : end]))
    except (StopIteration, json.JSONDecodeError):
        return None


def log_messages(logs, group: str, stream: str) -> list[str]:
    out, token = [], None
    while True:
        kwargs = {"logGroupName": group, "logStreamName": stream, "startFromHead": True}
        page = logs.get_log_events(**kwargs, **({"nextToken": token} if token else {}))
        out += [e["message"] for e in page["events"]]
        if page["nextForwardToken"] == token:
            return out
        token = page["nextForwardToken"]


def make_load_runner(aws: Aws, root: Path, duration_s: float, peak_rps: int, sleep=time.sleep,
                     clock=time.monotonic, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                     after_cell: Callable[[RunState, str, datetime], None] = lambda state, cell, start: None):
    """`after_cell(state, cell, load start)` runs once a cell's profiles are done; an error in it is a gap."""

    def run_profile(state: RunState, cell: str, profile: str, raw: Path) -> None:
        region, rid = state.region, d.cell_run_id(state.run_id, cell)
        harness = stack_outputs(aws("cloudformation", region), f"{rid}-harness")
        alb = rf.read(raw / cell / "deploy.json", rf.DeployStarted).alb_dns_name
        if not alb:
            raise LoadError("deploy.json has no alb_dns_name")
        env = {"TARGET_URL": f"http://{alb}", "PROFILE": profile, "PEAK_RPS": str(peak_rps),
               "DURATION_S": str(int(duration_s))}
        ecs, cluster = aws("ecs", region), harness["ClusterName"]
        started = ecs.run_task(
            cluster=cluster, taskDefinition=harness["TaskDefinitionArn"], launchType="FARGATE", count=1,
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": harness["SubnetIds"].split(","), "securityGroups": [harness["SecurityGroupId"]],
                "assignPublicIp": "DISABLED"}},
            overrides={"containerOverrides": [{"name": "k6", "environment": [
                {"name": k, "value": v} for k, v in env.items()]}]})
        if started.get("failures") or not started.get("tasks"):
            raise LoadError(f"run_task did not start a task: {started.get('failures')}")
        arn = started["tasks"][0]["taskArn"]
        deadline = clock() + duration_s + GRACE_S
        while (task := ecs.describe_tasks(cluster=cluster, tasks=[arn])["tasks"][0])["lastStatus"] != "STOPPED":
            if clock() > deadline:
                ecs.stop_task(cluster=cluster, task=arn, reason="advisor load timeout")
                raise LoadError(f"task still running {GRACE_S} s after the load should have ended")
            sleep(POLL_S)
        code = next((c.get("exitCode") for c in task["containers"] if c["name"] == "k6"), None)
        if code != 0:
            raise LoadError(f"task stopped with exit code {code}: {task.get('stoppedReason')}")
        messages = log_messages(aws("logs", region), harness["LogGroupName"], f"k6/k6/{arn.rsplit('/', 1)[1]}")
        summary = extract_summary(messages)
        if summary is None:
            raise LoadError(f"no k6 summary between {BEGIN} and {END}")
        try:
            rf.K6Summary.model_validate(summary)
        except ValueError as err:
            raise LoadError("k6 summary is not in the --summary-export shape") from err
        rf.write(raw / cell / f"k6-{profile}.json", summary, rf.K6Summary)

    def load_runner(state: RunState) -> dict:
        raw, gaps, windows = create_run_dirs(state.run_id, root).raw, [], {}
        for cell in d.cells_of(state):
            profiles: dict[str, dict] = {}
            for profile in [c.profile for c in state.cells if c.name == cell]:
                start = now()
                try:
                    run_profile(state, cell, profile, raw)
                except Exception as err:  # noqa: BLE001 - one failed cell is a gap, the other cells still run
                    gaps.append(rf.scrub(f"{cell}/{profile} load: {type(err).__name__}: {err}"))
                    print(gaps[-1], flush=True)
                end = now()
                windows[cell] = [windows.get(cell, [start.isoformat()])[0], end.isoformat()]
                profiles[profile] = {"start": start.isoformat(), "end": end.isoformat()}
                rf.write(raw / cell / "windows.json", profiles, dict[str, rf.Window])
            try:
                after_cell(state, cell, datetime.fromisoformat(windows[cell][0]))
            except Exception as err:  # noqa: BLE001 - the hook only reads; its failure is a gap
                gaps.append(rf.scrub(f"{cell} after load: {type(err).__name__}: {err}"))
                print(gaps[-1], flush=True)
        return {"status": "loaded", "gaps": [*state.gaps, *gaps], "load_windows": windows}

    return load_runner
