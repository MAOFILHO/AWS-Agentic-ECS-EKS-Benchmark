"""docs/METHOD.md, the method behind the comparison (issue #124).

Every figure in it must match runs/default.json (the plan config) or loadtests/*.js.
The profile rows are rebuilt here from the same formulas the k6 scripts use.
"""

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "docs" / "METHOD.md"
CONFIG = json.loads((ROOT / "runs" / "default.json").read_text())
COMMON_JS = (ROOT / "loadtests" / "common.js").read_text()
TOPICS = ("Matrix", "Equal capacity", "Workload", "Load profiles", "Metrics", "Cost model",
          "Reproduce a run", "Known limits")
# Numbers that are not run figures: the two axes of the matrix and the HTTP status of a ready check.
FIXED = {2.0, 200.0}


def _js_round(x: float) -> int:  # Math.round: halves go up
    return math.floor(x + 0.5)


def _numbers(value) -> list[float]:
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    return [float(value)] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


def _js_default(name: str) -> int:
    return int(re.search(rf"__ENV\.{name} \|\| (\d+)", COMMON_JS).group(1))


def _profile_rows() -> dict[str, str]:
    peak, duration = CONFIG["load"]["peak_rps"], int(CONFIG["plan"]["duration_s"])
    quarter = max(1, duration // 4)
    rest = max(1, duration - quarter)
    steps = [max(1, _js_round(peak * share)) for share in (0.25, 0.5, 0.75, 1)]
    base = max(1, _js_round(peak * 0.1))
    return {
        "steady": f"| `steady` | {peak} RPS for {duration} s |",
        "step": f"| `step` | {steps[0]}, {steps[1]}, {steps[2]} and {steps[3]} RPS, each held {quarter} s |",
        "spike": f"| `spike` | {base} RPS for {quarter} s, then {peak} RPS within 1 s, held {rest} s |",
        "scale-from-min": f"| `scale-from-min` | 0 RPS for {quarter} s, then {peak} RPS within 1 s, held {rest} s |",
    }


def _allowed() -> set[float]:
    cap = CONFIG["capacity"]
    peak, duration = CONFIG["load"]["peak_rps"], int(CONFIG["plan"]["duration_s"])
    rows = " ".join(_profile_rows().values())
    derived = {float(n) for n in re.findall(r"\d+", rows)}
    shares = {10.0, 25.0, 50.0, 75.0, 100.0}  # percent of peak in spike.js and step.js
    return (set(_numbers(CONFIG)) | derived | shares | FIXED
            | {cap["vcpu"] * 1024, float(peak), float(duration),
               float(_js_default("PEAK_RPS")), float(_js_default("DURATION_S"))})


def test_method_covers_each_topic_in_its_own_block():
    headings = re.findall(r"^### (.+)$", METHOD.read_text(), re.MULTILINE)
    assert headings == list(TOPICS)


def test_capacity_line_matches_the_plan_config():
    cap = CONFIG["capacity"]
    text = METHOD.read_text()
    want = (f"{cap['vcpu']} vCPU ({int(cap['vcpu'] * 1024)} CPU units) and {cap['memory_mib']} MiB per replica, "
            f"{cap['min_replicas']} to {cap['max_replicas']} replicas, CPU target {cap['cpu_target']}%")
    assert want in text
    assert f"`{CONFIG['plan']['instance_type']}`" in text


def test_profile_rows_match_the_k6_scripts_and_plan_config():
    text = METHOD.read_text()
    for profile, row in _profile_rows().items():
        assert row in text, profile
    hand = f"{_js_default('PEAK_RPS')} RPS for {_js_default('DURATION_S')} s"
    assert hand in text


def test_every_number_in_method_matches_the_config_or_loadtests():
    # Numbers glued to letters, dashes or dots (p95, k6, us-east-1, t3.medium) are names, not figures.
    text = METHOD.read_text()
    found = re.findall(r"(?<![\w.\-/])(\d+(?:\.\d+)?)(?![\w.\-]*\w)", text)
    allowed = _allowed()
    assert [n for n in found if float(n) not in allowed] == []


def test_readme_links_the_method():
    assert "](docs/METHOD.md)" in (ROOT / "README.md").read_text()


def test_method_holds_no_account_id_email_or_alb_host():
    text = METHOD.read_text()
    assert not re.search(r"\b\d{12}\b|[\w.+-]+@[\w-]+\.[\w.-]+|elb\.amazonaws\.com", text)
