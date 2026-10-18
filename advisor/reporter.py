"""Reporter node: writes results/<run-id>/report.md from metrics.json only (ADR 0004, issue #141).

Code writes every table from metrics.json: a figure that exists always shows, a missing one shows
`n/a` with its gap as a note. The LLM writes only the Verdict paragraph. It cannot compute new
figures, such as a percentage difference: a derived figure must already be in metrics.json. Every
Verdict draft goes through `numcheck.check`, `unsupported_claims` and `contradicted_rankings`;
offenders go back to the LLM, at most 3 tries in all. After the third failed try the report is
still written, with the tables and FALLBACK_VERDICT.
"""

import os
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from advisor.numcheck import ACCOUNT_ID_RE, EMAIL_RE, check
from advisor.schemas import Metrics, read_metrics
from advisor.state import RunState
from advisor.store import create_run_dirs

DEFAULT_BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"
MAX_TRIES = 3
FALLBACK_VERDICT = "No checked verdict could be written for this run: read the tables below."
# Table rows: metric -> row label, unit in the label (issue #122).
PROFILE_ROWS = {
    "p50_ms": "P50 latency (ms)",
    "p95_ms": "P95 latency (ms)",
    "p99_ms": "P99 latency (ms)",
    "error_rate": "Error rate",
    "scale_out_s": "Scale-out (s)",
    "cold_start_s": "Cold start (s)",
}
CELL_ROWS = {
    "deploy_time_s": "Deploy time (s)",
    "config_lines": "Config lines",
    "moving_parts": "Moving parts",
    "idle_usd_per_hour": "Idle cost (USD/h)",
    "usd_per_1k_requests": "Cost per 1k requests (USD)",
}
# "higher error rate", "error rate is worse": a comparison word within five words of "error", same clause.
_NEAR = r"(?:[^\w.!?;:\n]+\w+){0,5}?[^\w.!?;:\n]+"
_MORE = r"(?:higher|lower|worse|more)"
ERROR_CLAIM_RE = re.compile(
    rf"\b{_MORE}\b{_NEAR}(?P<a>error\w*)|\b(?P<b>error\w*){_NEAR}{_MORE}\b", re.IGNORECASE
)
_VERDICT_HEADING_RE = re.compile(r"\A#{1,6}[ \t]*Verdict[ \t]*\n", re.IGNORECASE)
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


def _ordered(first: list[str], seen: list) -> list:
    """`first` (matrix order), then the rest of `seen` in the order met; no repeats, no None."""
    return [x for x in dict.fromkeys([*first, *seen]) if x is not None]


def _profiles(metrics: Metrics, state: RunState | None = None) -> list[str]:
    matrix = [c.profile for c in state.cells] if state else []
    return _ordered(matrix, [f.profile for f in metrics.figures])


def _cells(metrics: Metrics, state: RunState | None = None) -> list[str]:
    matrix = [c.name for c in state.cells] if state else []
    return _ordered(matrix, [f.cell for f in metrics.figures])


def _rounded(metrics: Metrics) -> Metrics:
    """Figure values cut to 4 significant digits, so the LLM copies numbers instead of rounding them."""
    figures = [f.model_copy(update={"value": float(f"{f.value:.4g}")}) for f in metrics.figures]
    return metrics.model_copy(update={"figures": figures})


def build_prompt(metrics: Metrics) -> str:
    profiles = sorted(_profiles(metrics))
    rules = [f"Profiles that ran: {', '.join(profiles) or 'none'}. Name no other profile."]
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
    return (
        "Write the Verdict of a report on ECS vs EKS for hosting an AI agent fleet: one short "
        "paragraph of plain prose. No heading, no table, no list: the tables are written for you.\n"
        "Use only the figures in metrics.json below, as given or rounded. Do not compute new "
        "figures: no percentage difference, ratio, sum or average that is not already there.\n"
        "Write no other numbers: no dates, years, counts or versions.\n"
        "Make no claim the figures do not show.\n"
        + "".join(f"{rule}\n" for rule in rules)
        + "Figure values below are rounded to 4 significant digits: copy them as written.\n"
        + f"metrics.json:\n{_rounded(metrics).model_dump_json(indent=2)}\n"
    )


