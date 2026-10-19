"""Rewrite moving_parts in the published metrics from the templates, then rebuild aggregate.json (issue #149).

Usage:
    python -m scripts.recount_parts [--published results/published]

The parts depend on the repo files, not on the run, so no AWS call is needed. Run
scripts.make_readme_tables after it. The aggregate keeps the run order of the aggregate.json it
replaces, and its floats are rounded to 6 decimals like the metrics: a long fraction reads as an
account id to the repo guard.
"""

import argparse
import json
import sys
from pathlib import Path

from advisor.collector import moving_parts
from advisor.schemas import Metrics
from scripts.aggregate_runs import aggregate


def _round(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_round(v) for v in value]
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    return value


def recount(published: Path) -> None:
    old = published / "aggregate.json"
    order = json.loads(old.read_text())["runs"] if old.exists() else []
    runs = []
    for path in sorted(published.glob("*-metrics.json")):
        data = json.loads(path.read_text())
        for fig in data["figures"]:
            if fig["name"] == "moving_parts":
                fig["value"] = float(len(moving_parts(fig["cell"])))
        path.write_text(json.dumps(data, indent=2))
        runs.append(Metrics.model_validate(data))
    runs.sort(key=lambda r: order.index(r.run_id) if r.run_id in order else len(order))
    agg = aggregate(runs)
    agg = agg.model_validate(_round(agg.model_dump()))
    old.write_text(agg.model_dump_json(indent=2) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--published", type=Path, default=Path("results/published"))
    recount(parser.parse_args(argv).published)
    return 0


if __name__ == "__main__":
    sys.exit(main())
