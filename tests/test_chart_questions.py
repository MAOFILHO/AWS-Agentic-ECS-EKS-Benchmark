"""make chart on an aggregate: one chart per question, and the published 3-run results (issue #143)."""

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from advisor.schemas import read_aggregate
from scripts import make_chart

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chart" / "aggregate.json"
PUBLISHED = ROOT / "results" / "published"
SVG = "{http://www.w3.org/2000/svg}"
CHARTS = ["chart-burst-scaling.svg", "chart-burst-latency.svg", "chart-cost.svg", "chart-deploy.svg"]
CELLS = {"ecs-fargate", "ecs-ec2"}


def run_aggregate(tmp_path) -> Path:
    run_dir = tmp_path / "agg-01"
    run_dir.mkdir()
    shutil.copy(FIXTURE, run_dir / "aggregate.json")
    assert make_chart.main(["--run-id", "agg-01"], results=tmp_path) == 0
    return run_dir


def bars(svg: Path, metric: str | None = None) -> list[ET.Element]:
    return [r for r in ET.parse(svg).iter(f"{SVG}rect")
            if r.get("data-cell") and metric in (None, r.get("data-metric"))]


def title(svg: Path) -> str:
    return next(ET.parse(svg).iter(f"{SVG}text")).text


def test_aggregate_writes_four_question_charts_with_one_bar_per_cell(tmp_path):
    run_dir = run_aggregate(tmp_path)
    for name in CHARTS:
        svg = run_dir / name
        assert svg.exists(), name
        assert title(svg).endswith("?"), name
        for metric in {b.get("data-metric") for b in bars(svg)}:
            cells = [b.get("data-cell") for b in bars(svg, metric)]
            assert len(cells) == len(set(cells)), (name, metric)
            assert set(cells) <= CELLS
    assert {b.get("data-cell") for b in bars(run_dir / "chart-deploy.svg")} == CELLS


def test_burst_charts_use_spike_only(tmp_path):
    run_dir = run_aggregate(tmp_path)
    scale = {b.get("data-cell"): b for b in bars(run_dir / "chart-burst-scaling.svg", "scale_out_s")}
    words = [t.text for t in ET.parse(run_dir / "chart-burst-scaling.svg").iter(f"{SVG}text")]
    assert set(scale) == CELLS
    assert "73" in words and "186" not in words  # ecs-ec2 spike median, not scale-from-min


def test_latency_chart_uses_log_axis_and_shows_fast_and_slow_bars(tmp_path):
    svg = run_aggregate(tmp_path) / "chart-burst-latency.svg"
    assert ET.parse(svg).getroot().get("data-scale") == "log"
    p50 = {b.get("data-cell"): float(b.get("height")) for b in bars(svg, "p50_ms")}
    assert p50["ecs-fargate"] >= 20  # 4 ms is visible
    assert p50["ecs-ec2"] > p50["ecs-fargate"]  # 5410 ms is taller and still inside the plot
    assert p50["ecs-ec2"] <= make_chart.PLOT_H


def test_missing_figure_prints_na_and_draws_no_bar(tmp_path):
    svg = run_aggregate(tmp_path) / "chart-burst-scaling.svg"
    assert [b.get("data-cell") for b in bars(svg, "cold_start_s")] == ["ecs-fargate"]
    assert "n/a" in [t.text for t in ET.parse(svg).iter(f"{SVG}text")]


def test_published_holds_aggregate_four_charts_and_three_metrics():
    names = {p.name for p in PUBLISHED.iterdir()}
    assert names == {"aggregate.json", *CHARTS, "short1-metrics.json", "short2-metrics.json", "short3-metrics.json"}
    charts = make_chart.render_questions(read_aggregate(PUBLISHED / "aggregate.json"))
    for name, svg in charts.items():
        assert (PUBLISHED / name).read_text() == svg, name


def test_published_files_leak_no_account_id_or_email():
    for path in PUBLISHED.iterdir():
        text = path.read_text()
        # Same pattern as the repo guard in tests/test_bootstrap_oidc.py.
        assert not re.search(r"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])", text), path.name
        assert "@" not in text, path.name
