"""Draw the benchmark chart: results/<run-id>/metrics.json to a grouped bar SVG.

Usage:
    python -m scripts.make_chart --run-id <id> [--out PATH]

One group per metric, one bar per cell, each group on its own scale. A missing figure shows "n/a" and no bar.
With no metrics.json but an aggregate.json (scripts/aggregate_runs.py), each bar is the median with a min to max
whisker, and one more chart per question (QUESTIONS) goes next to chart.svg.
"""

import argparse
import math
import sys
from pathlib import Path
from typing import get_args
from xml.sax.saxutils import escape

from advisor.schemas import (
    Aggregate,
    Metrics,
    MetricsError,
    read_aggregate,
    read_metrics,
)
from advisor.state import CellName

GROUPS = [
    ("p95_ms", "p95 latency (ms)"),
    ("scale_out_s", "scale out (s)"),
    ("cold_start_s", "cold start (s)"),
    ("deploy_time_s", "deploy time (s)"),
]
IDLE = ("idle_usd_per_hour", "idle cost (USD/h)")
COLOURS = {"ecs-fargate": "#1f6fb2", "ecs-ec2": "#7fb3e0", "eks-fargate": "#c4561d", "eks-ec2": "#f0a868"}
BAR_W, GAP, PLOT_H, TOP, LEFT = 44, 8, 180, 60, 20
# (file, question, profile, groups, log axis). Latency spans 4 ms to 7000 ms, so a linear axis hides the fast cells.
QUESTIONS = [
    ("chart-burst-scaling.svg", "How fast does it scale out under a burst?", "spike",
     [("scale_out_s", "scale out (s)"), ("cold_start_s", "cold start (s)")], False),
    ("chart-burst-latency.svg", "How slow do replies get under a burst?", "spike",
     [("p50_ms", "p50 latency (ms, log)"), ("p95_ms", "p95 latency (ms, log)")], True),
    ("chart-cost.svg", "What does it cost?", None,
     [("idle_usd_per_hour", "idle cost (USD/h)"), ("usd_per_1k_requests", "USD per 1k requests")], False),
    ("chart-deploy.svg", "How long does a deploy take?", None, [("deploy_time_s", "deploy time (s)")], False),
]


def pick(metrics: Metrics) -> dict[tuple[str, str], float]:
    """(cell, metric) -> value. With several profiles, the steady one (or a profile-less one) wins."""
    values: dict[tuple[str, str], float] = {}
    for fig in sorted(metrics.figures, key=lambda f: f.profile not in (None, "steady")):
        values.setdefault((fig.cell, fig.name), fig.value)
    return values


def render(metrics: Metrics) -> str:
    return draw(f"Benchmark {metrics.run_id}", pick(metrics), {})


def render_aggregate(agg: Aggregate) -> str:
    """Median bars with a min to max whisker. With several profiles, the steady one (or a profile-less one) wins."""
    values: dict[tuple[str, str], float] = {}
    spread: dict[tuple[str, str], tuple[float, float]] = {}
    for fig in sorted(agg.figures, key=lambda f: f.profile not in (None, "steady")):
        if (fig.cell, fig.name) not in values:
            values[(fig.cell, fig.name)] = fig.median
            spread[(fig.cell, fig.name)] = (fig.min, fig.max)
    return draw(f"Benchmark median of {', '.join(agg.runs)}", values, spread)


def render_questions(agg: Aggregate) -> dict[str, str]:
    """File name -> SVG, one chart per question. Every cell of the aggregate gets a slot, so a gap shows n/a."""
    cells = [c for c in get_args(CellName) if any(f.cell == c for f in agg.figures)]
    charts = {}
    for file, question, profile, groups, log in QUESTIONS:
        figs = [f for f in agg.figures if f.profile == profile]
        values = {(f.cell, f.name): f.median for f in figs}
        spread = {(f.cell, f.name): (f.min, f.max) for f in figs}
        charts[file] = draw(question, values, spread, groups, cells, log)
    return charts


