"""Teardown node with a fake subprocess runner: no AWS, no real command (issue #85)."""

import re

from advisor import cli, deployer
from advisor.teardown import make_teardown
from tests.test_cost import StubPricing
from tests.test_deployer import ACCOUNT, ACCOUNT_ID, REGION, FakeRunner, state

LEFTOVER = f"  leftover arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/vpc-0abc\nerror: 1 resource(s) tagged run-id=x still live.\n"


def test_teardown_calls_delete_run_per_cell_with_region_and_yes(tmp_path):
    run = FakeRunner()
    out = make_teardown("sso", tmp_path, run=run)(state())
    assert out == {"status": "done"}
    argvs = [argv for argv, _ in run.calls]
    assert [a[a.index("--run-id") + 1] for a in argvs] == ["dep-01-ef", "dep-01-ke"]
    assert all(a[a.index("--region") + 1] == REGION and "--yes" in a for a in argvs)
    assert all(env == {"AWS_PROFILE": "sso"} for _, env in run.calls)


def test_clean_teardown_keeps_a_failed_status(tmp_path):
    failed = state().model_copy(update={"status": "failed"})
    assert make_teardown("sso", tmp_path, run=FakeRunner())(failed) == {}


def test_leftovers_set_the_status_and_print_masked_arns(tmp_path, capsys):
    run = FakeRunner(fail=lambda argv: "dep-01-ke" in argv, output=lambda argv: LEFTOVER)
    out = make_teardown("sso", tmp_path, run=run)(state())
    assert out["status"] == "leftovers" and "dep-01-ke" in out["errors"][-1]
    printed = capsys.readouterr()
    assert "leftover arn:aws:ec2" in printed.out
    assert not ACCOUNT_ID.search(printed.out + printed.err)
    record = (tmp_path / "runs" / "dep-01" / "commands.jsonl").read_text()
    assert '"exit": 1' in record and ACCOUNT not in record


def test_a_delete_run_that_raises_marks_leftovers_and_tries_every_cell(tmp_path):
    class Raising(FakeRunner):
        def __call__(self, argv, env):
            super().__call__(argv, env)
            raise FileNotFoundError("python")

    run = Raising()
    out = make_teardown("sso", tmp_path, run=run)(state())
    assert out["status"] == "leftovers" and "dep-01-ef" in out["errors"][-1] and "dep-01-ke" in out["errors"][-1]
    assert len(run.calls) == 2


def test_cli_exits_non_zero_when_teardown_leaves_resources(tmp_path, monkeypatch, capsys):
    def runner(argv, env):
        return (1, LEFTOVER) if "scripts/delete_run.py" in argv else (0, "")

    monkeypatch.setattr(deployer, "subprocess_runner", runner)
    monkeypatch.setenv("AWS_PROFILE", "sso")
    monkeypatch.setenv("IMAGE_URI", "repo:tag")
    argv = ["run", "--region", REGION, "--run-id", "td-01", "--profile", "steady"]
    assert cli.main(argv, pricing=StubPricing(), ask=lambda prompt: "y", root=tmp_path) == 1
    out = capsys.readouterr()
    assert "td-01: leftovers" in out.out
    assert not re.search(ACCOUNT_ID, out.out + out.err)
