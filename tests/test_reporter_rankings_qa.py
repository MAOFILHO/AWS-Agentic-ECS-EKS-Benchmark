"""QA edge cases for the ranking check (issue #135)."""

from pathlib import Path

import pytest

from advisor.reporter import contradicted_rankings
from advisor.schemas import Metrics

# Idle cost: ecs-fargate 0.0348/h, ecs-ec2 0.0641/h, eks-ec2 0.1641/h.
SAMPLE4 = Path(__file__).resolve().parent / "fixtures" / "reporter" / "sample4-metrics.json"


@pytest.fixture
def metrics() -> Metrics:
    return Metrics.model_validate_json(SAMPLE4.read_text())


@pytest.mark.parametrize(
    "text",
    [
        "ECS-EC2 is pricier than EKS-EC2.",
        "EKS EC2 is less expensive than ECS EC2.",
        # A decimal point inside the sentence does not split it.
        "At USD 0.0641/h, ECS EC2 is more expensive than EKS EC2.",
        "ECS EC2 is more costly compared to EKS EC2.",
    ],
)
def test_wrong_cost_ranking_in_other_words_is_rejected(metrics, text):
    assert contradicted_rankings(text, metrics) == [text.rstrip(".")]


@pytest.mark.parametrize(
    "text",
    [
        "ECS EC2 is slower than ECS EC2.",
        "ECS Fargate is cheaper per 1k requests than ECS EC2.",
        "| ecs-ec2 | cheaper than eks-ec2 |",
        # eks-fargate did not run in sample4: no figure, no check.
        "ECS Fargate is faster than EKS Fargate.",
    ],
)
def test_same_cell_right_ranking_or_unknown_cell_passes(metrics, text):
    assert contradicted_rankings(text, metrics) == []
