"""Reporter node: an LLM writes results/<run-id>/report.md from metrics.json only (ADR 0004).

The reporter cannot compute new figures, such as a percentage difference. A derived figure must
already be in metrics.json: add the field to the analyst first. Every draft goes through
`numcheck.check`, `unsupported_claims` and `contradicted_rankings`; offenders go back to the LLM, at most 3 tries in all. After the third failed
try the step fails, metrics.json stays, and no report is written.
"""

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from advisor.numcheck import ACCOUNT_ID_RE, EMAIL_RE, check
from advisor.schemas import Metrics, read_metrics
from advisor.state import RunState
from advisor.store import create_run_dirs

DEFAULT_BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"
MAX_TRIES = 3
# "higher error rate", "error rate is worse": a comparison word within five words of "error", same clause.
_NEAR = r"(?:[^\w.!?;:\n]+\w+){0,5}?[^\w.!?;:\n]+"
_MORE = r"(?:higher|lower|worse|more)"
ERROR_CLAIM_RE = re.compile(
    rf"\b{_MORE}\b{_NEAR}(?P<a>error\w*)|\b(?P<b>error\w*){_NEAR}{_MORE}\b", re.IGNORECASE
)
# A "Verdict" heading with text under it; the title's "A Verdict" does not count.
_VERDICT_RE = re.compile(r"^#{1,6}[ \t]*Verdict[ \t]*\n+(?!#)\S", re.IGNORECASE | re.MULTILINE)
# "no errors", "zero errors", "without errors" state the all-zero fact, not a comparison.
_NEGATED_RE = re.compile(r"\b(?:no|zero|without)\W+$", re.IGNORECASE)
# Ranking claims: "X is more costly than Y", "X has lower latency than Y" (issue #135).
RANKED = {
    "cost": ("idle_usd_per_hour", "usd_per_1k_requests"),
    "latency": ("p50_ms", "p95_ms", "p99_ms"),
}
# Comparative word -> (kind, +1 when the first cell's figure is higher).
_COMPARATIVES = {
    r"more (?:costly|expensive)|costlier|pricier|costs? more|higher (?:idle )?costs?": ("cost", 1),
    r"cheaper|less (?:costly|expensive)|costs? less|lower (?:idle )?costs?": ("cost", -1),
    r"slower|(?:higher|more) latency": ("latency", 1),
    r"faster|quicker|(?:lower|less) latency": ("latency", -1),
}
_RANK_RE = re.compile(
    r"\b(?:" + "|".join(f"(?P<c{i}>{p})" for i, p in enumerate(_COMPARATIVES)) + r")\b"
    r"(?P<rest>.*?)\b(?:than|compared (?:to|with))\b",
    re.IGNORECASE,
)
# "ECS EC2", "ECS with EC2", "EKS on Fargate", "ecs-ec2".
_CELL_RE = re.compile(r"\b(ECS|EKS)(?:-|\s+(?:with\s+|on\s+|using\s+)?)(EC2|Fargate)\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?](?!\s|$)[^.!?\n]*)*")


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


def _profiles(metrics: Metrics) -> list[str]:
    return sorted({f.profile for f in metrics.figures if f.profile})


def _rounded(metrics: Metrics) -> Metrics:
    """Figure values cut to 4 significant digits, so the LLM copies numbers instead of rounding them."""
    figures = [f.model_copy(update={"value": float(f"{f.value:.4g}")}) for f in metrics.figures]
    return metrics.model_copy(update={"figures": figures})


def build_prompt(metrics: Metrics) -> str:
    profiles = _profiles(metrics)
    rules = [f"Profiles that ran: {', '.join(profiles) or 'none'}. Name no other profile."]
    if profiles:
        rules.append(
            "Under ## Comparison table, write one table per profile, each under its own ### heading "
            f"that is just the profile name: {', '.join(profiles)}. Each profile table has a column "
            "for every cell and the p95 latency, p99 latency and error rate rows of that profile. "
            "After them, one ### Per cell heading with one table, written once, for the rows with no "
            "profile: deploy time (s), config lines, idle cost (USD/h), cost per thousand requests (USD)."
        )
    if set(profiles) <= {"steady"}:
        rules.append(
            "The steady profile measures no scaling: make no claim about scaling, scale-out or cold start."
        )
    error_rates = [f.value for f in metrics.figures if f.name == "error_rate"]
    if error_rates and not any(error_rates):
        rules.append('Every error rate is zero: write "no errors in any cell" and compare no error rates.')
    facts = rank_facts(metrics)
    if facts:
        rules.append(
            "Ranking facts, the only cost or latency rankings you may state:\n"
            + "\n".join(f"- {fact}" for fact in facts)
        )
    if metrics.shared_costs:
        rules.append(
            "Shared costs, not part of any cell: list each on its own line under a shared cost heading.\n"
            + "\n".join(f"- {c.item}: {c.usd} USD" for c in metrics.shared_costs)
        )
    return (
        "Write a short Markdown report on ECS vs EKS for hosting an AI agent fleet.\n"
        "Use these four sections, in this order, each with an H2 heading: "
        "## Verdict (one short paragraph, before the table), ## Comparison table, "
        "## Trade-offs, ## Recommendation.\n"
        "Comparison table: one column per cell, one row per metric, with the unit in the row label "
        "(for example \"P95 latency (ms)\", \"Idle cost (USD/h)\"); no unit in any column header.\n"
        "Use only the figures in metrics.json below, as given or rounded. Do not compute new "
        "figures: no percentage difference, ratio, sum or average that is not already there.\n"
        "Write no other numbers: no dates, years, counts or versions. Number list items 1 to 4 "
        "only, at the start of a line.\n"
        "Make no claim the figures do not show.\n"
        + "".join(f"{rule}\n" for rule in rules)
        + "Figure values below are rounded to 4 significant digits: copy them as written.\n"
        + f"metrics.json:\n{_rounded(metrics).model_dump_json(indent=2)}\n"
    )


