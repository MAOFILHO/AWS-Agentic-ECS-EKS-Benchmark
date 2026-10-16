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


SEPARATORS = ["..", "...", "-", "–", "—", "/", " to ", "~", ":", "→", " ", "_", ",,"]


@pytest.mark.parametrize("sep", SEPARATORS)
def test_any_separator_between_two_numbers_still_checks_both(sep):
    assert check(f"p95 is 120{sep}777 ms", make_metrics()) == ["777"]
    assert check(f"p95 is 777{sep}120 ms", make_metrics()) == ["777"]


@pytest.mark.parametrize(
    ("text", "invented"),
    [
        ("p95 is 120-777 ms", ["777"]),
        ("p95 is 120 -777 ms", ["777"]),
        ("NAT costs $0.09-$99", ["$99"]),
        ("costs $5-$99", ["$5", "$99"]),
        ("parts 1.2k-9k", ["9k"]),
        ("id 2024-5555-6666", ["2024", "5555", "6666"]),
        ("on 2026-10-15", ["2026", "10", "15"]),
        ("model v1.2", ["1.2"]),
        ("in 2026", ["2026"]),
        ("p9500 and ec2777", ["9500", "2777"]),
        ("eks-ec2-777", ["777"]),
        ("us-east-1-777", ["777"]),
    ],
)
def test_every_number_is_checked_unless_inside_an_allowed_label(text, invented):
    assert check(text, make_metrics()) == invented


@pytest.mark.parametrize(
    "label",
    [
        "p50",
        "p95",
        "p99",
        "ec2",
        "ecs-ec2",
        "ecs-fargate",
        "eks-ec2",
        "eks-fargate",
        "smoke-01",
        "us-east-1",
    ],
)
def test_each_allowed_label_passes_and_hides_nothing_next_to_it(label):
    assert check(f"the {label} cell", make_metrics()) == []
    assert check(f"the {label}-777 cell", make_metrics()) == ["777"]
    assert "777" in check(f"the 777{label} cell", make_metrics())


def test_list_numbers_one_to_four_pass_only_at_the_start_of_a_line():
    assert check("1. first\n2) second\n  3. third\n4. fourth", make_metrics()) == []
    assert check("1. first\nplus 2 more", make_metrics()) == ["2"]
    assert check("5. fifth", make_metrics()) == ["5"]
    assert check("1.5 first", make_metrics()) == ["1.5"]


def test_extract_numbers_finds_every_format():
    text = "p50 7 ms, 1,234 s, 3.5%, $0.09, 1.2k parts, 120ms and 2M rows"
    assert extract_numbers(text) == ["7", "1,234", "3.5%", "$0.09", "1.2k", "120", "2M"]


def test_extract_numbers_skips_only_allowed_labels():
    text = "ecs-ec2 and eks-ec2, p95 for run smoke-01 on 2026-10-15"
    assert extract_numbers(text, "smoke-01") == ["2026", "10", "15"]


def test_a_single_dot_is_a_decimal_that_must_be_known():
    assert check("p95 is 120.777 ms", make_metrics()) == ["120.777"]


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


def test_account_id_and_email_are_caught():
    # Built at runtime so no committed file holds an id- or email-shaped string.
    account, email = "1234" * 3, "someone" + "@" + "example.com"
    assert check(f"account {account} owner {email}", make_metrics()) == [account, email]
