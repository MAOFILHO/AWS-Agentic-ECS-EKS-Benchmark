"""Write the README comparison tables from an aggregate.json, so no number is typed by hand.

Usage:
    python -m scripts.make_readme_tables [--aggregate results/published/aggregate.json] [--readme README.md]

Each cell shows `median (min to max)`, or one value when every run agreed. A missing figure shows n/a.
The tables replace whatever sits between the START and END markers in the README.
"""

import argparse
import sys
from pathlib import Path
from typing import get_args

from advisor.schemas import Aggregate, AggregateFigure, read_aggregate
from advisor.state import CellName

START = "<!-- tables: python -m scripts.make_readme_tables -->"
END = "<!-- /tables -->"
ROWS = [
    ("p50_ms", "p50 latency (ms)"),
    ("p95_ms", "p95 latency (ms)"),
    ("p99_ms", "p99 latency (ms)"),
    ("error_rate", "Error rate"),
    ("scale_out_s", "Scale out (s)"),
    ("cold_start_s", "Cold start (s)"),
    ("deploy_time_s", "Deploy time (s)"),
    ("config_lines", "Config lines"),
    ("moving_parts", "Moving parts"),
    ("idle_usd_per_hour", "Idle cost (USD/h)"),
    ("usd_per_1k_requests", "USD per thousand requests"),
]
TABLES = [("spike", "Profile `spike`"), ("scale-from-min", "Profile `scale-from-min`"), (None, "Per cell")]


def _fmt(value: float, unit: str) -> str:
    if unit == "ratio":
        return f"{value:.2%}"
    if unit.startswith("usd"):
        return f"{value:.4f}"
    if value.is_integer() or abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}" if abs(value) >= 10 else f"{value:.2f}"


def _cell(fig: AggregateFigure | None) -> str:
    if fig is None:
        return "n/a"
    median = _fmt(fig.median, fig.unit)
    low, high = _fmt(fig.min, fig.unit), _fmt(fig.max, fig.unit)
    return median if low == high == median else f"{median} ({low} to {high})"


def render(agg: Aggregate) -> str:
    cells = [c for c in get_args(CellName) if any(f.cell == c for f in agg.figures)]
    figs = {(f.profile, f.cell, f.name): f for f in agg.figures}
    out = []
    for profile, title in TABLES:
        rows = [(name, label) for name, label in ROWS if any(k[0] == profile and k[2] == name for k in figs)]
        out += [f"**{title}**", "", "| Metric | " + " | ".join(cells) + " |", "|---" * (len(cells) + 1) + "|"]
        for name, label in rows:
            out.append(f"| {label} | " + " | ".join(_cell(figs.get((profile, c, name))) for c in cells) + " |")
        out.append("")
    return "\n".join(out).rstrip("\n")


def splice(readme: str, tables: str) -> str:
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{tables}\n{END}{tail}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aggregate", type=Path, default=Path("results/published/aggregate.json"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)
    args.readme.write_text(splice(args.readme.read_text(), render(read_aggregate(args.aggregate))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
