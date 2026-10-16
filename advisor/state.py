"""Typed state shared by every node of the advisor graph."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The run id flows into stack names and tags, so keep it short and DNS-safe.
RUN_ID_PATTERN = r"^[a-z0-9-]{1,24}$"

CellName = Literal["ecs-fargate", "ecs-ec2", "eks-fargate", "eks-ec2"]
Profile = Literal["steady", "step", "spike", "scale-from-min"]


class CellSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: CellName
    profile: Profile


class Capacity(BaseModel):
    """One capacity block, applied to every cell (SPEC section 4)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    vcpu: float = Field(gt=0)
    memory_mib: int = Field(gt=0)
    min_replicas: int = Field(ge=1)
    max_replicas: int = Field(ge=1)
    cpu_target: int = 60


class CostLine(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    item: str
    usd: float


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    cells: list[CellSpec]
    capacity: Capacity
    region: str
    ttl_hours: int = 4
    estimate_usd: float | None = None
    estimate_lines: list[CostLine] = []
    # On-demand USD prices from the Pricing API at plan time, and that response's publication date.
    prices: dict[str, float] = {}
    prices_as_of: str | None = None
    gaps: list[str] = []
    approved: bool | None = None
    approval: str | None = None  # who said yes or no: "human: yes", "auto: ..."
    status: str = "planned"
    errors: list[str] = []
    actual_usd: float | None = None
    # Per platform cell: [load start, load end], ISO 8601. The load runner writes it, the collector reads it.
    load_windows: dict[str, list[str]] = {}
