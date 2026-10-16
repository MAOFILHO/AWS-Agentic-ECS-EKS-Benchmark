"""Ctrl+C and SIGTERM during a deploy run teardown (issue #92). Fake runner, no AWS."""

import os
import re
import signal
import sys
import threading

import pytest

from advisor import cli, deployer
from tests.test_cost import StubPricing

ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
ARGV = ["run", "--region", "us-east-1", "--run-id", "int-01", "--profile", "steady", "--cells", "ecs-fargate,eks-ec2"]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(cli.boto3, "client", lambda *a, **k: pytest.fail("AWS call"))
    monkeypatch.setenv("AWS_PROFILE", "test")
    monkeypatch.setenv("IMAGE_URI", "repo:tag")
    monkeypatch.setenv("K6_IMAGE_URI", "repo:k6-tag")


def interrupting(monkeypatch, sig, teardown_sig=None):
    """A runner that sends `sig` to this process on the first deploy command, and `teardown_sig` on each delete_run."""
    calls = []

    def runner(argv, env):
        calls.append(argv)
        if any(a.endswith("deploy_stack.py") for a in argv) and sig and len(calls) == 1:
            os.kill(os.getpid(), sig)
        if any(a.endswith("delete_run.py") for a in argv) and teardown_sig:
            os.kill(os.getpid(), teardown_sig)
        return 0, ""

    monkeypatch.setattr(deployer, "subprocess_runner", runner)
    return calls


def run(tmp_path):
    return cli.main(ARGV, pricing=StubPricing(), ask=lambda prompt: "y", root=tmp_path)


def torn_down(calls):
    return [a[a.index("--run-id") + 1] for a in calls if any(x.endswith("delete_run.py") for x in a)]


def test_ctrl_c_during_deploy_runs_teardown_and_exits_130(tmp_path, monkeypatch, capsys):
    calls = interrupting(monkeypatch, signal.SIGINT)
    assert run(tmp_path) == 130
    assert torn_down(calls) == ["int-01-ef", "int-01-ke"]
    assert "run int-01: interrupted" in capsys.readouterr().out


def test_sigterm_during_deploy_runs_teardown(tmp_path, monkeypatch):
    calls = interrupting(monkeypatch, signal.SIGTERM)
    assert run(tmp_path) == 130
    assert torn_down(calls) == ["int-01-ef", "int-01-ke"]


def test_ctrl_c_during_the_harness_deploy_tears_down_that_cell(tmp_path, monkeypatch):
    """The harness stack is `<cell run id>-harness`, so delete_run.py for that run id removes it (issue #103)."""
    calls = []

    def runner(argv, env):
        calls.append(argv)
        if "infra/cfn/20-harness.yaml" in argv:
            os.kill(os.getpid(), signal.SIGINT)
        return 0, ""

    monkeypatch.setattr(deployer, "subprocess_runner", runner)
    assert run(tmp_path) == 130
    assert any("int-01-ef-harness" in a for a in calls)
    assert torn_down(calls)[0] == "int-01-ef"


def test_a_second_interrupt_during_teardown_does_not_stop_it(tmp_path, monkeypatch, capsys):
    calls = interrupting(monkeypatch, signal.SIGINT, teardown_sig=signal.SIGINT)
    assert run(tmp_path) == 130
    assert torn_down(calls) == ["int-01-ef", "int-01-ke"]  # both cells, despite an interrupt in each
    assert "teardown is running, please wait" in capsys.readouterr().out


def test_handlers_are_restored_after_the_run(tmp_path, monkeypatch):
    interrupting(monkeypatch, signal.SIGINT)
    before = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    run(tmp_path)
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == before


def test_the_state_is_interrupted_and_resume_reads_it(tmp_path, monkeypatch, capsys):
    interrupting(monkeypatch, signal.SIGINT)
    run(tmp_path)
    capsys.readouterr()
    log = (tmp_path / "runs" / "int-01" / "commands.jsonl").read_text()
    assert '"exit": 130' in log  # the interrupted command is recorded
    assert cli.main(["resume", "int-01"], root=tmp_path) == 130
    assert "run int-01: interrupted" in capsys.readouterr().out


def test_leftovers_after_an_interrupt_are_not_hidden(tmp_path, monkeypatch, capsys):
    calls = []

    def runner(argv, env):
        calls.append(argv)
        if any(a.endswith("deploy_stack.py") for a in argv) and len(calls) == 1:
            os.kill(os.getpid(), signal.SIGINT)
        return (1, "") if any(a.endswith("delete_run.py") for a in argv) else (0, "")

    monkeypatch.setattr(deployer, "subprocess_runner", runner)
    run(tmp_path)
    assert "run int-01: leftovers" in capsys.readouterr().out


def test_interrupt_before_the_yes_deploys_nothing(tmp_path, monkeypatch):
    calls = interrupting(monkeypatch, None)

    def ctrl_c(prompt):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cli.main(ARGV, pricing=StubPricing(), ask=ctrl_c, root=tmp_path)
    assert calls == []


def test_no_account_id_or_email_in_output_or_files(tmp_path, monkeypatch, capsys):
    interrupting(monkeypatch, signal.SIGINT)
    run(tmp_path)
    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert not ACCOUNT_ID.search(text) and not EMAIL.search(text)
    for path in (tmp_path / "runs").rglob("commands.jsonl"):
        assert not ACCOUNT_ID.search(path.read_text())


def test_the_real_runner_lets_a_child_finish_before_re_raising(tmp_path):
    marker = tmp_path / "done"
    code = "import sys,time,pathlib;time.sleep(0.5);pathlib.Path(sys.argv[1]).write_text('x')"
    timer = threading.Timer(0.1, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    with pytest.raises(KeyboardInterrupt):
        deployer.subprocess_runner([sys.executable, "-c", code, str(marker)], {})
    assert marker.exists()  # the child was not killed mid-way
