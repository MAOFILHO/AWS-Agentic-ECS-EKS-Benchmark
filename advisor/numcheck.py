"""Number check for the report: every number must come from metrics.json (ADR 0004).

`check` returns the offenders and never raises. It denies by default: every run of digits in the
text is a number, whatever characters surround it (a range dash, dots, a slash, "to", a word).
A number passes only when
- it equals a figure in metrics.json rounded to the shown precision (a percentage may show a
  ratio times 100), or
- it sits inside one allowed label form from ALLOWED_LABELS (or the run id), or
- it is a list number 1 to 4 at the start of a line ("1. first", "2) second").
Dates, years, plain counts and version numbers are not allowed forms. Account-id-shaped
numbers and email addresses always fail, even when glued to a name.

Allowed label forms, matched as whole words (a digit or letter next to one breaks the match):
p50 p95 p99 ec2 ecs-ec2 ecs-fargate eks-ec2 eks-fargate us-east-1 and the run id,
in any letter case.
"""

import re

from advisor.schemas import Metrics

ALLOWED_LABELS = (
    "p50",
    "p95",
    "p99",
    "ecs-ec2",
    "ecs-fargate",
    "eks-ec2",
    "eks-fargate",
    "ec2",
    "us-east-1",
)
LIST_NUMBER_RE = re.compile(r"(?m)^[ \t]*([1-4])(?=[.)][ \t])")
NUMBER_RE = re.compile(
    r"\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:([kKM])(?![A-Za-z]))?(%)?"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
MULTIPLIERS = {"k": 1e3, "K": 1e3, "M": 1e6}


def _blank(text: str, run_id: str | None) -> str:
    """Replace allowed labels and list numbers with spaces of the same length."""
    labels = sorted(
        ALLOWED_LABELS + ((run_id,) if run_id else ()), key=len, reverse=True
    )
    label_re = re.compile(
        r"(?<!\w)(?:" + "|".join(map(re.escape, labels)) + r")(?!\w)", re.IGNORECASE
    )

    def blank(m: re.Match) -> str:
        return " " * len(m.group(0))

    text = label_re.sub(blank, text)
    return LIST_NUMBER_RE.sub(lambda m: m.group(0).replace(m.group(1), " "), text)


def _numbers(text: str, run_id: str | None = None) -> list[re.Match]:
    return list(NUMBER_RE.finditer(_blank(text, run_id)))


def extract_numbers(text: str, run_id: str | None = None) -> list[str]:
    return [m.group(0) for m in _numbers(text, run_id)]


def _known_values(metrics: Metrics) -> list[float]:
    values = [f.value for f in metrics.figures] + [c.usd for c in metrics.shared_costs]
    # Numbers written inside units and gaps ("usd/1k") may be restated as they are.
    words = [f.unit for f in metrics.figures] + [c.item for c in metrics.shared_costs]
    for m in _numbers(" ".join(words + metrics.gaps), metrics.run_id):
        values.append(_parse(m)[0])
    return values


def _parse(m: re.Match) -> tuple[float, float]:
    """Return (shown value, half a unit of the last shown digit)."""
    whole, frac, suffix, _ = m.groups()
    mult = MULTIPLIERS.get(suffix, 1.0)
    decimals = len(frac) - 1 if frac else 0
    return float(
        whole.replace(",", "") + (frac or "")
    ) * mult, 0.5 * 10**-decimals * mult


def check(text: str, metrics: Metrics) -> list[str]:
    known = _known_values(metrics)
    offenders = []
    for m in _numbers(text, metrics.run_id):
        # Account ids are caught by the whole-text scan below, even when glued to a name.
        if ACCOUNT_ID_RE.fullmatch(m.group(1)):
            continue
        shown, half_step = _parse(m)
        candidates = known + ([v * 100 for v in known] if m.group(4) else [])
        if not any(
            abs(shown - abs(v)) <= half_step + 1e-9 * max(1.0, abs(v))
            for v in candidates
        ):
            offenders.append(m.group(0))
    return offenders + ACCOUNT_ID_RE.findall(text) + EMAIL_RE.findall(text)
