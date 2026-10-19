"""aggregate repeat runs into n, min, median, max (issue #125)."""

import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from advisor.schemas import Aggregate, AggregateFigure, MetricsError, read_aggregate
from scripts import aggregate_runs, make_chart

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "aggregate"
RUNS = ["run-a", "run-b", "run-c"]
SVG = "{http://www.w3.org/2000/svg}"


def seed(results: Path, runs=RUNS):
    for rid in runs:
        (results / rid).mkdir(parents=True)
        shutil.copy(FIXTURES / f"{rid}.json", results / rid / "metrics.json")


def aggregate(results: Path, runs=RUNS, name="agg-1"):
    out = results / name / "aggregate.json"
    code = aggregate_runs.main(["--runs", ",".join(runs), "--out", str(out)], results=results)
    return code, out


def test_three_runs_give_n_min_median_max_for_one_latency_figure(tmp_path):
    seed(tmp_path)
    code, out = aggregate(tmp_path)
    assert code == 0
    agg = read_aggregate(out)
    assert agg.runs == RUNS
    fig = next(f for f in agg.figures if (f.name, f.cell, f.profile) == ("p95_ms", "ecs-fargate", "steady"))
    assert (fig.n, fig.min, fig.median, fig.max) == (3, 3.9, 4.316744, 5.1)
    assert fig.unit == "ms" and fig.runs == RUNS


def test_runs_with_different_cell_sets_aggregate_each_cell_over_its_own_runs(tmp_path):
    # Issue #159: the eks-fargate runs hold other cells than the published runs.
    seed(tmp_path)
    path = tmp_path / "run-c" / "metrics.json"
    data = json.loads(path.read_text())
    data["figures"] = [f for f in data["figures"] if f["cell"] != "eks-ec2"]
    path.write_text(json.dumps(data))
    code, out = aggregate(tmp_path)
    assert code == 0
    agg = read_aggregate(out)
    assert agg.runs == RUNS
    by_cell = {f.cell: f for f in agg.figures if (f.name, f.profile) == ("p95_ms", "steady")}
    assert (by_cell["ecs-fargate"].n, by_cell["ecs-fargate"].runs) == (3, RUNS)
    assert (by_cell["eks-ec2"].n, by_cell["eks-ec2"].runs) == (2, ["run-a", "run-b"])


def test_runs_with_different_profile_sets_are_rejected(tmp_path, capsys):
    seed(tmp_path)
    path = tmp_path / "run-b" / "metrics.json"
    path.write_text(path.read_text().replace('"steady"', '"spike"'))
    code, out = aggregate(tmp_path)
    assert code != 0
    assert not out.exists()
    assert "profile" in capsys.readouterr().err


def test_missing_run_exits_with_error(tmp_path, capsys):
    seed(tmp_path, ["run-a"])
    code, out = aggregate(tmp_path, ["run-a", "nope"])
    assert code != 0 and not out.exists()
    assert "error:" in capsys.readouterr().err


def test_repeated_run_id_is_rejected(tmp_path, capsys):
    seed(tmp_path, ["run-a"])
    code, out = aggregate(tmp_path, ["run-a", "run-a", "run-a"])
    assert code != 0 and not out.exists()
    err = capsys.readouterr().err
    assert "error:" in err and "run-a" in err


def test_unit_that_differs_between_runs_is_rejected(tmp_path, capsys):
    seed(tmp_path)
    path = tmp_path / "run-b" / "metrics.json"
    data = json.loads(path.read_text())
    fig = next(f for f in data["figures"] if f["name"] == "p95_ms")
    fig["unit"] = "s"
    path.write_text(json.dumps(data))
    code, out = aggregate(tmp_path)
    assert code != 0 and not out.exists()
    err = capsys.readouterr().err
    assert "error:" in err and "unit" in err


def test_bad_aggregate_json_raises(tmp_path):
    bad = tmp_path / "aggregate.json"
    bad.write_text('{"runs": ["run-a"], "figures": [{"name": "p95_ms", "min": "fast"}]}')
    with pytest.raises(MetricsError):
        read_aggregate(bad)


def test_chart_draws_median_bars_with_min_max_whiskers(tmp_path):
    seed(tmp_path)
    aggregate(tmp_path)
    assert make_chart.main(["--run-id", "agg-1"], results=tmp_path) == 0
    tree = ET.parse(tmp_path / "agg-1" / "chart.svg")
    bars = [r for r in tree.iter(f"{SVG}rect") if r.get("data-cell")]
    whiskers = [ln for ln in tree.iter(f"{SVG}line") if ln.get("data-whisker")]
    assert {(b.get("data-cell"), b.get("data-metric")) for b in bars} == {
        (w.get("data-cell"), w.get("data-metric")) for w in whiskers}
    w = next(w for w in whiskers if w.get("data-cell") == "ecs-fargate" and w.get("data-metric") == "p95_ms")
    b = next(b for b in bars if b.get("data-cell") == "ecs-fargate" and b.get("data-metric") == "p95_ms")
    top_of_bar = float(b.get("y"))
    assert float(w.get("y2")) < top_of_bar < float(w.get("y1"))
    assert "4.31674" in [t.text for t in tree.iter(f"{SVG}text")]


