"""The review lane prompt lists the gh reads that work, so agents stop inventing bad ones (#39)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAVE = (ROOT / ".claude/workflows/super-board-wave.js").read_text()
CONFIG = json.loads(
    (ROOT / ".claude/super-board/configs/agentic-platform-advisor.json").read_text()
)


def _review_reads() -> str:
    match = re.search(r"const REVIEW_READS = \[(.*?)\n\]", WAVE, re.DOTALL)
    assert match, "REVIEW_READS block missing from super-board-wave.js"
    return match.group(1)


def test_review_reads_name_the_repo_from_config():
    repo = CONFIG["repo"]["remote"].removeprefix("https://github.com/")
    assert repo in _review_reads()


def test_review_reads_list_valid_reads():
    reads = _review_reads()
    assert "gh pr view" in reads and "--json" in reads
    assert "gh api graphql" in reads and "reviewThreads" in reads


def test_review_reads_name_the_three_invalid_patterns():
    reads = _review_reads()
    assert "--slurp" in reads and "--jq" in reads
    assert "reviewThreads" in reads and "not a" in reads
    assert "wrong repo" in reads


def test_review_lane_includes_the_reads():
    assert "...REVIEW_READS" in WAVE
