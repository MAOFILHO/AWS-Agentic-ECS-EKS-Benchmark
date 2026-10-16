"""Shape of results/<run-id>/metrics.json (SPEC sections 7 and 8)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from advisor.state import RUN_ID_PATTERN, CellName, Profile

MetricName = Literal[
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "error_rate",
    "scale_out_s",
    "cold_start_s",
    "deploy_time_s",
    "usd_per_1k_requests",
    "idle_usd_per_hour",
    "config_lines",
    "moving_parts",
]


class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: MetricName
    value: float
    unit: str
    # Raw file path, relative to results/<run-id>/raw/. A figure with no source is invalid.
    source: str = Field(min_length=1)
    cell: CellName
    profile: Profile | None = None


class SharedCost(BaseModel):
    """NAT gateway, VPC endpoints, harness: reported on their own line, not spread across cells."""

    model_config = ConfigDict(extra="forbid")

    item: str
    usd: float
    source: str = Field(min_length=1)


class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    figures: list[Figure] = []
    shared_costs: list[SharedCost] = []
    gaps: list[str] = []
    # The load harness ran above its CPU threshold, so latency may reflect the harness (SPEC 12).
    harness_bound: bool = False