def test_single_run_metrics_chart_is_unchanged(tmp_path):
    seed(tmp_path, ["run-a"])
    assert make_chart.main(["--run-id", "run-a"], results=tmp_path) == 0
    svg = (tmp_path / "run-a" / "chart.svg").read_text()
    assert "data-whisker" not in svg
    metrics = make_chart.read_metrics(tmp_path / "run-a" / "metrics.json")
    assert svg == make_chart.render(metrics)


def test_single_run_chart_matches_main_byte_for_byte(tmp_path):
    # run-a-chart-main.svg was drawn by scripts/make_chart.py on main 451a2c4, before aggregate support.
    seed(tmp_path, ["run-a"])
    assert make_chart.main(["--run-id", "run-a"], results=tmp_path) == 0
    assert (tmp_path / "run-a" / "chart.svg").read_bytes() == (FIXTURES / "run-a-chart-main.svg").read_bytes()


def test_make_aggregate_writes_valid_json_and_chart_renders(tmp_path):
    seed(tmp_path / "results")
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    for d in ("advisor", "scripts"):
        (tmp_path / d).symlink_to(ROOT / d)
    for f in ("pyproject.toml", "uv.lock"):
        (tmp_path / f).symlink_to(ROOT / f)
    env_make = ["make", "-s", "aggregate", "RUNS=run-a,run-b,run-c", "OUT=agg-1"]
    result = subprocess.run(env_make, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    agg = read_aggregate(tmp_path / "results" / "agg-1" / "aggregate.json")
    assert len(agg.figures) == 6
    assert make_chart.main(["--run-id", "agg-1"], results=tmp_path / "results") == 0
    assert "data-whisker" in (tmp_path / "results" / "agg-1" / "chart.svg").read_text()


def test_make_aggregate_without_args_fails():
    result = subprocess.run(["make", "-s", "aggregate"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "RUNS" in result.stdout + result.stderr


def test_no_aws_sdk_loaded_on_the_aggregate_and_chart_path(tmp_path):
    seed(tmp_path)
    code = (
        "import sys; from pathlib import Path; from scripts import aggregate_runs, make_chart; "
        f"r = Path({str(tmp_path)!r}); "
        "assert aggregate_runs.main(['--runs', 'run-a,run-b,run-c', '--out', str(r / 'agg-1' / 'aggregate.json')], "
        "results=r) == 0; "
        "assert make_chart.main(['--run-id', 'agg-1'], results=r) == 0; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('boto3', 'botocore', 'aiobotocore')))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert result.stdout.strip().splitlines()[-1] == "[]"
    for script in ("aggregate_runs.py", "make_chart.py"):
        assert not re.search(r"boto|bedrock|aws", (ROOT / "scripts" / script).read_text(), re.IGNORECASE)


def _fig(cell, run_ids, name="p95_ms", profile="spike"):
    return AggregateFigure(name=name, unit="ms", cell=cell, profile=profile, n=len(run_ids),
                           min=4.0, median=5.0, max=6.0, runs=run_ids)


def test_readme_tables_print_n_per_cell():
    from scripts.make_readme_tables import render

    six = [f"r{i}" for i in range(1, 7)]
    agg = Aggregate(runs=six, figures=[_fig("ecs-fargate", six), _fig("eks-fargate", six[3:])])
    lines = render(agg).splitlines()
    assert "| Metric | ecs-fargate | eks-fargate |" in lines
    assert "| Runs | n=6 | n=3 |" in lines
    assert "cells were measured in different run sets; ecs-fargate appears in all of them." in render(agg).lower()


def test_readme_table_names_n_on_a_figure_with_fewer_runs_than_its_column():
    # Issue #161: eks-fargate spike scale-out exists in kf5 only, while its column rests on 2 runs.
    from scripts.make_readme_tables import render

    two = ["kf5", "kf6"]
    agg = Aggregate(runs=two, figures=[_fig("eks-fargate", two), _fig("eks-fargate", ["kf5"], name="scale_out_s")])
    lines = render(agg).splitlines()
    assert "| Runs | n=2 |" in lines
    assert "| p95 latency (ms) | 5 (4 to 6) |" in lines
    assert "| Scale out (s) | 5 (4 to 6) (n=1) |" in lines


def test_readme_tables_with_one_cell_set_have_no_run_set_note():
    from scripts.make_readme_tables import render

    runs = ["r1", "r2", "r3"]
    text = render(Aggregate(runs=runs, figures=[_fig("ecs-fargate", runs), _fig("eks-ec2", runs)]))
    assert "| Runs | n=3 | n=3 |" in text.splitlines()
    assert "different run sets" not in text


def test_published_charts_still_render_the_same(tmp_path):
    published = ROOT / "results" / "published"
    (tmp_path / "published").mkdir()
    shutil.copy(published / "aggregate.json", tmp_path / "published" / "aggregate.json")
    assert make_chart.main(["--run-id", "published"], results=tmp_path) == 0
    for svg in published.glob("chart-*.svg"):
        assert (tmp_path / "published" / svg.name).read_bytes() == svg.read_bytes(), svg.name
