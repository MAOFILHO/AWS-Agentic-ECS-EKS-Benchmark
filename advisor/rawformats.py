"""Raw file contract: what the collector writes under results/<run-id>/raw/<cell>/.

Every file is JSON. `read` and `write` validate against the file's model and raise RawFormatError on bad data,
including a NaN or Infinity number. The analyst turns a missing or bad file into a gap, never into a zero.

- `k6-<profile>.json`: the k6 `--summary-export` JSON. Read: `metrics.http_req_duration`
  (`med`, `p(95)`, `p(99)` in ms; p(99) needs `--summary-trend-stats` to include it),
  `metrics.http_reqs.count`, and `metrics.http_req_failed` (a Rate: `passes` = failed
  requests, which include timeouts; `fails` = successful requests).
- `cloudwatch-<metric>.json`: a list of `{"timestamp": ISO 8601, "value": number}`.
  `cloudwatch-harness-cpu.json` is the harness CPU percent during the load (SPEC section 12).
- `alb.json`: `{"request_count", "http_5xx", "response_time"}`, each a CloudWatch series.
- `events.json`: ECS task or Kubernetes pod events, a list of
  `{"timestamp": ISO 8601, "kind": "alarm" | "scheduled" | "healthy", "message": str}`.
  `alarm` = autoscaling alarm or HPA decision, `scheduled` = task or pod placed,
  `healthy` = replica passed its health check (`/readyz` 200 or ALB target healthy).
- `deploy.json`: `{"stack_create_start", "stack_create_end", "target_healthy"}`, ISO 8601, and
  `alb_dns_name`. The deployer writes all but `target_healthy` (DeployStarted), which the collector adds.
- `config.json`: `{"config_lines": int, "moving_parts": int}`.
- `cost.json`: `{"load_usd": number, "idle_usd_per_hour": number}`, from the cost model.
"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)


class RawFormatError(ValueError):
    pass


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Duration(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    med: float | None = Field(None, ge=0)
    p95: float | None = Field(None, ge=0, alias="p(95)")
    p99: float | None = Field(None, ge=0, alias="p(99)")


class Count(BaseModel):
    count: int = Field(ge=0)


class Rate(BaseModel):
    passes: int = Field(ge=0)
    fails: int = Field(ge=0)


class K6Metrics(BaseModel):
    http_req_duration: Duration | None = None
    http_reqs: Count
    http_req_failed: Rate


class K6Summary(BaseModel):
    metrics: K6Metrics


class Point(Strict):
    timestamp: AwareDatetime
    value: float


class Alb(Strict):
    request_count: list[Point]
    http_5xx: list[Point]
    response_time: list[Point]


class Event(Strict):
    timestamp: AwareDatetime
    kind: Literal["alarm", "scheduled", "healthy"]
    message: str


class DeployStarted(Strict):
    """deploy.json as the deployer writes it, before the collector adds target_healthy."""

    stack_create_start: AwareDatetime
    stack_create_end: AwareDatetime
    alb_dns_name: str | None = None


class Deploy(DeployStarted):
    target_healthy: AwareDatetime


class Config(Strict):
    config_lines: int = Field(ge=0)
    moving_parts: int = Field(ge=0)


class Cost(Strict):
    load_usd: float = Field(ge=0)
    idle_usd_per_hour: float = Field(ge=0)


def read(path: Path, shape):
    """Parse one raw file into `shape` (a model above, or a list of one)."""
    try:
        return TypeAdapter(shape).validate_python(json.loads(path.read_text()))
    except (json.JSONDecodeError, ValidationError) as err:
        raise RawFormatError(f"{path.name}: {err}") from err


# A 12-digit run is an AWS account id; an address is an email. Neither may reach a file or a log line.
SENSITIVE = re.compile(r"(?<!\d)\d{12}(?!\d)|[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(value):
    """Mask account ids and emails in every string of `value`. Numbers are left alone."""
    if isinstance(value, str):
        return SENSITIVE.sub("*" * 12, value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def write(path: Path, data, shape) -> None:
    """Write one raw file. Data that fails `shape` raises RawFormatError and nothing is written."""
    try:
        TypeAdapter(shape).validate_python(data)
    except ValidationError as err:
        raise RawFormatError(f"{path.name}: {err}") from err
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scrub(data), indent=2) + "\n")
