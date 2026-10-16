"""Reporter node: an LLM writes results/<run-id>/report.md from metrics.json only (ADR 0004).

The reporter cannot compute new figures, such as a percentage difference. A derived figure must
already be in metrics.json: add the field to the analyst first. Every draft goes through
`numcheck.check`; offenders go back to the LLM, at most 3 tries in all. After the third failed
try the step fails, metrics.json stays, and no report is written.
"""

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from advisor.numcheck import ACCOUNT_ID_RE, EMAIL_RE, check
from advisor.schemas import Metrics, read_metrics
from advisor.state import RunState
from advisor.store import create_run_dirs

DEFAULT_BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"
MAX_TRIES = 3


class LLM(Protocol):
    def complete(self, prompt: str) -> str: ...


class BedrockLLM:
    """Bedrock through the Converse API; model id from BEDROCK_MODEL_ID."""

    def __init__(self, client=None, env: Mapping[str, str] = os.environ):
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime")
        self.client = client
        self.model_id = env.get("BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID

    def complete(self, prompt: str) -> str:
        resp = self.client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        return resp["output"]["message"]["content"][0]["text"]


def build_prompt(metrics: Metrics) -> str:
    return (
        "Write a short Markdown report on ECS vs EKS for hosting an AI agent fleet: a verdict, "
        "a comparison table, the trade-offs, and a recommendation.\n"
        "Use only the figures in metrics.json below, as given or rounded. Do not compute new "
        "figures: no percentage difference, ratio, sum or average that is not already there.\n"
        "Write no other numbers: no dates, years, counts or versions. Number list items 1 to 4 "
        "only, at the start of a line.\n"
        f"metrics.json:\n{metrics.model_dump_json(indent=2)}\n"
    )


def _redact(offender: str) -> str:
    if EMAIL_RE.fullmatch(offender) or ACCOUNT_ID_RE.search(offender):
        return "<redacted>"
    return offender


def make_reporter(llm: LLM, root: Path) -> Callable[[RunState], dict]:
    def reporter(state: RunState) -> dict:
        paths = create_run_dirs(state.run_id, root)
        metrics = read_metrics(paths.metrics)
        prompt = build_prompt(metrics)
        for _ in range(MAX_TRIES):
            draft = llm.complete(prompt)
            offenders = check(draft, metrics)
            if not offenders:
                paths.report.write_text(draft)
                return {"status": "reported"}
            prompt = (
                f"{build_prompt(metrics)}\nYour last draft:\n{draft}\n"
                f"These numbers are not in metrics.json: {', '.join(offenders)}. "
                "Rewrite the report without them."
            )
        error = (
            f"reporter: numbers not in metrics.json after {MAX_TRIES} tries: "
            + ", ".join(map(_redact, offenders))
        )
        return {"status": "report_failed", "errors": [*state.errors, error]}

    return reporter
