"""Aggregate repeat runs: results/<id>/metrics.json for each run to one aggregate.json.

Usage:
    python -m scripts.aggregate_runs --runs a,b,c --out results/<name>/aggregate.json

For each (name, cell, profile) it records n, min, median, max and the source run ids.
Runs with different cell or profile sets are refused.
"""

import argparse
import statistics
import sys
from pathlib import Path

from pydantic import ValidationError

from advisor.schemas import (
    Aggregate,
    AggregateFigure,
    Metrics,
    MetricsError,
    read_metrics,
)


def aggregate(runs: list[Metrics]) -> Aggregate:
    for attr in ("cell", "profile"):
        sets = {r.run_id: {getattr(f, attr) for f in r.figures} for r in runs}
        if len({frozenset(s) for s in sets.values()}) > 1:
            shown = "; ".join(f"{rid}: {sorted(s, key=str)}" for rid, s in sets.items())
            raise MetricsError(f"runs have different {attr} sets ({shown})")
    groups: dict[tuple, list[tuple[str, float, str]]] = {}
    for run in runs:
        for fig in run.figures:
            groups.setdefault((fig.name, fig.cell, fig.profile), []).append((run.run_id, fig.value, fig.unit))
    figures = []
    for (name, cell, profile), rows in groups.items():
        values = [v for _, v, _ in rows]
        figures.append(AggregateFigure(
            name=name, unit=rows[0][2], cell=cell, profile=profile, n=len(rows),
            min=min(values), median=statistics.median(values), max=max(values),
            runs=[rid for rid, _, _ in rows],
        ))
    return Aggregate(runs=[r.run_id for r in runs], figures=figures)


def main(argv=None, results: Path = Path("results")) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", required=True, help="comma-separated run ids")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    ids = [r for r in args.runs.split(",") if r]
    try:
        agg = aggregate([read_metrics(results / rid / "metrics.json") for rid in ids])
    except (MetricsError, ValidationError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(agg.model_dump_json(indent=2) + "\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
