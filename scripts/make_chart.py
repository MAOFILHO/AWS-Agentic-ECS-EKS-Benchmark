"""Draw the benchmark chart: results/<run-id>/metrics.json to a grouped bar SVG.

Usage:
    python -m scripts.make_chart --run-id <id> [--out PATH]

One group per metric, one bar per cell, each group on its own scale. A missing figure shows "n/a" and no bar.
"""

import argparse
import sys
from pathlib import Path
from typing import get_args
from xml.sax.saxutils import escape

from advisor.schemas import Metrics, MetricsError, read_metrics
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


def pick(metrics: Metrics) -> dict[tuple[str, str], float]:
    """(cell, metric) -> value. With several profiles, the steady one (or a profile-less one) wins."""
    values: dict[tuple[str, str], float] = {}
    for fig in sorted(metrics.figures, key=lambda f: f.profile not in (None, "steady")):
        values.setdefault((fig.cell, fig.name), fig.value)
    return values


def render(metrics: Metrics) -> str:
    values = pick(metrics)
    cells = [c for c in get_args(CellName) if any(cell == c for cell, _ in values)]
    groups = GROUPS + ([IDLE] if any(name == IDLE[0] for _, name in values) else [])
    group_w = max(len(cells) * (BAR_W + GAP) + 3 * GAP, 150)
    width = LEFT * 2 + group_w * len(groups)
    base = TOP + PLOT_H
    height = base + 70
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12">'),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (f'<text x="{LEFT}" y="24" font-size="16" font-weight="bold" fill="#1a1a1a">'
         f"Benchmark {escape(metrics.run_id)}</text>"),
    ]
    for g, (name, label) in enumerate(groups):
        x0 = LEFT + g * group_w
        top = max((values.get((c, name), 0.0) for c in cells), default=0.0)
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
            h = max(value / top * PLOT_H, 1.0) if top > 0 else 1.0
            out.append(f'<rect data-cell="{cell}" data-metric="{name}" x="{x}" y="{base - h:.1f}" '
                       f'width="{BAR_W}" height="{h:.1f}" fill="{COLOURS[cell]}"/>')
            out.append(f'<text x="{mid}" y="{base - h - 4:.1f}" text-anchor="middle" fill="#1a1a1a">'
                       f"{value:.6g}</text>")
    for i, cell in enumerate(cells):
        x = LEFT + i * 130
        out.append(f'<rect x="{x}" y="{height - 26}" width="12" height="12" fill="{COLOURS[cell]}"/>')
        out.append(f'<text x="{x + 18}" y="{height - 16}" fill="#1a1a1a">{cell}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main(argv=None, results: Path = Path("results")) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    source = results / args.run_id / "metrics.json"
    try:
        metrics = read_metrics(source)
    except (MetricsError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    out = args.out or results / args.run_id / "chart.svg"
    out.write_text(render(metrics))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
