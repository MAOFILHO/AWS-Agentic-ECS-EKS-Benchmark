"""Collector node (SPEC section 10): after the load, write the raw files the analyst reads.

Reads only: it never changes AWS state. Every file goes through rf.write, which masks account ids
and emails. A section that fails becomes a gap in state.gaps; its file is not written, so the analyst
reports the gap too. Needs the harness stack to output ClusterName and TaskDefinitionArn, and Container
Insights on the harness cluster (the harness CPU percent is CpuUtilized over CpuReserved). Service CPU and
memory come from the AWS/ECS namespace, so only ECS cells get cloudwatch-cpu.json and cloudwatch-memory.json.
cost.json and shared-cost.json come from the cost model (SPEC section 8): the measured load window times the plan's
usage and the plan-time prices in state.prices. A missing price or input leaves the file out and is a gap.
"""

import json
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from advisor import cost
from advisor import deployer as d
from advisor import rawformats as rf
from advisor.load_runner import Aws, stack_outputs
from advisor.planner import PlanConfig
from advisor.state import RunState
from advisor.store import create_run_dirs

ECS_PARTS = ("cluster", "service", "ALB", "autoscaling policy")
EKS_PARTS = ("cluster", "node group", "controller", "add-ons")
HELM = "infra/helm/workload"
METRIC_TRIES, METRIC_WAIT_S = 3, 60  # Container Insights publishes 1 to 2 minutes after the task ends


def config_files(cell: str) -> list[str]:
    if cell.startswith("ecs-"):
        return ["infra/cfn/30-ecs.yaml"]
    return ["infra/cfn/40-eks.yaml", "infra/cfn/41-eks-lbc-iam.yaml", f"{HELM}/values.yaml",
            f"{HELM}/values-{cell.removeprefix('eks-')}.yaml"]


def config(cell: str) -> dict:
    lines = sum(len((d.ROOT / f).read_text().splitlines()) for f in config_files(cell))
    return {"config_lines": lines, "moving_parts": len(ECS_PARTS if cell.startswith("ecs-") else EKS_PARTS)}


def metric(id: str, namespace: str, name: str, dims: dict, stat: str, **extra) -> dict:
    dimensions = [{"Name": k, "Value": v} for k, v in dims.items()]
    stat_spec = {"Metric": {"Namespace": namespace, "MetricName": name, "Dimensions": dimensions},
                 "Period": 60, "Stat": stat}
    return {"Id": id, "MetricStat": stat_spec, **extra}


def series(cw, queries: list[dict], start: datetime, end: datetime, optional: tuple[str, ...] = ()) -> dict[str, list[dict]]:
    """One get_metric_data call -> {query id: [{"timestamp", "value"}]}, oldest first.

    A series with no datapoints is a missing source, so it raises and the section becomes a gap. `optional` ids
    may be empty: CloudWatch publishes no HTTPCode_Target_5XX_Count datapoint when there were no 5xx.
    """
    got = cw.get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end, ScanBy="TimestampAscending")
    found = {r["Id"]: [{"timestamp": t.isoformat(), "value": v} for t, v in zip(r["Timestamps"], r["Values"])]
             for r in got["MetricDataResults"]}
    for q in queries:
        if q.get("ReturnData", True) and not found.get(q["Id"]) and q["Id"] not in optional:
            raise LookupError(f"no datapoints for {q['Id']}")
    return found


def event(when: datetime, kind: str, message: str) -> dict:
    return {"timestamp": when.isoformat(), "kind": kind, "message": message}


def ecs_events(aws: Aws, region: str, cluster: str, service: str, start: datetime) -> list[dict]:
    """Service events (tasks started, steady state) plus the autoscaling alarms and scaling activities."""
    resource = f"service/{cluster}/{service}"
    found = []
    for e in aws("ecs", region).describe_services(cluster=cluster, services=[service])["services"][0]["events"]:
        kind = "scheduled" if "has started" in e["message"] else "healthy" if "steady state" in e["message"] else None
        if kind and e["createdAt"] >= start:
            found.append(event(e["createdAt"], kind, e["message"]))
    history = aws("cloudwatch", region).describe_alarm_history(
        AlarmTypes=["MetricAlarm"], HistoryItemType="StateUpdate", StartDate=start)["AlarmHistoryItems"]
    found += [event(h["Timestamp"], "alarm", h["HistorySummary"]) for h in history
              if resource in h["AlarmName"] and h["HistorySummary"].endswith("to ALARM")]
    activities = aws("application-autoscaling", region).describe_scaling_activities(
        ServiceNamespace="ecs", ResourceId=resource)["ScalingActivities"]
    return found + [event(a["StartTime"], "alarm", a["Description"]) for a in activities if a["StartTime"] >= start]