def verdict_of(draft: str) -> str:
    """The draft without a leading "## Verdict" heading, trimmed."""
    return _VERDICT_HEADING_RE.sub("", draft.strip(), count=1).strip()


def _shown(value: float) -> str:
    """4 significant digits, whole numbers from 1000 up, never scientific notation."""
    return f"{value:.0f}" if abs(value) >= 1000 else format(Decimal(f"{value:.4g}"), "f")


def _gap_profile(gap: str, profiles: list[str]) -> str | None:
    m = re.match(r"[\w-]+/([\w-]+)", gap)
    return m.group(1) if m and m.group(1) in profiles else None


def _table(rows: dict[str, str], cells: list[str], values: dict, notes: list[str]) -> str:
    lines = [f"| Metric | {' | '.join(cells)} |", "|---|" + "---|" * len(cells)]
    for name, label in rows.items():
        shown = [_shown(values[name, c]) if (name, c) in values else "n/a" for c in cells]
        lines.append(f"| {label} | {' | '.join(shown)} |")
    if notes:
        lines += ["", "Notes:", *(f"- {n}" for n in notes)]
    return "\n".join(lines) + "\n"


def render_report(metrics: Metrics, verdict: str, state: RunState | None = None) -> str:
    """The whole report: the Verdict as given, every table from metrics.json, cells in matrix order."""
    cells, profiles = _cells(metrics, state), _profiles(metrics, state)
    values = {(f.name, f.cell, f.profile): f.value for f in metrics.figures}
    out = [f"# ECS vs EKS: {metrics.run_id}\n", f"## Verdict\n{verdict}\n", "## Comparison table\n"]
    for p in profiles:
        notes = [
            g for g in metrics.gaps
            if _gap_profile(g, profiles) == p
            or (_gap_profile(g, profiles) is None and any(n in g for n in PROFILE_ROWS))
        ]
        rows = {(n, c): v for (n, c, q), v in values.items() if q == p}
        out.append(f"### {p}\n" + _table(PROFILE_ROWS, cells, rows, notes))
    notes = [g for g in metrics.gaps if _gap_profile(g, profiles) is None and not any(n in g for n in PROFILE_ROWS)]
    rows = {(n, c): v for (n, c, q), v in values.items() if q is None}
    out.append("### Per cell\n" + _table(CELL_ROWS, cells, rows, notes))
    if metrics.shared_costs:
        out.append(
            "## Shared costs\nNot part of any cell.\n\n| Item | USD |\n|---|---|\n"
            + "".join(f"| {c.item} | {_shown(c.usd)} |\n" for c in metrics.shared_costs)
        )
    return "\n".join(out)


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
            verdict = verdict_of(llm.complete(prompt))
            offenders = check(verdict, metrics)
            claims = unsupported_claims(verdict, metrics)
            rankings = contradicted_rankings(verdict, metrics)
            shape = not verdict or bool(re.search(r"^\s*[#|]", verdict, re.MULTILINE))
            if not offenders and not claims and not rankings and not shape:
                paths.report.write_text(render_report(metrics, verdict, state))
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
            if shape:
                problems.append("Write one paragraph of prose only: no heading, no table, not empty.")
            prompt = (
                f"{build_prompt(metrics)}\nYour last draft:\n{verdict}\n"
                f"{' '.join(problems)} Rewrite the Verdict without them."
            )
        paths.report.write_text(render_report(metrics, FALLBACK_VERDICT, state))
        error = (
            f"reporter: fallback Verdict after {MAX_TRIES} tries: "
            + ", ".join([
                *map(_redact, offenders),
                *claims,
                *rankings,
                *(["not one prose paragraph"] if shape else []),
            ])
        )
        return {"status": "reported", "errors": [*state.errors, error]}

    return reporter
