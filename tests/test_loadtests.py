"""Static checks on loadtests/: the four k6 profiles and the image. Only `k6 inspect` runs k6."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import push_image

ROOT = Path(__file__).resolve().parent.parent
LOADTESTS = ROOT / "loadtests"
EXECUTORS = {
    "steady": "constant-arrival-rate",
    "step": "ramping-arrival-rate",
    "spike": "ramping-arrival-rate",
    "scale-from-min": "ramping-arrival-rate",
}


def test_one_script_per_profile():
    assert {p.stem for p in LOADTESTS.glob("*.js")} == {*EXECUTORS, "common"}


@pytest.mark.parametrize("profile", EXECUTORS)
def test_profile_controls_the_arrival_rate_not_the_users(profile):
    text = (LOADTESTS / f"{profile}.js").read_text()
    assert re.findall(r'executor: "([\w-]+)"', text) == [EXECUTORS[profile]]
    assert "export const options" in text and "export default triage" in text


def test_scale_from_min_starts_with_no_traffic():
    text = (LOADTESTS / "scale-from-min.js").read_text()
    assert "startRate: 0" in text and "{ target: 0," in text


def test_request_is_a_json_post_to_triage_checked_for_200_with_env_inputs():
    text = (LOADTESTS / "common.js").read_text()
    assert "http.post(`${target}/triage`" in text and "application/json" in text
    assert "r.status === 200" in text and "JSON.stringify({ text:" in text
    assert all(f"__ENV.{n}" in text for n in ("TARGET_URL", "PEAK_RPS", "DURATION_S"))


def test_dockerfile_builds_from_the_pinned_k6_base_the_mirror_pushes():
    text = (LOADTESTS / "Dockerfile").read_text()
    assert f"ARG BASE_IMAGE={push_image.K6_BASE}\n" in text and "FROM ${BASE_IMAGE}" in text
    assert re.fullmatch(r"grafana/k6:\d+\.\d+\.\d+", push_image.K6_BASE)
    assert "COPY *.js /scripts/" in text


def test_no_account_id_or_email_in_the_new_files():
    for path in [*LOADTESTS.iterdir(), ROOT / "infra/cfn/20-harness.yaml"]:
        text = path.read_text()
        assert not re.search(r"\b\d{12}\b|[\w.+-]+@[\w-]+\.[\w.]+", text), path


@pytest.mark.skipif(shutil.which("k6") is None, reason="k6 is not installed")
@pytest.mark.parametrize("profile", EXECUTORS)
def test_k6_inspect_accepts_the_script(profile):
    cmd = ["k6", "inspect", str(LOADTESTS / f"{profile}.js")]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
