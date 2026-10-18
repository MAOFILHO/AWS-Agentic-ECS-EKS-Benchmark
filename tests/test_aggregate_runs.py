"""aggregate repeat runs into n, min, median, max (issue #125)."""

import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from advisor.schemas import MetricsError, read_aggregate
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


def test_runs_with_different_cell_sets_are_rejected(tmp_path, capsys):
    seed(tmp_path)
    path = tmp_path / "run-c" / "metrics.json"
    data = json.loads(path.read_text())
    data["figures"] = [f for f in data["figures"] if f["cell"] != "eks-ec2"]
    path.write_text(json.dumps(data))
    code, out = aggregate(tmp_path)
    assert code != 0
    assert not out.exists()
    err = capsys.readouterr().err
    assert "error:" in err and "cell" in err


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
