"""scripts/recount_parts.py rewrites the published moving parts from the templates (issue #149). No AWS."""

import json
import shutil
from pathlib import Path

from advisor.collector import moving_parts
from advisor.schemas import read_aggregate, read_metrics
from scripts.recount_parts import recount

PUBLISHED = Path(__file__).resolve().parents[1] / "results" / "published"


def test_recount_sets_each_cells_count_and_rebuilds_the_aggregate(tmp_path):
    for path in PUBLISHED.glob("*.json"):
        shutil.copy(path, tmp_path)
    data = json.loads((tmp_path / "short1-metrics.json").read_text())
    for fig in data["figures"]:
        if fig["name"] == "moving_parts":
            fig["value"] = 99.0
    (tmp_path / "short1-metrics.json").write_text(json.dumps(data, indent=2))

    recount(tmp_path)

    for name in ("short1", "short2", "short3"):
        for fig in read_metrics(tmp_path / f"{name}-metrics.json").figures:
            if fig.name == "moving_parts":
                assert fig.value == len(moving_parts(fig.cell))
    agg = read_aggregate(tmp_path / "aggregate.json")
    assert agg.runs == ["short1", "short2", "short3"]
    for fig in agg.figures:
        if fig.name == "moving_parts":
            assert fig.min == fig.max == len(moving_parts(fig.cell))


def test_published_files_carry_the_template_counts(tmp_path):
    for path in PUBLISHED.glob("*.json"):
        shutil.copy(path, tmp_path)
    recount(tmp_path)
    for path in PUBLISHED.glob("*.json"):
        assert (tmp_path / path.name).read_text() == path.read_text(), path.name
