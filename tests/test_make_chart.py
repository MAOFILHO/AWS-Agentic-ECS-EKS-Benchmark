"""make chart: metrics.json to a grouped bar SVG (issue #112)."""

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import make_chart

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chart" / "metrics.json"
SVG = "{http://www.w3.org/2000/svg}"


def run_chart(tmp_path, text=None):
    run_dir = tmp_path / "chart-01"
    run_dir.mkdir()
    if text is None:
        shutil.copy(FIXTURE, run_dir / "metrics.json")
    else:
        (run_dir / "metrics.json").write_text(text)
    code = make_chart.main(["--run-id", "chart-01"], results=tmp_path)
    return code, run_dir / "chart.svg"


def texts(svg: Path) -> list[str]:
    return [t.text for t in ET.parse(svg).iter(f"{SVG}text")]


def bars(svg: Path) -> list[ET.Element]:
    return [r for r in ET.parse(svg).iter(f"{SVG}rect") if r.get("data-cell")]


def test_two_cells_write_both_names_and_p95_values(tmp_path):
    code, svg = run_chart(tmp_path)
    assert code == 0
    words = texts(svg)
    assert "ecs-fargate" in words and "eks-ec2" in words
    assert "120.4" in words and "98.7" in words


def test_missing_figure_renders_na_and_no_bar(tmp_path):
    code, svg = run_chart(tmp_path)
    assert code == 0
    assert "n/a" in texts(svg)
    cold = [b for b in bars(svg) if b.get("data-metric") == "cold_start_s"]
    assert [b.get("data-cell") for b in cold] == ["ecs-fargate"]
    assert all(float(b.get("height")) > 0 for b in bars(svg))


def test_one_cell_run_renders(tmp_path):
    one = '{"run_id": "chart-01", "figures": [{"name": "p95_ms", "value": 120.4, "unit": "ms", ' \
          '"source": "k6.json", "cell": "ecs-fargate"}]}'
    code, svg = run_chart(tmp_path, one)
    assert code == 0
    words = texts(svg)
    assert "ecs-fargate" in words and "eks-ec2" not in words
    assert "120.4" in words
    assert "idle cost (USD/h)" not in words


def test_idle_cost_group_only_when_present(tmp_path):
    _, svg = run_chart(tmp_path)
    assert "idle cost (USD/h)" in texts(svg)


def test_svg_has_its_own_background(tmp_path):
    _, svg = run_chart(tmp_path)
    first = next(ET.parse(svg).iter(f"{SVG}rect"))
    assert first.get("width") == "100%" and first.get("fill")


@pytest.mark.parametrize("value", ["NaN", '"fast"'])
def test_invalid_metrics_exits_with_error_and_writes_nothing(tmp_path, capsys, value):
    bad = FIXTURE.read_text().replace("120.4", value, 1)
    code, svg = run_chart(tmp_path, bad)
    assert code != 0
    assert not svg.exists()
    assert "error:" in capsys.readouterr().err


def test_missing_metrics_file_exits_with_error(tmp_path, capsys):
    assert make_chart.main(["--run-id", "nope"], results=tmp_path) != 0
    assert "error:" in capsys.readouterr().err


def test_out_flag_writes_there(tmp_path):
    (tmp_path / "chart-01").mkdir()
    shutil.copy(FIXTURE, tmp_path / "chart-01" / "metrics.json")
    out = tmp_path / "elsewhere.svg"
    assert make_chart.main(["--run-id", "chart-01", "--out", str(out)], results=tmp_path) == 0
    assert out.exists()


def test_make_target_runs_the_script_with_uv_locked():
    makefile = (Path(__file__).resolve().parent.parent / "Makefile").read_text()
    recipe = re.search(r"^chart:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert recipe and "uv run --locked python -m scripts.make_chart --run-id" in recipe.group(1)


def test_make_chart_without_run_id_fails():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(["make", "-s", "chart"], cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "RUN_ID" in result.stdout + result.stderr
