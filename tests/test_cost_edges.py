"""Edge cases for the price reader, cost model and planner (QA for issue #81)."""

import json

from advisor.cost import estimate
from advisor.planner import make_planner, run_matrix
from advisor.pricing import read_prices
from advisor.state import CellSpec, RunState
from tests.test_cost import CAPACITY, CELL, CONFIG, SAVED, STATE, StubPricing


class FailingPricing:
    def get_products(self, **kwargs):
        raise TimeoutError("network down")


class TwoPricesPricing(StubPricing):
    """Returns the saved rows plus a second, different EKS price: an ambiguous answer."""

    def get_products(self, ServiceCode, Filters, FormatVersion):
        page = super().get_products(ServiceCode, Filters, FormatVersion)
        if ServiceCode == "AmazonEKS":
            other = json.loads(SAVED["AmazonEKS"][0])
            dims = next(iter(other["terms"]["OnDemand"].values()))["priceDimensions"]
            next(iter(dims.values()))["pricePerUnit"]["USD"] = "0.6000000000"
            page["PriceList"] = page["PriceList"] + [json.dumps(other)]
        return page


class PagedPricing(StubPricing):
    """Splits every answer into one product per page, linked by NextToken."""

    def get_products(self, ServiceCode, Filters, FormatVersion, NextToken=None):
        rows = super().get_products(ServiceCode, Filters, FormatVersion)["PriceList"]
        i = int(NextToken or 0)
        page = {"PriceList": rows[i : i + 1]}
        if i + 1 < len(rows):
            page["NextToken"] = str(i + 1)
        return page


def test_api_error_is_a_gap_and_the_estimate_counts_no_zero_lines():
    prices, as_of, gaps = read_prices(FailingPricing(), "us-east-1", "t3.medium")
    assert prices == {} and as_of is None
    assert len(gaps) == 10 and all("Pricing API error TimeoutError" in g for g in gaps)
    out = make_planner(FailingPricing(), CONFIG)(STATE)
    assert out["estimate_usd"] == 0 and out["estimate_lines"] == []
    assert any("eks_cluster_hour" in g for g in out["gaps"])


def test_two_different_prices_are_a_gap_not_a_pick():
    prices, _, gaps = read_prices(TwoPricesPricing(), "us-east-1", "t3.medium")
    assert "eks_cluster_hour" not in prices
    assert any("eks_cluster_hour: 2 different prices" in g for g in gaps)


def test_paged_answers_are_read_to_the_end():
    prices, as_of, gaps = read_prices(PagedPricing(), "us-east-1", "t3.medium")
    assert gaps == [] and len(prices) == 10
    assert prices["nat_gb"] == 0.045 and as_of == "2026-10-02T00:00:00Z"


def test_asked_cells_replace_the_default_pair():
    state = STATE.model_copy(
        update={"cells": [CellSpec(name="eks-fargate", profile="steady")]}
    )
    assert {c.name for c in run_matrix(state)} == {"eks-fargate"}
    prices, _, _ = read_prices(StubPricing(), "us-east-1", "t3.medium")
    items = [
        line.item
        for line in estimate(
            CellSpec(name="eks-fargate", profile="steady"), CAPACITY, 3600, prices, CELL
        ).lines
    ]
    assert "eks-fargate/steady eks control plane" in items
    assert "eks-fargate/steady fargate vcpu" in items
    assert not any("ec2 instances" in i for i in items)


def test_ttl_exactly_at_the_limit_is_not_flagged():
    out = make_planner(StubPricing(), CONFIG.model_copy(update={"duration_s": 1800}))(
        STATE
    )  # 8 x 0.5 h = 4 h
    assert out["errors"] == []
    RunState.model_validate(STATE.model_dump() | out)


def _alb_row(usagetype: str, location_type: str, usd: str) -> str:
    row = json.loads(SAVED["AWSELB"][0])
    row["product"]["attributes"] |= {
        "usagetype": usagetype,
        "locationType": location_type,
    }
    dims = next(iter(row["terms"]["OnDemand"].values()))["priceDimensions"]
    next(iter(dims.values()))["pricePerUnit"]["USD"] = usd
    return json.dumps(row)


class LiveAlbPricing(StubPricing):
    """The three ALB hour rows the live Pricing API returned for us-east-1 on 2026-10-15."""

    def get_products(self, ServiceCode, Filters, FormatVersion):
        if ServiceCode != "AWSELB":
            return super().get_products(ServiceCode, Filters, FormatVersion)
        live = [
            _alb_row("LoadBalancerUsage", "AWS Region", "0.0225000000"),
            _alb_row("TS-LoadBalancerUsage", "AWS Region", "0.0050000000"),
            _alb_row("Outposts-LoadBalancerUsage", "AWS Outposts", "0.0225000000"),
            SAVED["AWSELB"][1],  # the LCU row, unchanged
        ]
        saved, SAVED["AWSELB"] = SAVED["AWSELB"], live
        try:
            return super().get_products(ServiceCode, Filters, FormatVersion)
        finally:
            SAVED["AWSELB"] = saved


def test_alb_hour_reads_the_region_price_from_live_shaped_rows():
    prices, _, gaps = read_prices(LiveAlbPricing(), "us-east-1", "t3.medium")
    assert gaps == []
    assert (
        prices["alb_hour"] == 0.0225
    )  # not the trust-store (TS-) row, not the Outposts row
