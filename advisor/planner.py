"""Planner node: run matrix, cost estimate before any spend, TTL check (SPEC sections 4 and 8)."""

from collections.abc import Callable
from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field

from advisor.cost import Breakdown, CellUsage, SharedUsage, estimate, shared_costs
from advisor.pricing import read_prices
from advisor.state import CellName, CellSpec, Profile, RunState

# Decision 8: the default run is ecs-fargate vs eks-ec2. ecs-ec2 and eks-fargate only when asked.
DEFAULT_CELLS: tuple[CellName, ...] = ("ecs-fargate", "eks-ec2")
PROFILES: tuple[Profile, ...] = get_args(Profile)


class PlanConfig(BaseModel):
    """What the estimate needs. All fields are required: the planner has no hidden defaults."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    instance_type: str
    duration_s: float = Field(gt=0)  # one cell x profile run; runs are counted back to back
    cell: CellUsage
    shared: SharedUsage


def run_matrix(state: RunState) -> list[CellSpec]:
    """Every chosen cell x every chosen profile. None chosen means the default pair x every profile."""
    names = list(dict.fromkeys(c.name for c in state.cells)) or list(DEFAULT_CELLS)
    profiles = list(dict.fromkeys(c.profile for c in state.cells)) or list(PROFILES)
    return [CellSpec(name=name, profile=profile) for name in names for profile in profiles]


def make_planner(client: Any, config: PlanConfig) -> Callable[[RunState], dict]:
    """Build the LangGraph node. `client` is a boto3 pricing client (us-east-1) or a stub."""

    def planner(state: RunState) -> dict:
        matrix = run_matrix(state)
        prices, as_of, gaps = read_prices(client, state.region, config.instance_type)
        run_s = config.duration_s * len(matrix)
        parts = [estimate(c, state.capacity, config.duration_s, prices, config.cell) for c in matrix]
        parts.append(shared_costs(run_s, prices, config.shared))
        total = Breakdown(gaps=gaps)
        for part in parts:
            total.lines += part.lines
            total.gaps += part.gaps
        errors = list(state.errors)
        if run_s > state.ttl_hours * 3600:
            errors.append(f"run of {run_s / 3600:.2f} h exceeds ttl_hours={state.ttl_hours}")
        return {
            "cells": matrix,
            "estimate_usd": round(total.total, 4),
            "estimate_lines": total.lines,
            "prices": prices,
            "prices_as_of": as_of,
            "gaps": list(dict.fromkeys(total.gaps)),
            "errors": errors,
        }

    return planner
