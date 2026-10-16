"""Number check for the report: every number must come from metrics.json (ADR 0004).

`check` returns the offenders and never raises. A shown number passes when it equals a figure
rounded to the shown precision. Percentages may show a ratio times 100. The plain counts 1 to 4
(list items) and plain years pass. Account-id-shaped numbers and email addresses always fail.

Every number in a range or pair is checked: "120-777", "120–777", "120—777", "120 to 777",
"120/777", "120 -777" and "$5-$99" each check both sides. Only these label forms are skipped:
- a digit inside a word: "p95", "ec2", "eks-ec2", "v1.2"
- a number after a hyphen that follows a word with a letter: "smoke-01", "us-east-1"
- the month and day of a valid ISO date "2026-10-15" (the year is checked as a plain year);
  a dashed group that is not a valid date ("2024-5555-6666") is checked like any range
"""

import re

from advisor.schemas import Metrics

# Not inside a word ("ec2", "p95") or a decimal ("v1.2"); hyphen labels are filtered below.
NUMBER_RE = re.compile(
    r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:([kKM])(?![A-Za-z]))?(%)?"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
DATE_RE = re.compile(r"(?<![\w.-])(\d{4})-(\d{2})-(\d{2})(?![\w-])")
NUMBER_TOKEN_RE = re.compile(r"\$?[\d.,]*\d[kKM]?%?")
LEFT_TOKEN_RE = re.compile(r"[\w.,$%]*$")
MULTIPLIERS = {"k": 1e3, "K": 1e3, "M": 1e6}


def _date_interiors(text: str) -> set[int]:
    """Start offsets of the month and day of each valid ISO date."""
    starts = set()
    for d in DATE_RE.finditer(text):
        year, month, day = (int(g) for g in d.groups())
        if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            starts.update({d.start(2), d.start(3)})
    return starts


def _is_label(text: str, start: int, date_interiors: set[int]) -> bool:
    if start in date_interiors:
        return True
    if start == 0 or text[start - 1] != "-":
        return False
    left = LEFT_TOKEN_RE.search(text, 0, start - 1).group(0)
    # A bare number on the left is a range ("120-777"); a word with a letter is a name.
    return bool(left) and not NUMBER_TOKEN_RE.fullmatch(left)


def _numbers(text: str) -> list[re.Match]:
    interiors = _date_interiors(text)
    return [
        m for m in NUMBER_RE.finditer(text) if not _is_label(text, m.start(), interiors)
    ]


def extract_numbers(text: str) -> list[str]:
    return [m.group(0) for m in _numbers(text)]


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
    for m in _numbers(text):
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
