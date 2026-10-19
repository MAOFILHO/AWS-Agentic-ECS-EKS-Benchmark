"""Draw the benchmark chart: results/<run-id>/metrics.json to a grouped bar SVG.

Usage:
    python -m scripts.make_chart --run-id <id> [--out PATH]

One group per metric, one bar per cell, each group on its own scale. A missing figure shows "n/a" and no bar.
With no metrics.json but an aggregate.json (scripts/aggregate_runs.py), each bar is the median with a min to max
whisker, and one more chart per question (QUESTIONS) goes next to chart.svg. On a question chart, a bar that
rests on fewer runs than every other bar of its group is lighter and labelled with its n.
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
AXIS = 44  # room for tick labels left of each group on the question charts
FEWER_OPACITY = "0.45"  # fill of a bar that rests on fewer runs than the others in its group
# (file, question, profile, groups, log axis). Latency spans 4 ms to 7000 ms, so a linear axis hides the fast cells.
QUESTIONS = [
    ("chart-burst-scaling.svg", "How fast does it scale out under a burst?", "spike",
     [("scale_out_s", "scale out (s)"), ("cold_start_s", "cold start (s)")], False),
    ("chart-burst-latency.svg", "How slow do replies get under a burst?", "spike",
     [("p50_ms", "p50 latency (ms, log)"), ("p95_ms", "p95 latency (ms, log)"),
      ("p99_ms", "p99 latency (ms, log)")], True),
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
        ns = {(f.cell, f.name): f.n for f in figs}
        charts[file] = draw(question, values, spread, groups, cells, log, readable=True, ns=ns)
    return charts


def draw(title: str, values: dict[tuple[str, str], float], spread: dict[tuple[str, str], tuple[float, float]],
         groups=None, cells=None, log: bool = False, readable: bool = False, ns=None) -> str:
    """readable: 3 significant digit labels above the whisker, ticks and gridlines.
    ns: (cell, metric) -> run count. A bar whose n is under that of every other bar in its group is lighter and
    carries an n= label under its value, and a legend line explains it."""
    if cells is None:
        cells = [c for c in get_args(CellName) if any(cell == c for cell, _ in values)]
    if groups is None:
        groups = GROUPS + ([IDLE] if any(name == IDLE[0] for _, name in values) else [])
    pad = AXIS if readable else 0
    group_w = max(len(cells) * (BAR_W + GAP) + 3 * GAP, 150) + pad
    width = max(LEFT * 2 + group_w * len(groups), LEFT * 2 + 130 * len(cells), LEFT * 2 + 9 * len(title))
    base = TOP + PLOT_H
    ns = ns or {}
    fewer = {(c, name) for name, _ in groups for c in cells if (c, name) in ns
             and all(ns[(c, name)] < ns[(o, name)] for o in cells if o != c and (o, name) in ns)
             and any(o != c and (o, name) in ns for o in cells)}
    height = base + 70 + (18 if fewer else 0)
    # On a log chart every group shares one axis, so p50, p95 and p99 bars compare across groups.
    names = [name for name, _ in groups]
    shared = (log_scale(names, cells, values, spread) if log and any(values.get((c, n)) for c in cells for n in names)
              else None)
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12"'
         + (' data-scale="log">' if log else ">")),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (f'<text x="{LEFT}" y="24" font-size="16" font-weight="bold" fill="#1a1a1a">'
         f"{escape(title)}</text>"),
    ]
    if readable and shared:
        out += tick_lines(*shared, base, LEFT + pad + GAP, LEFT + group_w * len(groups) - GAP)
    for g, (name, label) in enumerate(groups):
        x0 = LEFT + g * group_w
        top = max((spread.get((c, name), (0.0, values.get((c, name), 0.0)))[1] for c in cells), default=0.0)
        if shared and top > 0:
            scale, ticks = shared
        else:
            scale, ticks = (lambda v, top=top: v / top * PLOT_H), linear_ticks(top)
        out.append(f'<text x="{x0 + pad + (group_w - pad) / 2}" y="{base + 20}" text-anchor="middle" fill="#1a1a1a">'
                   f"{escape(label)}</text>")
        if readable and top > 0 and not shared:
            out += tick_lines(scale, ticks, base, x0 + pad + GAP, x0 + group_w - GAP)
        out.append(f'<line x1="{x0 + pad + GAP}" y1="{base}" x2="{x0 + group_w - GAP}" y2="{base}" '
                   f'stroke="#999999"/>')
        for i, cell in enumerate(cells):
            x = x0 + pad + 2 * GAP + i * (BAR_W + GAP)
            mid = x + BAR_W / 2
            value = values.get((cell, name))
            if value is None:
                out.append(f'<text x="{mid}" y="{base - 6}" text-anchor="middle" fill="#555555">n/a</text>')
                continue
            h = max(scale(value), 1.0) if top > 0 else 1.0
            few = (cell, name) in fewer
            out.append(f'<rect data-cell="{cell}" data-metric="{name}" x="{x}" y="{base - h:.1f}" '
                       f'width="{BAR_W}" height="{h:.1f}" fill="{COLOURS[cell]}"'
                       + (f' fill-opacity="{FEWER_OPACITY}"/>' if few else "/>"))
            whisker = (cell, name) in spread and top > 0
            lo, hi = (base - scale(v) for v in spread[(cell, name)]) if whisker else (base, base)
            if readable:
                y = min(base - h, hi) - 4
                if few:
                    out.append(f'<text data-n="1" data-cell="{cell}" data-metric="{name}" x="{mid}" y="{y:.1f}" '
                               f'text-anchor="middle" font-size="10" fill="#555555">n={ns[(cell, name)]}</text>')
                    y -= 14
                out.append(f'<text data-label="1" data-cell="{cell}" data-metric="{name}" x="{mid}" y="{y:.1f}" '
                           f'text-anchor="middle" fill="#1a1a1a">{sig3(value)}</text>')
            else:
                out.append(f'<text x="{mid}" y="{base - h - 4:.1f}" text-anchor="middle" fill="#1a1a1a">'
                           f"{value:.6g}</text>")
            if whisker:
                out.append(f'<line data-whisker="1" data-cell="{cell}" data-metric="{name}" x1="{mid}" y1="{lo:.1f}" '
                           f'x2="{mid}" y2="{hi:.1f}" stroke="#1a1a1a"/>')
    for i, cell in enumerate(cells):
        x = LEFT + i * 130
        out.append(f'<rect x="{x}" y="{base + 44}" width="12" height="12" fill="{COLOURS[cell]}"/>')
        out.append(f'<text x="{x + 18}" y="{base + 54}" fill="#1a1a1a">{cell}</text>')
    if fewer:
        out.append(f'<text x="{LEFT}" y="{base + 74}" fill="#555555">lighter bar: fewer runs</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def log_scale(names, cells, values, spread):
    """(value -> bar height, decade ticks) on a log axis from the decade below the smallest value to the one above
    the largest, across every group in names."""
    seen = [v for n in names for c in cells for v in (values.get((c, n)), *spread.get((c, n), ())) if v and v > 0]
    lo = math.floor(math.log10(min(seen)))
    hi = max(math.ceil(math.log10(max(seen))), lo + 1)
    return (lambda v: (math.log10(v) - lo) / (hi - lo) * PLOT_H if v > 0 else 0.0), [10.0 ** d for d in range(lo, hi + 1)]


def tick_lines(scale, ticks, base, x1, x2) -> list[str]:
    """A light gridline from a 4 px tick left of x1 to x2, and its label, per tick."""
    out = []
    for t in ticks:
        y = base - scale(t)
        if y < base:  # the 0 gridline would hide under the axis line
            out.append(f'<line data-grid="1" x1="{x1 - 4}" y1="{y:.1f}" x2="{x2}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        out.append(f'<text data-tick="1" x="{x1 - 6}" y="{y + 4:.1f}" text-anchor="end" fill="#555555">{sig3(t)}</text>')
    return out


def linear_ticks(top: float) -> list[float]:
    """0 up to top in steps of 1, 2 or 5 times a power of ten, about five of them."""
    if top <= 0:
        return []
    raw = top / 5
    mag = 10.0 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    return [k * step for k in range(int(top / step + 1e-9) + 1)]


def sig3(value: float) -> str:
    """3 significant digits, no exponent for everyday sizes: 5412.19 -> 5410, 0.034842 -> 0.0348."""
    return format(float(f"{value:.3g}"), "g")


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
