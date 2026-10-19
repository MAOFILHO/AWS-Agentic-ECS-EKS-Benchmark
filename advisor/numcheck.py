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


# Which metric a table row label or a sentence is about (issue #150).
_METRIC_WORDS = {
    "p50_ms": r"\bp50\b",
    "p95_ms": r"\bp95\b",
    "p99_ms": r"\bp99\b",
    "error_rate": r"\berrors?\b",
    "scale_out_s": r"\bscal(?:e|ed|es|ing)[ -]out\b",
    "cold_start_s": r"\bcold start",
    "deploy_time_s": r"\bdeploy",
    "config_lines": r"\bconfig lines\b",
    "moving_parts": r"\bmoving parts\b",
    "idle_usd_per_hour": r"\bidle\b",
    "usd_per_1k_requests": r"\bper (?:1k|thousand)\b",
}
_CELL_NAME_RE = re.compile(r"\b(ECS|EKS)(?:-|\s+(?:with\s+|on\s+|using\s+)?)(EC2|Fargate)\b", re.IGNORECASE)
_BLOCK_START_RE = re.compile(r"\s*(?:\d+\.|[-*])\s")
_PROFILE_HEAD_RE = re.compile(r"\*\*(?:Profile `([\w-]+)`|Per cell)\*\*")


def _scoped(metrics: Metrics, names: set[str], cells: set[str], profile: str | None, any_profile: bool) -> Metrics:
    keep = [
        f for f in metrics.figures
        if (not names or f.name in names) and (not cells or f.cell in cells) and (any_profile or f.profile == profile)
    ]
    return metrics.model_copy(update={"figures": keep})


def _blocks(text: str) -> list[tuple[str, str | None, list[str] | None]]:
    """(text, profile, header) per piece: a table row (with its header), or a sentence of prose.

    A fenced code block comes back as one piece with the header `[]`: it names no cell or metric.
    """
    out, para, profile, header, fenced = [], [], None, None, []
    in_fence = False
    for line in [*text.splitlines(), ""]:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            if not in_fence:
                out.append(("\n".join(fenced), None, []))
                fenced = []
            continue
        if in_fence:
            fenced.append(line)
            continue
        row = line.strip().startswith("|")
        if row or not line.strip() or _BLOCK_START_RE.match(line):
            sentences = re.split(r"(?<=[.!?])\s+", " ".join(para).strip()) if para else []
            out += [(s, None, None) for s in sentences if s]
            para = []
        if row:
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if cols[0].lower() == "metric":
                header = [c.lower() for c in cols]
            elif not set(line.strip()) <= set("|-: "):
                out.append((line, profile, header))
            continue
        header = None
        if head := _PROFILE_HEAD_RE.fullmatch(line.strip()):
            profile = head.group(1)
        elif line.strip():
            marker = _BLOCK_START_RE.match(line)
            para.append(line[marker.end():].strip() if marker else line.strip())
    return out


def check_scoped(text: str, metrics: Metrics) -> list[str]:
    """Like `check`, but a number must match a figure of the cell and metric its row or sentence names.

    A table row takes its metric from the row label, its cell from the column header and its
    profile from the heading above the table. A sentence takes the metrics and cells it names, and
    the cells of the sentence before it ("ECS Fargate. Its p95 was ..."); one that names none of a
    kind accepts any. Within a sentence the numbers are not told apart per cell.
    """
    offenders, previous = [], set()
    for piece, profile, header in _blocks(text):
        if header is None:
            names = {n for n, w in _METRIC_WORDS.items() if re.search(w, piece, re.IGNORECASE)}
            cells = {"-".join(m.groups()).lower() for m in _CELL_NAME_RE.finditer(piece)}
            offenders += check(piece, _scoped(metrics, names, cells | previous if cells else cells, None, True))
            previous = cells
            continue
        previous = set()
        if not header:  # fenced code
            offenders += check(piece, metrics)
            continue
        cols = [c.strip() for c in piece.strip().strip("|").split("|")]
        names = {n for n, w in _METRIC_WORDS.items() if re.search(w, cols[0], re.IGNORECASE)}
        for cell, col in zip(header[1:], cols[1:]):
            offenders += check(col, _scoped(metrics, names, {cell}, profile, False))
    return offenders
