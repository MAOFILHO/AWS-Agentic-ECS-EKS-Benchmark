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


def test_published_holds_aggregate_four_charts_and_five_metrics():
    names = {p.name for p in PUBLISHED.iterdir()}
    runs = ("short1", "short2", "short3", "kf5", "kf6")
    assert names == {"aggregate.json", "readme-facts.json", *CHARTS, *(f"{r}-metrics.json" for r in runs)}
    agg = read_aggregate(PUBLISHED / "aggregate.json")
    assert agg.runs == list(runs)
    assert {f.cell for f in agg.figures} == {"ecs-fargate", "ecs-ec2", "eks-fargate", "eks-ec2"}
    charts = make_chart.render_questions(read_aggregate(PUBLISHED / "aggregate.json"))
    for name, svg in charts.items():
        assert (PUBLISHED / name).read_text() == svg, name


def test_published_files_leak_no_account_id_or_email():
    for path in PUBLISHED.iterdir():
        text = path.read_text()
        # Same pattern as the repo guard in tests/test_bootstrap_oidc.py.
        assert not re.search(r"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])", text), path.name
        assert "@" not in text, path.name


def texts(svg: Path, attr: str) -> list[ET.Element]:
    return [t for t in ET.parse(svg).iter(f"{SVG}text") if t.get(attr)]


def sig_digits(label: str) -> int:
    return len(label.replace("-", "").replace(".", "").lstrip("0").rstrip("0"))


def test_value_labels_have_at_most_three_significant_digits(tmp_path):
    run_dir = run_aggregate(tmp_path)
    labels = [t.text for name in CHARTS for t in texts(run_dir / name, "data-label")]
    assert labels and all(sig_digits(t) <= 3 for t in labels), labels
    assert "5410" in labels and "0.0348" in labels


def test_value_label_sits_above_its_whisker_top(tmp_path):
    run_dir = run_aggregate(tmp_path)
    for name in CHARTS:
        tree = ET.parse(run_dir / name)
        tops = {(w.get("data-cell"), w.get("data-metric")): float(w.get("y2"))
                for w in tree.iter(f"{SVG}line") if w.get("data-whisker")}
        labels = [t for t in tree.iter(f"{SVG}text") if t.get("data-label")]
        assert labels, name
        for t in labels:
            assert float(t.get("y")) < tops[(t.get("data-cell"), t.get("data-metric"))], (name, t.text)


def test_log_chart_has_a_tick_label_for_each_decade(tmp_path):
    svg = run_aggregate(tmp_path) / "chart-burst-latency.svg"
    ticks = {t.text for t in texts(svg, "data-tick")}
    assert {"1", "10", "100", "1000", "10000"} <= ticks


def test_linear_charts_have_tick_labels_and_gridlines(tmp_path):
    run_dir = run_aggregate(tmp_path)
    for name in CHARTS:
        if name == "chart-burst-latency.svg":
            continue
        assert len(texts(run_dir / name, "data-tick")) >= 2, name
        assert [ln for ln in ET.parse(run_dir / name).iter(f"{SVG}line") if ln.get("data-grid")], name


def test_burst_latency_chart_has_p50_p95_p99_for_each_cell(tmp_path):
    svg = run_aggregate(tmp_path) / "chart-burst-latency.svg"
    for metric in ("p50_ms", "p95_ms", "p99_ms"):
        assert {b.get("data-cell") for b in bars(svg, metric)} == CELLS, metric



def fewer_runs_charts(tmp_path) -> tuple[dict[str, str], dict[str, str]]:
    """Question charts of the fixture as is, and with the ecs-ec2 spike scale-out resting on 1 run (issue #163)."""
    agg = read_aggregate(FIXTURE)
    few = agg.model_copy(update={"figures": [
        f.model_copy(update={"n": 1}) if (f.cell, f.name, f.profile) == ("ecs-ec2", "scale_out_s", "spike") else f
        for f in agg.figures]})
    return make_chart.render_questions(agg), make_chart.render_questions(few)


def test_bar_with_fewer_runs_is_lighter_and_labelled_with_its_n(tmp_path):
    _, few = fewer_runs_charts(tmp_path)
    svg = tmp_path / "scaling.svg"
    svg.write_text(few["chart-burst-scaling.svg"])
    scale = {b.get("data-cell"): b for b in bars(svg, "scale_out_s")}
    assert scale["ecs-ec2"].get("fill-opacity") == make_chart.FEWER_OPACITY
    assert scale["ecs-fargate"].get("fill-opacity") is None
    marks = [(t.get("data-cell"), t.get("data-metric"), t.text) for t in texts(svg, "data-n")]
    assert marks == [("ecs-ec2", "scale_out_s", "n=1")]
    words = [t.text for t in ET.parse(svg).iter(f"{SVG}text")]
    assert "lighter bar: fewer runs" in words
    # The n label sits under the value label and above the bar.
    label = next(t for t in texts(svg, "data-label") if t.get("data-cell") == "ecs-ec2" and t.get("data-metric") == "scale_out_s")
    n = texts(svg, "data-n")[0]
    assert float(label.get("y")) < float(n.get("y")) < float(scale["ecs-ec2"].get("y"))


def test_chart_with_equal_n_is_unchanged(tmp_path):
    same, few = fewer_runs_charts(tmp_path)
    for name in CHARTS:
        assert "fill-opacity" not in same[name] and "data-n=" not in same[name] and "fewer runs" not in same[name]
        if name != "chart-burst-scaling.svg":
            assert few[name] == same[name], name
