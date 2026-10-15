import json
import re
from pathlib import Path

import pytest

from advisor.cost import CellUsage, SharedUsage, estimate, shared_costs
from advisor.planner import DEFAULT_CELLS, PlanConfig, make_planner, run_matrix
from advisor.pricing import read_prices
from advisor.state import Capacity, CellSpec, RunState

FIXTURE = Path(__file__).parent / "fixtures" / "pricing" / "get_products.json"
SAVED = json.loads(FIXTURE.read_text())  # saved GetProducts PriceList entries, by ServiceCode


class StubPricing:
    """Answers GetProducts from the saved responses, applying TERM_MATCH and CONTAINS filters."""

    def __init__(self, hide: str = "\0"):
        self.hide = hide  # usagetype substring to drop, to simulate a Pricing API gap

    def get_products(self, ServiceCode, Filters, FormatVersion):
        def attrs(raw):
            product = json.loads(raw)["product"]
            return product["attributes"] | {"productFamily": product["productFamily"]}

        def keep(a):
            ok = [a.get(f["Field"]) == f["Value"] or (f["Type"] == "CONTAINS" and f["Value"] in a.get(f["Field"], "")) for f in Filters]
            return all(ok) and self.hide not in a.get("usagetype", "")

        return {"PriceList": [raw for raw in SAVED[ServiceCode] if keep(attrs(raw))]}


CELL = CellUsage(replicas=2, nodes=1, lcus=1, log_gb=0.1)
SHARED = SharedUsage(nat_gb=1, endpoints=5, harness_vcpu=1, harness_memory_mib=2048, harness_tasks=1)
CONFIG = PlanConfig(instance_type="t3.medium", duration_s=600, cell=CELL, shared=SHARED)
CAPACITY = Capacity(vcpu=0.25, memory_mib=512, min_replicas=2, max_replicas=4)
STATE = RunState(run_id="plan-01", cells=[], capacity=CAPACITY, region="us-east-1")


def test_read_prices_from_fixtures():
    prices, as_of, gaps = read_prices(StubPricing(), "us-east-1", "t3.medium")
    assert gaps == []
    assert len(prices) == 10
    assert prices["ec2_instance_hour"] == 0.0416  # the Linux row, not the Windows one
    assert as_of == "2026-10-02T00:00:00Z"


def test_default_matrix_is_ecs_fargate_vs_eks_ec2():
    assert DEFAULT_CELLS == ("ecs-fargate", "eks-ec2")
    matrix = run_matrix(STATE)
    assert {c.name for c in matrix} == {"ecs-fargate", "eks-ec2"}
    assert len(matrix) == 8  # 2 cells x 4 profiles


def test_estimate_for_default_matrix_matches_hand_computed_figure():
    out = make_planner(StubPricing(), CONFIG)(STATE)
    h = 600 / 3600  # one cell x profile run, in hours
    ecs_fargate = 0.25 * 2 * h * 0.04048 + 0.5 * 2 * h * 0.004445 + h * 0.0225 + h * 0.008 + 0.1 * 0.50
    eks_ec2 = h * 0.10 + h * 0.0416 + h * 0.0225 + h * 0.008 + 0.1 * 0.50
    run_h = 8 * h
    shared = run_h * 0.045 + 0.045 + 5 * run_h * 0.01 + run_h * 0.04048 + 2 * run_h * 0.004445
    assert 4 * ecs_fargate + 4 * eks_ec2 + shared == pytest.approx(0.789017, abs=1e-6)
    assert out["estimate_usd"] == 0.789
    assert out["gaps"] == [] and out["errors"] == []
    assert len(out["cells"]) == 8
    RunState.model_validate(STATE.model_dump() | out)  # the update fits the run record


def test_missing_price_is_a_gap_not_a_zero():
    prices, _, gaps = read_prices(StubPricing(hide="Fargate-vCPU"), "us-east-1", "t3.medium")
    assert "fargate_vcpu_hour" not in prices
    assert any("fargate_vcpu_hour" in g for g in gaps)
    cell = estimate(CellSpec(name="ecs-fargate", profile="steady"), CAPACITY, 600, prices, CELL)
    assert not any("fargate vcpu" in line.item for line in cell.lines)
    assert any("fargate_vcpu_hour" in g for g in cell.gaps)
    out = make_planner(StubPricing(hide="Fargate-vCPU"), CONFIG)(STATE)
    assert any("fargate_vcpu_hour" in g for g in out["gaps"])
    assert all(line.usd > 0 for line in out["estimate_lines"])


def test_shared_costs_are_own_lines_not_inside_a_cell():
    prices, _, _ = read_prices(StubPricing(), "us-east-1", "t3.medium")
    items = [line.item for line in shared_costs(3600, prices, SHARED).lines]
    assert items == ["shared nat gateway", "shared nat data", "shared vpc endpoints", "shared harness vcpu", "shared harness memory"]
    cell = estimate(CellSpec(name="eks-ec2", profile="spike"), CAPACITY, 3600, prices, CELL)
    assert not any(word in line.item for line in cell.lines for word in ("nat", "endpoint", "harness"))


def test_ttl_check_flags_a_run_longer_than_ttl():
    out = make_planner(StubPricing(), CONFIG.model_copy(update={"duration_s": 3600}))(STATE)  # 8 h > 4 h
    assert any("ttl_hours=4" in e for e in out["errors"])


def test_no_account_id_or_email_in_outputs_or_files():
    texts = [json.dumps(make_planner(StubPricing(), CONFIG)(STATE), default=str), FIXTURE.read_text()]
    texts += [(FIXTURE.parents[3] / "advisor" / n).read_text() for n in ("pricing.py", "cost.py", "planner.py")]
    for text in texts:
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", text)
        assert not re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text)
