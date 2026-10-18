"""A malformed --slurp + --jq read exits 64; a real gh failure still halts (#129)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".claude/bin/super-board-github-read.py"
WAVE = (ROOT / ".claude/workflows/super-board-wave.js").read_text()
COMMENTS = ["api", "repos/{owner}/{repo}/issues/1/comments", "--paginate", "--slurp"]


@pytest.fixture
def fake_gh(tmp_path):
    """A `gh` on PATH that counts its calls and fails like a GitHub outage."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho x >> "{calls}"\necho "HTTP 502: Bad Gateway" >&2\nexit 1\n')
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SB_GITHUB_HALT_FILE": str(tmp_path / "github-halt.json"),
        "SB_GITHUB_RETRY_DELAY": "0",
    }
    env.pop("SB_GITHUB_READ_STRICT", None)

    def run(*gh_args):
        result = subprocess.run(
            [sys.executable, str(HELPER), "--kind", "comments", "--", *gh_args],
            capture_output=True, text=True, env=env, check=False,
        )
        count = len(calls.read_text().splitlines()) if calls.exists() else 0
        return result, count, tmp_path / "github-halt.json"

    return run


@pytest.mark.parametrize("flag", [["--jq", ".[]"], ["--jq=.[]"], ["-q", ".[]"], ["--template", "{{.}}"], ["-t", "{{.}}"],
                                  ["-q.[]"], ["-t{{.}}"]])
def test_slurp_with_jq_exits_64_without_halting(fake_gh, flag):
    result, calls, halt_file = fake_gh(*COMMENTS, *flag)
    assert result.returncode == 64
    assert calls == 0
    assert not halt_file.exists()
    assert "| jq" in result.stderr


def test_attached_slurp_with_jq_exits_64_without_halting(fake_gh):
    result, calls, halt_file = fake_gh("api", "repos/{owner}/{repo}/issues/1/comments", "--paginate", "--slurp=true", "--jq", ".[]")
    assert result.returncode == 64
    assert calls == 0
    assert not halt_file.exists()


def test_real_gh_failure_on_valid_form_still_halts(fake_gh):
    result, calls, halt_file = fake_gh(*COMMENTS)
    assert result.returncode == 79
    assert calls == 3
    assert halt_file.exists()


def test_read_guidance_shows_how_to_filter_slurped_pages():
    shapes = re.search(r"const READ_SHAPES = \[(.*?)\n\]", WAVE, re.DOTALL).group(1)
    assert "--slurp | jq" in shapes
