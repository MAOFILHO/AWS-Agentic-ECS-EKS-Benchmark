import pytest

from advisor.numcheck import check, extract_numbers
from advisor.schemas import Figure, Metrics


def make_metrics() -> Metrics:
    return Metrics(
        run_id="smoke-01",
        figures=[
            Figure(
                name="p95_ms",
                value=120.4,
                unit="ms",
                cell="ecs-fargate",
                source="a.json",
            ),
            Figure(
                name="error_rate",
                value=0.0123,
                unit="ratio",
                cell="eks-ec2",
                source="b.json",
            ),
            Figure(
                name="usd_per_1k_requests",
                value=0.0912,
                unit="usd/1k",
                cell="ecs-ec2",
                source="c",
            ),
            Figure(
                name="deploy_time_s",
                value=1234.0,
                unit="s",
                cell="eks-ec2",
                source="d.json",
            ),
            Figure(
                name="moving_parts",
                value=1500.0,
                unit="count",
                cell="eks-ec2",
                source="e",
            ),
        ],
        shared_costs=[{"item": "nat-gateway", "usd": 0.09, "source": "cost/nat.json"}],
    )


def test_extract_numbers_finds_every_format():
    text = "p50 7 ms, 1,234 s, 3.5%, $0.09, 1.2k parts, 120ms and 2M rows"
    assert extract_numbers(text) == ["7", "1,234", "3.5%", "$0.09", "1.2k", "120", "2M"]


def test_extract_numbers_skips_digits_inside_names_and_dates():
    text = "ecs-ec2 and eks-ec2, p95 for run smoke-01 on 2026-10-15, model v1.2"
    assert extract_numbers(text) == ["2026"]


@pytest.mark.parametrize(
    ("text", "invented"),
    [
        ("p95 is 120-777 ms", ["777"]),
        ("p95 is 120–777 ms", ["777"]),
        ("p95 is 120—777 ms", ["777"]),
        ("p95 is 120 to 777 ms", ["777"]),
        ("p95 is 120/777 ms", ["777"]),
        ("p95 is 120 -777 ms", ["777"]),
        ("p95 is 120.4-777 ms", ["777"]),
        ("NAT costs $0.09-$99", ["$99"]),
        ("costs $5-$99", ["$5", "$99"]),
        ("parts 1.2k-9k", ["9k"]),
        ("id 2024-5555-6666", ["5555", "6666"]),
    ],
)
def test_every_number_in_a_range_or_pair_is_checked(text, invented):
    assert check(text, make_metrics()) == invented


@pytest.mark.parametrize(
    "text",
    ["p95 on ec2", "eks-ec2 and ecs-fargate", "run smoke-01", "us-east-1", "on 2026-10-15"],
)
def test_allowed_label_forms_pass(text):
    assert check(text, make_metrics()) == []


def test_known_numbers_pass():
    text = "ECS on EC2 costs $0.0912 per 1k requests. p95 is 120.4 ms. NAT costs $0.09."
    assert check(text, make_metrics()) == []


def test_invented_number_is_caught():
    assert check("p95 is 99 ms on ECS.", make_metrics()) == ["99"]


def test_number_in_table_cell_is_caught():
    table = "| cell | p95 ms |\n|---|---|\n| ecs-fargate | 120 |\n| eks-ec2 | 87.5 |\n"
    assert check(table, make_metrics()) == ["87.5"]


@pytest.mark.parametrize(
    "shown", ["120", "120.4", "$0.09", "0.091", "1,234", "1.2%", "1.5k"]
)
def test_same_figure_rounded_to_shown_precision_passes(shown):
    assert check(f"value {shown} here", make_metrics()) == []


@pytest.mark.parametrize("shown", ["121", "120.3", "0.2", "1.3%", "1.6k"])
def test_rounding_outside_shown_precision_is_caught(shown):
    assert check(f"value {shown} here", make_metrics()) == [shown]


def test_plain_counts_and_years_pass():
    text = "1. first\n2. second\nThe 3 options and 4 cells, run in 2026."
    assert check(text, make_metrics()) == []


def test_account_id_and_email_are_caught():
    # Built at runtime so no committed file holds an id- or email-shaped string.
    account, email = "1234" * 3, "someone" + "@" + "example.com"
    assert check(f"account {account} owner {email}", make_metrics()) == [account, email]
