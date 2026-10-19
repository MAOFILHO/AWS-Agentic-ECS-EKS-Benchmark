"""Rewrite moving_parts in the published metrics from the templates, then rebuild aggregate.json (issue #149).

Usage:
    python -m scripts.recount_parts [--published results/published]

The parts depend on the repo files, not on the run, so no AWS call is needed. Run
scripts.make_readme_tables after it.
"""

import argparse
import json
import sys
from pathlib import Path

from advisor.collector import moving_parts
from advisor.schemas import Metrics
from scripts.aggregate_runs import aggregate


def recount(published: Path) -> None:
    runs = []
    for path in sorted(published.glob("*-metrics.json")):
        data = json.loads(path.read_text())
        for fig in data["figures"]:
            if fig["name"] == "moving_parts":
                fig["value"] = float(len(moving_parts(fig["cell"])))
        path.write_text(json.dumps(data, indent=2))
        runs.append(Metrics.model_validate(data))
    (published / "aggregate.json").write_text(aggregate(runs).model_dump_json(indent=2) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--published", type=Path, default=Path("results/published"))
    recount(parser.parse_args(argv).published)
    return 0


if __name__ == "__main__":
    sys.exit(main())
