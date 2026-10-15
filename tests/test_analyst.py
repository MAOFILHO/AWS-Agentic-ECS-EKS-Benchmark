import re
import shutil
from pathlib import Path

import pytest

from advisor.analyst import analyse, analyst_node
from advisor.rawformats import Alb, K6Summary, RawFormatError, read
from advisor.schemas import Metrics
from advisor.state import Capacity, CellSpec, RunState

FIXTURES = Path(__file__).parent / "fixtures" / "raw"
REAL, EDGE = FIXTURES / "real-01", FIXTURES / "edge-01"
REAL_CELLS = [
    CellSpec(name="ecs-fargate", profile="steady"),
    CellSpec(name="ecs-fargate", profile="spike"),
    CellSpec(name="eks-ec2", profile="steady"),
]
EDGE_CELLS = [CellSpec(name="ecs-fargate", profile="steady")]


def real_copy(tmp_path: Path) -> Path:
    shutil.copytree(REAL, tmp_path / "raw")
    return tmp_path / "raw"


# Hand-computed from the fixture files.
EXPECTED = [
    ("p50_ms", "ecs-fargate", "steady", 41.5),
    ("p95_ms", "ecs-fargate", "steady", 96.4),
    ("p99_ms", "ecs-fargate", "steady", 181.7),
    ("error_rate", "ecs-fargate", "steady", 6 / 3000),
    ("p95_ms", "ecs-fargate", "spike", 240.5),
    ("error_rate", "ecs-fargate", "spike", 40 / 2000),
    ("p50_ms", "eks-ec2", "steady", 38.0),
    ("error_rate", "eks-ec2", "steady", 0.0),
    ("scale_out_s", "ecs-fargate", None, 69.0),  # alarm 20:10:00, healthy 20:11:09
    ("scale_out_s", "eks-ec2", None, 22.0),  # alarm 21:56:01, healthy 21:56:23
    ("cold_start_s", "ecs-fargate", None, 38.0),  # scheduled 20:10:31
    ("cold_start_s", "eks-ec2", None, 3.0),  # scheduled 21:56:20
    ("deploy_time_s", "ecs-fargate", None, 252.0),  # 19:50:00 to 19:54:12
    ("deploy_time_s", "eks-ec2", None, 1110.0),  # 21:20:00 to 21:38:30
    ("usd_per_1k_requests", "ecs-fargate", None, 0.0125 / (2994 + 1960) * 1000),
    ("usd_per_1k_requests", "eks-ec2", None, 0.031 / 3000 * 1000),
    ("idle_usd_per_hour", "eks-ec2", None, 0.115),
    ("config_lines", "ecs-fargate", None, 212),
    ("moving_parts", "eks-ec2", None, 5),
]


@pytest.mark.parametrize(("name", "cell", "profile", "expected"), EXPECTED)
def test_each_metric_matches_hand_computed_value(name, cell, profile, expected):
    metrics = analyse("real-01", REAL, REAL_CELLS)
    found = [f for f in metrics.figures if (f.name, f.cell, f.profile) == (name, cell, profile)]
    assert len(found) == 1
    assert found[0].value == pytest.approx(expected)


def test_real_run_has_no_gaps_and_is_not_harness_bound():
    metrics = analyse("real-01", REAL, REAL_CELLS)
    assert (metrics.gaps, metrics.harness_bound) == ([], False)


MISSING = ["events.json", "deploy.json", "config.json", "cost.json", "k6-spike.json"]


@pytest.mark.parametrize("missing", [*MISSING, "cloudwatch-harness-cpu.json"])
def test_missing_raw_file_is_a_gap_not_zero(tmp_path, missing):
    raw = real_copy(tmp_path)
    (raw / "ecs-fargate" / missing).unlink()
    metrics = analyse("real-01", raw, REAL_CELLS)
    gone = f"ecs-fargate/{missing}"
    assert any(gone in gap for gap in metrics.gaps), metrics.gaps
    assert not [f for f in metrics.figures if gone in f.source]


def test_zero_requests_and_missing_files_give_gaps_not_errors():
    metrics = analyse("edge-01", EDGE, EDGE_CELLS)
    names = {f.name for f in metrics.figures}
    assert names == {"config_lines", "moving_parts", "idle_usd_per_hour"}
    assert all(f.value == f.value for f in metrics.figures)  # no NaN
    gaps = "\n".join(metrics.gaps)
    for missing in ["error_rate", "usd_per_1k_requests", "p95_ms", "events.json", "deploy.json"]:
        assert missing in gaps


@pytest.mark.parametrize(("raw", "cells"), [(REAL, REAL_CELLS), (EDGE, EDGE_CELLS)])
def test_every_figure_source_exists(raw, cells):
    metrics = analyse("run-01", raw, cells)
    assert metrics.figures
    for f in metrics.figures:
        for part in f.source.split(" + "):
            assert (raw / part).is_file(), f


def test_harness_cpu_above_threshold_marks_run():
    metrics = analyse("real-01", REAL, REAL_CELLS, harness_cpu_threshold=40.0)
    assert metrics.harness_bound is True
    assert any("harness" in gap and "44.5" in gap for gap in metrics.gaps)


def test_malformed_raw_file_is_refused_and_becomes_a_gap(tmp_path):
    raw = real_copy(tmp_path)
    (raw / "eks-ec2" / "deploy.json").write_text("{not json")
    (raw / "ecs-fargate" / "alb.json").write_text('{"request_count": "many"}')
    (raw / "ecs-fargate" / "k6-spike.json").write_text('{"metrics": {"http_reqs": {"count": -1}}}')
    with pytest.raises(RawFormatError):
        read(raw / "ecs-fargate" / "alb.json", Alb)
    with pytest.raises(RawFormatError):
        read(raw / "ecs-fargate" / "k6-spike.json", K6Summary)
    assert read(REAL / "ecs-fargate" / "alb.json", Alb).http_5xx[0].value == 6
    gaps = "\n".join(analyse("real-01", raw, REAL_CELLS).gaps)
    assert "eks-ec2/deploy.json" in gaps and "ecs-fargate/k6-spike.json" in gaps


def test_node_writes_metrics_json(tmp_path):
    raw = tmp_path / "results" / "real-01" / "raw"
    shutil.copytree(REAL, raw)
    capacity = Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4)
    state = RunState(run_id="real-01", cells=REAL_CELLS, capacity=capacity, region="us-east-1")
    assert analyst_node(state, root=tmp_path) == {"status": "analysed"}
    written = Metrics.model_validate_json((raw.parent / "metrics.json").read_text())
    assert written == analyse("real-01", raw, REAL_CELLS)


def test_fixtures_and_outputs_hold_no_account_id_or_email():
    pattern = re.compile(r"(?<!\d)\d{12}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+")
    texts = [p.read_text() for p in FIXTURES.rglob("*.json")]
    texts += [analyse("real-01", REAL, REAL_CELLS).model_dump_json()]
    texts += [analyse("edge-01", EDGE, EDGE_CELLS).model_dump_json()]
    assert not [t for t in texts if pattern.search(t)]
