import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from advisor.schemas import Figure, Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs, open_checkpointer


def make_state() -> RunState:
    return RunState(
        run_id="smoke-01",
        cells=[
            CellSpec(name="ecs-fargate", profile="steady"),
            CellSpec(name="eks-ec2", profile="spike"),
        ],
        capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4),
        region="us-east-1",
        estimate_usd=1.25,
        approved=None,
        status="planned",
    )


def test_run_state_round_trips_through_json():
    state = make_state()
    again = RunState.model_validate_json(state.model_dump_json())
    assert again == state
    assert again.ttl_hours == 4
    assert again.capacity.cpu_target == 60


def test_run_state_rejects_unknown_cell():
    with pytest.raises(ValidationError):
        CellSpec(name="aks", profile="steady")


def test_figure_without_source_is_rejected():
    with pytest.raises(ValidationError):
        Figure(name="p95_ms", value=120.0, unit="ms", cell="ecs-fargate")
    with pytest.raises(ValidationError):
        Figure(name="p95_ms", value=120.0, unit="ms", cell="ecs-fargate", source="")


def test_metrics_json_round_trips_with_shared_costs_and_gaps():
    metrics = Metrics(
        run_id="smoke-01",
        figures=[
            Figure(
                name="p95_ms",
                value=120.0,
                unit="ms",
                cell="ecs-fargate",
                profile="steady",
                source="ecs-fargate/steady/k6-summary.json",
            )
        ],
        shared_costs=[{"item": "nat-gateway", "usd": 0.09, "source": "cost/nat.json"}],
        gaps=["no price for CloudWatch ingest in us-east-1"],
    )
    assert Metrics.model_validate_json(metrics.model_dump_json()) == metrics


@pytest.mark.parametrize("run_id", ["", "Bad", "a_b", "x" * 25, "../etc", "a b"])
def test_bad_run_id_is_refused(tmp_path, run_id):
    with pytest.raises(ValueError):
        create_run_dirs(run_id, tmp_path)
    with pytest.raises(ValidationError):
        RunState(**{**make_state().model_dump(), "run_id": run_id})


def test_run_dirs_are_created_under_root(tmp_path):
    paths = create_run_dirs("smoke-01", tmp_path)
    assert paths.raw == tmp_path / "results" / "smoke-01" / "raw"
    assert paths.raw.is_dir()
    assert paths.metrics == tmp_path / "results" / "smoke-01" / "metrics.json"
    assert paths.report == tmp_path / "results" / "smoke-01" / "report.md"
    assert paths.checkpoint == tmp_path / "runs" / "smoke-01" / "state.sqlite"
    assert paths.checkpoint.parent.is_dir()


def test_checkpointer_file_opens(tmp_path):
    paths = create_run_dirs("smoke-01", tmp_path)
    with open_checkpointer(paths) as saver:
        assert saver.get({"configurable": {"thread_id": "smoke-01"}}) is None
    assert paths.checkpoint.is_file()


def test_advisor_files_hold_no_account_id_or_email():
    pattern = re.compile(r"(?<!\d)\d{12}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+")
    root = Path(__file__).resolve().parent.parent / "advisor"
    for path in root.rglob("*.py"):
        assert not pattern.search(path.read_text()), path
