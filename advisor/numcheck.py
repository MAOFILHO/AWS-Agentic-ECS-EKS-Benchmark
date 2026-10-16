"""Number check for the report: every number must come from metrics.json (ADR 0004).

`check` returns the offenders and never raises. A shown number passes when it equals a figure
rounded to the shown precision. Percentages may show a ratio times 100. The plain counts 1 to 4
(list items) and plain years pass. Account-id-shaped numbers and email addresses always fail.
"""

import re

from advisor.schemas import Metrics

# Not inside a word ("ec2", "p95"), a decimal ("v1.2") or a hyphenated name ("smoke-01").
NUMBER_RE = re.compile(
    r"(?<![\w.])(?<!\w-)\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:([kKM])(?![A-Za-z]))?(%)?"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
MULTIPLIERS = {"k": 1e3, "K": 1e3, "M": 1e6}


def extract_numbers(text: str) -> list[str]:
    return [m.group(0) for m in NUMBER_RE.finditer(text)]


def _known_values(metrics: Metrics) -> list[float]:
    values = [f.value for f in metrics.figures] + [c.usd for c in metrics.shared_costs]
    # Numbers written inside units and gaps ("usd/1k") may be restated as they are.
    words = [f.unit for f in metrics.figures] + [c.item for c in metrics.shared_costs]
    for m in NUMBER_RE.finditer(" ".join(words + metrics.gaps)):
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


def _is_plain_count_or_year(m: re.Match) -> bool:
    whole, frac, suffix, pct = m.groups()
    if frac or suffix or pct or m.group(0).startswith("$") or "," in whole:
        return False
    return 1 <= int(whole) <= 4 or (len(whole) == 4 and 1900 <= int(whole) <= 2100)


def check(text: str, metrics: Metrics) -> list[str]:
    known = _known_values(metrics)
    offenders = []
    for m in NUMBER_RE.finditer(text):
        # Account ids are caught by the whole-text scan below, even when glued to a name.
        if ACCOUNT_ID_RE.fullmatch(m.group(1)) or _is_plain_count_or_year(m):
            continue
        shown, half_step = _parse(m)
        candidates = known + ([v * 100 for v in known] if m.group(4) else [])
        if not any(
            abs(shown - abs(v)) <= half_step + 1e-9 * max(1.0, abs(v))
            for v in candidates
        ):
            offenders.append(m.group(0))
    return offenders + ACCOUNT_ID_RE.findall(text) + EMAIL_RE.findall(text)