def kubectl(run: d.Runner, env: dict, kubeconfig: str, *args: str) -> dict:
    code, out = run(["kubectl", *args, "-o", "json", "--kubeconfig", kubeconfig], env)
    if code:
        raise RuntimeError(f"kubectl {args[0]} {args[1]} exited {code}")
    return json.loads(out)


def eks_events(run: d.Runner, env: dict, cluster: str, region: str, start: datetime) -> list[dict]:
    """HPA scale-up decisions and pod placements from `kubectl get events`; a pod's Ready time from `get pods`."""
    found = []
    with tempfile.TemporaryDirectory() as tmp:
        kubeconfig = f"{tmp}/config"  # never ~/.kube/config
        code, _ = run(["aws", "eks", "update-kubeconfig", "--name", cluster, "--region", region,
                       "--kubeconfig", kubeconfig], env)
        if code:
            raise RuntimeError(f"update-kubeconfig exited {code}")
        for e in kubectl(run, env, kubeconfig, "get", "events")["items"]:
            when = datetime.fromisoformat(e.get("lastTimestamp") or e["eventTime"])
            what, reason = e["involvedObject"]["kind"], e["reason"]
            if when >= start and what == "HorizontalPodAutoscaler" and "above target" in e["message"]:
                found.append(event(when, "alarm", e["message"]))
            elif when >= start and what == "Pod" and reason == "Scheduled":
                found.append(event(when, "scheduled", e["message"]))
        for pod in kubectl(run, env, kubeconfig, "get", "pods")["items"]:
            for c in pod["status"].get("conditions", []):
                if c["type"] == "Ready" and c["status"] == "True" and datetime.fromisoformat(c["lastTransitionTime"]) >= start:
                    found.append(event(datetime.fromisoformat(c["lastTransitionTime"]), "healthy",
                                       f"pod {pod['metadata']['name']} Ready"))
    return found


def priced(part: cost.Breakdown) -> float:
    """A breakdown's total; any missing price raises, so the file is left out and the section is a gap."""
    if part.gaps:
        raise LookupError("; ".join(part.gaps))
    return part.total


def cell_cost(state: RunState, cell: str, load_s: float, plan: PlanConfig | None) -> dict:
    """cost.json: the cell over its load window, and the cell for one hour at min replicas with no load."""
    if plan is None:
        raise LookupError("no plan usage given to the collector")
    spec = next(c for c in state.cells if c.name == cell)
    idle = plan.cell.model_copy(update={"replicas": state.capacity.min_replicas, "lcus": 0, "log_gb": 0})
    return {"load_usd": priced(cost.estimate(spec, state.capacity, load_s, state.prices, plan.cell)),
            "idle_usd_per_hour": priced(cost.estimate(spec, state.capacity, 3600, state.prices, idle))}


