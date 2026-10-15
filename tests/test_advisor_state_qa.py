import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from advisor.schemas import Figure, Metrics
from advisor.state import Capacity, CellSpec, RunState
from advisor.store import create_run_dirs, open_checkpointer

ACCOUNT_OR_EMAIL = re.compile(r"(?<!\d)\d{12}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+")


def make_state(run_id: str = "qa-80") -> RunState:
    return RunState(
        run_id=run_id,
        cells=[CellSpec(name=n, profile="scale-from-min") for n in ("ecs-ec2", "eks-fargate")],
        capacity=Capacity(vcpu=0.5, memory_mib=1024, min_replicas=1, max_replicas=3),
        region="us-east-1",
        approved=True,
        errors=["first"],
        actual_usd=0.42,
    )


@pytest.mark.parametrize("run_id", ["abc\n", "abc-\n", "\nabc", "-" * 25, "ABC-1"])
def test_run_id_with_newline_or_out_of_range_is_refused(tmp_path, run_id):
    with pytest.raises(ValueError):
        create_run_dirs(run_id, tmp_path)
    with pytest.raises(ValidationError):
        make_state(run_id)
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("run_id", ["a", "x" * 24, "0-9-z"])
def test_run_id_at_the_edges_is_accepted(tmp_path, run_id):
    assert create_run_dirs(run_id, tmp_path).raw.is_dir()
    assert make_state(run_id).run_id == run_id


def test_figure_with_null_source_is_rejected():
    with pytest.raises(ValidationError):
        Figure(name="p50_ms", value=1.0, unit="ms", cell="ecs-ec2", source=None)


def test_shared_cost_without_source_is_rejected():
    with pytest.raises(ValidationError):
        Metrics(run_id="qa-80", shared_costs=[{"item": "nat-gateway", "usd": 0.09}])


def test_full_run_state_round_trips_with_every_field_set():
    state = make_state()
    assert RunState.model_validate_json(state.model_dump_json()) == state


def test_written_outputs_and_tests_hold_no_account_id_or_email(tmp_path):
    paths = create_run_dirs("qa-80", tmp_path)
    metrics = Metrics(
        run_id="qa-80",
        figures=[Figure(name="p99_ms", value=9.0, unit="ms", cell="eks-ec2", source="k6.json")],
        gaps=["no price"],
    )
    paths.metrics.write_text(metrics.model_dump_json())
    with open_checkpointer(paths):
        pass
    outputs = [paths.metrics.read_text(), make_state().model_dump_json()]
    sources = [p.read_text() for p in Path(__file__).resolve().parent.glob("test_advisor_*.py")]
    for text in outputs + sources:
        assert not ACCOUNT_OR_EMAIL.search(text)
