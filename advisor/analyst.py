"""Deterministic analyst (ADR 0004): raw files in, metrics.json out. No LLM, no AWS."""

from pathlib import Path

from advisor import rawformats as rf
from advisor.schemas import Figure, Metrics
from advisor.state import CellSpec, RunState
from advisor.store import create_run_dirs

UNITS = {"p50_ms": "ms", "p95_ms": "ms", "p99_ms": "ms", "error_rate": "ratio"}
UNITS |= {"scale_out_s": "s", "cold_start_s": "s", "deploy_time_s": "s"}
UNITS |= {"usd_per_1k_requests": "usd", "idle_usd_per_hour": "usd/h"}
UNITS |= {"config_lines": "lines", "moving_parts": "count"}


class _Run:
    """Figures and gaps of one run. A figure only comes from a valid raw file."""

    def __init__(self, raw: Path):
        self.raw, self.figures, self.gaps = raw, [], []

    def read(self, cell: str, name: str, shape):
        """Read raw/<cell>/<name>; a missing or bad file is a gap and returns None."""
        path = self.raw / cell / name
        if not path.is_file():
            self.gaps.append(f"{cell}/{name}: missing")
            return None
        try:
            return rf.read(path, shape)
        except rf.RawFormatError as err:
            self.gaps.append(f"{cell}/{err}")
            return None

    def add(self, name, value, cell, sources, profile=None, why=""):
        if value is None:
            self.gaps.append(f"{cell}{'/' + profile if profile else ''} {name}: {why}")
            return
        source = " + ".join(f"{cell}/{s}" for s in sources)
        unit = UNITS[name]
        self.figures.append(Figure(name=name, value=value, unit=unit, source=source, cell=cell, profile=profile))


def _to_healthy(events, kind: str) -> float | None:
    """Seconds from the first `kind` event to the first `healthy` event after it."""
    starts = [e.timestamp for e in events if e.kind == kind]
    if not starts:
        return None
    ends = [e.timestamp for e in events if e.kind == "healthy" and e.timestamp >= min(starts)]
    return (min(ends) - min(starts)).total_seconds() if ends else None


def _cell(run: _Run, cell: str, profiles: list[str], threshold: float) -> bool:
    ok_requests, k6_files = 0, []
    for profile in profiles:
        name = f"k6-{profile}.json"
        k6 = run.read(cell, name, rf.K6Summary)
        if k6 is None:
            ok_requests = None
            continue
        k6_files.append(name)
        m = k6.metrics
        if ok_requests is not None:
            ok_requests += m.http_req_failed.fails
        for metric, field in (("p50_ms", "med"), ("p95_ms", "p95"), ("p99_ms", "p99")):
            got = getattr(m.http_req_duration, field, None)
            run.add(metric, got, cell, [name], profile, f"{name} has no {field}")
        total = m.http_reqs.count
        rate = m.http_req_failed.passes / total if total else None
        run.add("error_rate", rate, cell, [name], profile, f"{name} has zero requests")

    events = run.read(cell, "events.json", list[rf.Event])
    if events is not None:
        for metric, kind in (("scale_out_s", "alarm"), ("cold_start_s", "scheduled")):
            got = _to_healthy(events, kind)
            run.add(metric, got, cell, ["events.json"], why=f"no {kind} then healthy")

    deploy = run.read(cell, "deploy.json", rf.Deploy)
    if deploy is not None:
        took = (deploy.target_healthy - deploy.stack_create_start).total_seconds()
        run.add("deploy_time_s", took, cell, ["deploy.json"])

    config = run.read(cell, "config.json", rf.Config)
    if config is not None:
        run.add("config_lines", config.config_lines, cell, ["config.json"])
        run.add("moving_parts", config.moving_parts, cell, ["config.json"])

    cost = run.read(cell, "cost.json", rf.Cost)
    if cost is not None:
        run.add("idle_usd_per_hour", cost.idle_usd_per_hour, cell, ["cost.json"])
        per_1k = cost.load_usd / ok_requests * 1000 if ok_requests else None
        why = "a k6 file is missing" if ok_requests is None else "zero successful requests"
        run.add("usd_per_1k_requests", per_1k, cell, ["cost.json", *k6_files], why=why)

    cpu = run.read(cell, "cloudwatch-harness-cpu.json", list[rf.Point]) or []
    peak = max((p.value for p in cpu), default=0.0)
    if peak > threshold:
        run.gaps.append(f"{cell}: harness CPU peaked at {peak}% (threshold {threshold}%)")
    return peak > threshold


def analyse(
    run_id: str, raw: Path, cells: list[CellSpec], harness_cpu_threshold: float = 70.0
) -> Metrics:
    profiles: dict[str, list[str]] = {}
    for spec in cells:
        if spec.profile not in profiles.setdefault(spec.name, []):
            profiles[spec.name].append(spec.profile)
    run = _Run(raw)
    bound = [_cell(run, c, p, harness_cpu_threshold) for c, p in profiles.items()]
    return Metrics(run_id=run_id, figures=run.figures, gaps=run.gaps, harness_bound=any(bound))


def analyst_node(state: RunState, *, root: Path, harness_cpu_threshold: float = 70.0) -> dict:
    """LangGraph node. Bind `root` with functools.partial when adding it to the graph."""
    paths = create_run_dirs(state.run_id, root)
    metrics = analyse(state.run_id, paths.raw, state.cells, harness_cpu_threshold)
    paths.metrics.write_text(metrics.model_dump_json(indent=2))
    return {"status": "analysed"}
