"""On-demand prices from the AWS Pricing API (SPEC section 8).

The client is passed in: a boto3 `pricing` client on the us-east-1 endpoint in real use, a stub in
tests. A price that cannot be read becomes a gap; it is never replaced by a guess.
"""

import json
import re
from typing import Any

PRICING_ENDPOINT_REGION = "us-east-1"

# price key -> (ServiceCode, productFamily or None, usagetype substring). EC2 instances are separate.
USAGE_PRICES = {
    "eks_cluster_hour": ("AmazonEKS", None, "AmazonEKS-Hours:perCluster"),
    "fargate_vcpu_hour": ("AmazonECS", None, "Fargate-vCPU-Hours:perCPU"),
    "fargate_gb_hour": ("AmazonECS", None, "Fargate-GB-Hours"),
    "alb_hour": ("AWSELB", "Load Balancer-Application", "LoadBalancerUsage"),
    "alb_lcu_hour": ("AWSELB", "Load Balancer-Application", "LCUUsage"),
    "nat_hour": ("AmazonEC2", "NAT Gateway", "NatGateway-Hours"),
    "nat_gb": ("AmazonEC2", "NAT Gateway", "NatGateway-Bytes"),
    "vpce_hour": ("AmazonVPC", None, "VpcEndpoint-Hours"),
    "cw_ingest_gb": ("AmazonCloudWatch", None, "DataProcessing-Bytes"),
}


def _match(field: str, value: str, kind: str = "TERM_MATCH") -> dict[str, str]:
    return {"Type": kind, "Field": field, "Value": value}


def price_queries(region: str, instance_type: str) -> dict[str, tuple[str, list[dict[str, str]]]]:
    """Price key -> (ServiceCode, GetProducts filters). Each query must yield exactly one USD price."""
    queries = {}
    for key, (service, family, usage) in USAGE_PRICES.items():
        filters = [_match("regionCode", region), _match("usagetype", usage, "CONTAINS")]
        queries[key] = (service, filters + ([_match("productFamily", family)] if family else []))
    linux = {"instanceType": instance_type, "operatingSystem": "Linux", "tenancy": "Shared"}
    linux |= {"preInstalledSw": "NA", "capacitystatus": "Used", "regionCode": region}
    queries["ec2_instance_hour"] = ("AmazonEC2", [_match(k, v) for k, v in linux.items()])
    return queries


def _products(client: Any, service_code: str, filters: list[dict[str, str]]) -> list[dict]:
    kwargs: dict[str, Any] = {"ServiceCode": service_code, "Filters": filters, "FormatVersion": "aws_v1"}
    products: list[dict] = []
    while True:
        page = client.get_products(**kwargs)
        products += [json.loads(item) for item in page.get("PriceList", [])]
        if not page.get("NextToken"):
            return products
        kwargs["NextToken"] = page["NextToken"]


def _usage_is(product: dict, usage: str) -> bool:
    """True when usagetype is `usage`, bare or behind a region prefix (USE1-). Not TS-, not Outposts-."""
    usagetype = product.get("product", {}).get("attributes", {}).get("usagetype", "")
    return re.fullmatch(rf"(?:[A-Z]{{2,4}}\d-)?{re.escape(usage)}", usagetype) is not None


def _usd(product: dict) -> set[float]:
    terms = product.get("terms", {}).get("OnDemand", {}).values()
    dims = [d for t in terms for d in t.get("priceDimensions", {}).values()]
    return {float(d["pricePerUnit"]["USD"]) for d in dims if "USD" in d.get("pricePerUnit", {})}


def read_prices(client: Any, region: str, instance_type: str) -> tuple[dict[str, float], str | None, list[str]]:
    """Return (prices, as_of, gaps). `as_of` is the newest publicationDate in the responses."""
    prices: dict[str, float] = {}
    gaps: list[str] = []
    dates: list[str] = []
    for key, (service_code, filters) in price_queries(region, instance_type).items():
        try:
            products = _products(client, service_code, filters)
        except Exception as err:  # noqa: BLE001 - any API failure is a gap, never a made-up price
            gaps.append(f"price {key}: Pricing API error {type(err).__name__}")
            continue
        if key in USAGE_PRICES:
            products = [p for p in products if _usage_is(p, USAGE_PRICES[key][2])]
        found = set().union(*(_usd(p) for p in products)) - {0.0}
        dates += [p["publicationDate"] for p in products if p.get("publicationDate")]
        if len(found) == 1:
            prices[key] = found.pop()
        else:
            why = "no on-demand USD price" if not found else f"{len(found)} different prices"
            gaps.append(f"price {key}: {why} in {service_code} for {region}")
    return prices, max(dates, default=None), gaps