def draw(title: str, values: dict[tuple[str, str], float], spread: dict[tuple[str, str], tuple[float, float]],
         groups=None, cells=None, log: bool = False) -> str:
    if cells is None:
        cells = [c for c in get_args(CellName) if any(cell == c for cell, _ in values)]
    if groups is None:
        groups = GROUPS + ([IDLE] if any(name == IDLE[0] for _, name in values) else [])
    group_w = max(len(cells) * (BAR_W + GAP) + 3 * GAP, 150)
    width = max(LEFT * 2 + group_w * len(groups), LEFT * 2 + 130 * len(cells), LEFT * 2 + 9 * len(title))
    base = TOP + PLOT_H
    height = base + 70
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12"'
         + (' data-scale="log">' if log else ">")),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (f'<text x="{LEFT}" y="24" font-size="16" font-weight="bold" fill="#1a1a1a">'
         f"{escape(title)}</text>"),
    ]
    for g, (name, label) in enumerate(groups):
        x0 = LEFT + g * group_w
        top = max((spread.get((c, name), (0.0, values.get((c, name), 0.0)))[1] for c in cells), default=0.0)
        scale = (log_scale(name, cells, values, spread, top) if log and top > 0
                 else lambda v, top=top: v / top * PLOT_H)
        out.append(f'<text x="{x0 + group_w / 2}" y="{base + 20}" text-anchor="middle" fill="#1a1a1a">'
                   f"{escape(label)}</text>")
        out.append(f'<line x1="{x0 + GAP}" y1="{base}" x2="{x0 + group_w - GAP}" y2="{base}" stroke="#999999"/>')
        for i, cell in enumerate(cells):
            x = x0 + 2 * GAP + i * (BAR_W + GAP)
            mid = x + BAR_W / 2
            value = values.get((cell, name))
            if value is None:
                out.append(f'<text x="{mid}" y="{base - 6}" text-anchor="middle" fill="#555555">n/a</text>')
                continue
            h = max(scale(value), 1.0) if top > 0 else 1.0
            out.append(f'<rect data-cell="{cell}" data-metric="{name}" x="{x}" y="{base - h:.1f}" '
                       f'width="{BAR_W}" height="{h:.1f}" fill="{COLOURS[cell]}"/>')
            out.append(f'<text x="{mid}" y="{base - h - 4:.1f}" text-anchor="middle" fill="#1a1a1a">'
                       f"{value:.6g}</text>")
            if (cell, name) in spread and top > 0:
                lo, hi = (base - scale(v) for v in spread[(cell, name)])
                out.append(f'<line data-whisker="1" data-cell="{cell}" data-metric="{name}" x1="{mid}" y1="{lo:.1f}" '
                           f'x2="{mid}" y2="{hi:.1f}" stroke="#1a1a1a"/>')
    for i, cell in enumerate(cells):
        x = LEFT + i * 130
        out.append(f'<rect x="{x}" y="{height - 26}" width="12" height="12" fill="{COLOURS[cell]}"/>')
        out.append(f'<text x="{x + 18}" y="{height - 16}" fill="#1a1a1a">{cell}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def log_scale(name, cells, values, spread, top):
    """Value -> bar height on a log axis from the decade below the smallest value to the decade above the top."""
    low = min(v for c in cells for v in (values.get((c, name)), *spread.get((c, name), ())) if v and v > 0)
    lo = math.floor(math.log10(low))
    hi = max(math.ceil(math.log10(top)), lo + 1)
    return lambda v: (math.log10(v) - lo) / (hi - lo) * PLOT_H if v > 0 else 0.0


def main(argv=None, results: Path = Path("results")) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    source = results / args.run_id / "metrics.json"
    aggregate = results / args.run_id / "aggregate.json"
    charts = {}
    try:
        if not source.exists() and aggregate.exists():
            agg = read_aggregate(aggregate)
            svg = render_aggregate(agg)
            charts = render_questions(agg)
        else:
            svg = render(read_metrics(source))
    except (MetricsError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    out = args.out or results / args.run_id / "chart.svg"
    out.write_text(svg)
    print(out)
    for file, chart in charts.items():
        (results / args.run_id / file).write_text(chart)
        print(results / args.run_id / file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