def make_collector(aws: Aws, profile: str | None, root: Path, run: d.Runner | None = None,
                   sleep: Callable[[float], None] = time.sleep, plan: PlanConfig | None = None):
    def collector(state: RunState) -> dict:
        region, raw, gaps = state.region, create_run_dirs(state.run_id, root).raw, []
        env = {"AWS_PROFILE": profile} if profile else {}

        def section(cell: str, name: str, fn: Callable[[], None]) -> None:
            try:
                fn()
            except Exception as err:  # noqa: BLE001 - one failed section is a gap, the others still run
                gaps.append(rf.scrub(f"{cell}/{name}: {type(err).__name__}: {err}"))
                print(gaps[-1], flush=True)

        def collect_cell(cell: str) -> None:
            out, rid = raw / cell, d.cell_run_id(state.run_id, cell)
            section(cell, "config.json", lambda: rf.write(out / "config.json", config(cell), rf.Config))
            if cell not in state.load_windows:
                gaps.append(f"{cell}: no load window, so no CloudWatch, ALB or event files")
                return
            start, end = (datetime.fromisoformat(t) for t in state.load_windows[cell])
            load_s = (end - start).total_seconds()
            section(cell, "cost.json", lambda: rf.write(out / "cost.json", cell_cost(state, cell, load_s, plan), rf.Cost))
            cell_stack: dict[str, str] = {}
            section(cell, "stack outputs", lambda: cell_stack.update(stack_outputs(aws("cloudformation", region), f"{rid}-{cell}")))
            if not cell_stack:
                return
            cw = aws("cloudwatch", region)  # a client is made, nothing is called yet
            ecs = cell.startswith("ecs-")

            def service_metrics():
                dims = {"ClusterName": cell_stack["ClusterName"], "ServiceName": cell_stack["ServiceName"]}
                got = series(cw, [metric("cpu", "AWS/ECS", "CPUUtilization", dims, "Average"),
                                  metric("memory", "AWS/ECS", "MemoryUtilization", dims, "Average")], start, end)
                for name in ("cpu", "memory"):
                    rf.write(out / f"cloudwatch-{name}.json", got[name], list[rf.Point])

            def harness_cpu():
                harness = stack_outputs(aws("cloudformation", region), f"{rid}-harness")
                family = harness["TaskDefinitionArn"].rsplit("/", 1)[1].rsplit(":", 1)[0]
                dims = {"ClusterName": harness["ClusterName"], "TaskDefinitionFamily": family}
                queries = [metric("used", "ECS/ContainerInsights", "CpuUtilized", dims, "Average", ReturnData=False),
                           metric("reserved", "ECS/ContainerInsights", "CpuReserved", dims, "Average", ReturnData=False),
                           {"Id": "cpu", "Expression": "100 * used / reserved"}]
                for attempt in range(METRIC_TRIES):
                    try:
                        got = series(cw, queries, start, end)
                        break
                    except LookupError:
                        if attempt == METRIC_TRIES - 1:
                            raise  # still empty: the section becomes a gap
                        sleep(METRIC_WAIT_S)
                rf.write(out / "cloudwatch-harness-cpu.json", got["cpu"], list[rf.Point])

            def alb_files():
                deploy = rf.read(out / "deploy.json", rf.DeployStarted)
                elb = aws("elbv2", region)
                lb = next(b for b in elb.describe_load_balancers()["LoadBalancers"] if b["DNSName"] == deploy.alb_dns_name)
                lb_dim = lb["LoadBalancerArn"].split("loadbalancer/", 1)[1]
                tg = elb.describe_target_groups(LoadBalancerArn=lb["LoadBalancerArn"])["TargetGroups"][0]
                tg_dim = tg["TargetGroupArn"].rsplit(":", 1)[1]
                dims = {"LoadBalancer": lb_dim}
                got = series(cw, [metric("count", "AWS/ApplicationELB", "RequestCount", dims, "Sum"),
                                  metric("e5xx", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", dims, "Sum"),
                                  metric("time", "AWS/ApplicationELB", "TargetResponseTime", dims, "Average")], start, end,
                             optional=("e5xx",))
                rf.write(out / "alb.json", {"request_count": got["count"], "http_5xx": got["e5xx"],
                                            "response_time": got["time"]}, rf.Alb)
                # The ALB API has no transition time: the first minute with a healthy target is the closest record.
                since = deploy.stack_create_start
                up = series(cw, [metric("up", "AWS/ApplicationELB", "HealthyHostCount",
                                        {**dims, "TargetGroup": tg_dim}, "Maximum")], since, end + timedelta(minutes=5))
                healthy = next((p["timestamp"] for p in up["up"] if p["value"] >= 1), None)
                if healthy is None:
                    raise LookupError("no healthy target found for the deploy.json target_healthy field")
                rf.write(out / "deploy.json", {**deploy.model_dump(mode="json"), "target_healthy": healthy}, rf.Deploy)

            def events():
                if ecs:
                    found = ecs_events(aws, region, cell_stack["ClusterName"], cell_stack["ServiceName"], start)
                else:
                    found = eks_events(run or d.subprocess_runner, env, cell_stack["ClusterName"], region, start)
                rf.write(out / "events.json", sorted(found, key=lambda e: e["timestamp"]), list[rf.Event])

            if ecs:
                section(cell, "cloudwatch-cpu/memory", service_metrics)
            section(cell, "cloudwatch-harness-cpu.json", harness_cpu)
            section(cell, "alb.json + deploy.json", alb_files)
            section(cell, "events.json", events)
        def shared_cost():
            if plan is None or not state.load_windows:
                raise LookupError("no plan usage" if plan is None else "no load window")
            run_s = sum((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
                        for a, b in state.load_windows.values())
            part = cost.shared_costs(run_s, state.prices, plan.shared)
            priced(part)
            rf.write(raw / "shared-cost.json", [line.model_dump() for line in part.lines], list[rf.SharedCostLine])

        for cell in d.cells_of(state):
            collect_cell(cell)
        section("run", "shared-cost.json", shared_cost)
        return {"status": "collected", "gaps": [*state.gaps, *gaps]}

    return collector
