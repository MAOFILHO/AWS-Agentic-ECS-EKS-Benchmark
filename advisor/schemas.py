"""Shape of results/<run-id>/metrics.json (SPEC sections 7 and 8) and of runs/default.json."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from advisor.planner import PlanConfig
from advisor.state import RUN_ID_PATTERN, Capacity, CellName, Profile

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
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: MetricName
    value: float
    unit: str
    # Raw file path, relative to results/<run-id>/raw/. A figure with no source is invalid.
    source: str = Field(min_length=1)
    cell: CellName
    profile: Profile | None = None


class SharedCost(BaseModel):
    """NAT gateway, VPC endpoints, harness: reported on their own line, not spread across cells."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    item: str
    usd: float
    source: str = Field(min_length=1)


class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    figures: list[Figure] = []
    shared_costs: list[SharedCost] = []
    gaps: list[str] = []
    # The load harness ran above its CPU threshold, so latency may reflect the harness (SPEC 12).
    harness_bound: bool = False


class MetricsError(ValueError):
    pass


def read_metrics(path: Path) -> Metrics:
    """Parse metrics.json. A bad file, or a NaN or Infinity number, raises MetricsError, never a default."""
    try:
        return Metrics.model_validate_json(path.read_text())
    except ValidationError as err:
        raise MetricsError(f"{path.name}: {err}") from err


class AggregateFigure(BaseModel):
    """One (name, cell, profile) across repeat runs: how many runs had it, and the spread."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: MetricName
    unit: str
    cell: CellName
    profile: Profile | None = None
    n: int = Field(ge=1)
    min: float
    median: float
    max: float
    runs: list[str] = Field(min_length=1)


class Aggregate(BaseModel):
    """Shape of results/<name>/aggregate.json, written by scripts/aggregate_runs.py."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    runs: list[str] = Field(min_length=1)
    figures: list[AggregateFigure] = []


def read_aggregate(path: Path) -> Aggregate:
    """Parse aggregate.json. A bad file raises MetricsError, never a default."""
    try:
        return Aggregate.model_validate_json(path.read_text())
    except ValidationError as err:
        raise MetricsError(f"{path.name}: {err}") from err


class LoadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    peak_rps: int = Field(gt=0)


class RunConfig(BaseModel):
    """runs/default.json: the capacity block, the plan inputs and the load."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    capacity: Capacity
    plan: PlanConfig
    load: LoadConfig
