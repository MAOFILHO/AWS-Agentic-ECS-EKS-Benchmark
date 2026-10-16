"""QA v3 red tests for #83: a dot-dot range must not hide an invented number."""

import pytest

from advisor.numcheck import check
from advisor.schemas import Figure, Metrics


def make_metrics() -> Metrics:
    return Metrics(
        run_id="qa-83",
        figures=[
            Figure(name="p95_ms", value=120.4, unit="ms", cell="ecs-fargate", source="k6.json")
        ],
    )


@pytest.mark.parametrize("text", ["120..777 ms", "120...777 ms", "120 .. 777 ms"])
def test_dot_dot_range_checks_the_second_number(text):
    assert "777" in check(text, make_metrics())


@pytest.mark.parametrize("text", ["120.4 ms"])
def test_a_real_decimal_still_reads_as_one_number(text):
    assert check(text, make_metrics()) == []