def has_verdict(text: str) -> bool:
    return bool(_VERDICT_RE.search(text))


def missing_profiles(text: str, metrics: Metrics) -> list[str]:
    """Profiles with no heading of their own, when more than one profile ran."""
    profiles = _profiles(metrics)
    if len(profiles) < 2:
        return []
    flags = re.IGNORECASE | re.MULTILINE
    heading = r"^#{2,6}[^\n]*(?<![\w-])%s(?![\w-])"
    return [p for p in profiles if not re.search(heading % re.escape(p), text, flags)]


def unsupported_claims(text: str, metrics: Metrics) -> list[str]:
    """Comparative error claims ("higher error rates") when every error_rate figure is the same."""
    error_rates = {f.value for f in metrics.figures if f.name == "error_rate"}
    if len(error_rates) != 1:
        return []
    claims, pos = [], 0
    while m := ERROR_CLAIM_RE.search(text, pos):
        word = "a" if m.group("a") else "b"
        if _NEGATED_RE.search(text[: m.start(word)]):
            pos = m.end(word)  # "no errors" is fine; look again past that word
        else:
            claims.append(m.group(0))
            pos = m.end()
    return claims


def _ranked_groups(metrics: Metrics) -> dict[tuple[str, str | None], dict[str, float]]:
    """(metric, profile) -> {cell: value} for the cost and latency metrics."""
    names = {n for kind in RANKED.values() for n in kind}
    groups: dict[tuple[str, str | None], dict[str, float]] = {}
    for f in metrics.figures:
        if f.name in names:
            groups.setdefault((f.name, f.profile), {})[f.cell] = f.value
    return groups


def rank_facts(metrics: Metrics) -> list[str]:
    """Cells from lowest to highest, per cost or latency metric and profile, where two or more cells ran."""
    facts = []
    for (name, profile), cells in sorted(_ranked_groups(metrics).items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        if len(cells) > 1:
            label = f"{name} ({profile})" if profile else name
            facts.append(f"{label}: lowest to highest: {', '.join(sorted(cells, key=cells.get))}")
    return facts


def contradicted_rankings(text: str, metrics: Metrics) -> list[str]:
    """Sentences ranking two cells on cost or latency that no figure in metrics.json supports.

    A sentence counts only when a cell is named before the comparative word and another after
    "than"; anything less clear is left alone. A claim passes when any figure of that kind, in any
    profile, ranks the two cells that way.
    """
    groups = _ranked_groups(metrics)
    claims = []
    for sentence in _SENTENCE_RE.findall(text):
        m = _RANK_RE.search(sentence)
        if not m:
            continue
        before = _CELL_RE.findall(sentence[: m.start()])
        after = _CELL_RE.findall(sentence[m.end():])
        if not before or not after:
            continue
        (a, b) = ("-".join(before[-1]).lower(), "-".join(after[0]).lower())
        kind, sign = next(v for i, v in enumerate(_COMPARATIVES.values()) if m.group(f"c{i}"))
        pairs = [
            (cells[a], cells[b])
            for (name, _), cells in groups.items()
            if name in RANKED[kind] and a in cells and b in cells
        ]
        if a != b and pairs and not any(sign * (x - y) > 0 for x, y in pairs):
            claims.append(sentence.strip())
    return claims


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
            claims = unsupported_claims(draft, metrics)
            rankings = contradicted_rankings(draft, metrics)
            missing = not has_verdict(draft)
            no_section = missing_profiles(draft, metrics)
            if not offenders and not claims and not rankings and not missing and not no_section:
                paths.report.write_text(draft)
                return {"status": "reported"}
            problems = []
            if offenders:
                problems.append(f"These numbers are not in metrics.json: {', '.join(offenders)}.")
            if claims:
                problems.append(
                    f"Every error rate is the same, so these claims are false: {'; '.join(claims)}."
                )
            if rankings:
                problems.append(
                    f"These rankings contradict metrics.json: {'; '.join(rankings)}. "
                    "State only rankings from the ranking facts."
                )
            if missing:
                problems.append("The report has no Verdict section: start with a ## Verdict paragraph.")
            if no_section:
                problems.append(
                    f"These profiles have no ### table section: {', '.join(no_section)}."
                )
            prompt = (
                f"{build_prompt(metrics)}\nYour last draft:\n{draft}\n"
                f"{' '.join(problems)} Rewrite the report without them."
            )
        error = (
            f"reporter: numbers not in metrics.json or unsupported claims after {MAX_TRIES} tries: "
            + ", ".join([
                *map(_redact, offenders),
                *claims,
                *rankings,
                *(["no Verdict section"] if missing else []),
                *(f"no {p} section" for p in no_section),
            ])
        )
        return {"status": "report_failed", "errors": [*state.errors, error]}

    return reporter
