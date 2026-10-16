"""Cost model (SPEC section 8): resource-seconds x on-demand price. Pure functions, no defaults.

A line whose price is missing is left out and named in `gaps`; it is never counted as zero.
"""

from pydantic import BaseModel, ConfigDict, Field

from advisor.state import Capacity, CellSpec, CostLine


class CellUsage(BaseModel):
    """Per cell x profile run. `nodes` is ignored on Fargate cells."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    replicas: int = Field(ge=1)
    nodes: int = Field(ge=1)
    lcus: float = Field(ge=0)
    log_gb: float = Field(ge=0)


class SharedUsage(BaseModel):
    """One NAT gateway per run (Decision 6), the VPC endpoints, and the Fargate k6 harness."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    nat_gb: float = Field(ge=0)
    endpoints: int = Field(ge=0)
    harness_vcpu: float = Field(gt=0)
    harness_memory_mib: int = Field(gt=0)
    harness_tasks: int = Field(ge=1)


class Breakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    lines: list[CostLine] = []
    gaps: list[str] = []

    @property
    def total(self) -> float:
        return sum(line.usd for line in self.lines)

    def add(self, prices: dict[str, float], item: str, key: str, quantity: float) -> None:
        if key in prices:
            self.lines.append(CostLine(item=item, usd=quantity * prices[key]))
        else:
            self.gaps.append(f"{item}: price {key} missing")


def estimate(
    cell_spec: CellSpec, capacity: Capacity, duration_s: float, prices: dict[str, float], usage: CellUsage
) -> Breakdown:
    """Cost of one cell for one profile run."""
    h = duration_s / 3600
    tag = f"{cell_spec.name}/{cell_spec.profile}"
    out = Breakdown()
    if cell_spec.name.startswith("eks-"):
        out.add(prices, f"{tag} eks control plane", "eks_cluster_hour", h)
    if cell_spec.name.endswith("-fargate"):
        out.add(prices, f"{tag} fargate vcpu", "fargate_vcpu_hour", capacity.vcpu * usage.replicas * h)
        out.add(prices, f"{tag} fargate memory", "fargate_gb_hour", capacity.memory_mib / 1024 * usage.replicas * h)
    else:
        out.add(prices, f"{tag} ec2 instances", "ec2_instance_hour", usage.nodes * h)
    out.add(prices, f"{tag} alb", "alb_hour", h)
    out.add(prices, f"{tag} alb lcu", "alb_lcu_hour", usage.lcus * h)
    out.add(prices, f"{tag} cloudwatch ingest", "cw_ingest_gb", usage.log_gb)
    return out


def shared_costs(duration_s: float, prices: dict[str, float], usage: SharedUsage) -> Breakdown:
    """Shared lines, reported on their own and never spread across cells."""
    h = duration_s / 3600
    out = Breakdown()
    out.add(prices, "shared nat gateway", "nat_hour", h)
    out.add(prices, "shared nat data", "nat_gb", usage.nat_gb)
    out.add(prices, "shared vpc endpoints", "vpce_hour", usage.endpoints * h)
    out.add(prices, "shared harness vcpu", "fargate_vcpu_hour", usage.harness_vcpu * usage.harness_tasks * h)
    gb = usage.harness_memory_mib / 1024
    out.add(prices, "shared harness memory", "fargate_gb_hour", gb * usage.harness_tasks * h)
    return out
