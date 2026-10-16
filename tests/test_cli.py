import re

import pytest

from advisor import cli, deployer
from tests.test_cost import StubPricing

ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@pytest.fixture(autouse=True)
def no_aws(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("AWS call")

    monkeypatch.setattr(cli.boto3, "client", refuse)
    # A yes deploys: every deploy and teardown command goes to a runner that succeeds and does nothing.
    monkeypatch.setattr(deployer, "subprocess_runner", lambda argv, env: (0, ""))
    monkeypatch.setenv("AWS_PROFILE", "test")
    monkeypatch.setenv("IMAGE_URI", "repo:tag")
    monkeypatch.setenv("K6_IMAGE_URI", "repo:k6-tag")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)


def main(argv, tmp_path, answer="n"):
    return cli.main(argv, pricing=StubPricing(), ask=lambda prompt: answer, root=tmp_path)


def test_run_refuses_without_a_region_and_makes_no_aws_call(tmp_path):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["run", "--run-id", "cli-01"], ask=lambda prompt: "y", root=tmp_path)
    assert "no region" in str(exit_.value.code)  # sys.exit(message) exits 1 and prints it
    assert not (tmp_path / "runs").exists()


def test_region_from_env_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert main(["run", "--run-id", "cli-02"], tmp_path) == 0


def test_no_prints_the_estimate_and_stops(tmp_path, capsys):
    argv = ["run", "--cells", "ecs-fargate,eks-ec2", "--profile", "steady", "--region", "us-east-1", "--run-id", "cli-03"]
    assert main(argv, tmp_path) == 0
    out = capsys.readouterr().out
    assert "estimate" in out and "ecs-fargate/steady" in out and "eks-ec2/steady" in out
    assert "step" not in out.replace("steady", "")  # --profile narrows the matrix
    assert "stopped" in out
    assert (tmp_path / "runs" / "cli-03" / "state.sqlite").exists()  # checkpoint kept


def test_auto_approve_under_skips_the_prompt(tmp_path, capsys):
    def never(prompt):
        raise AssertionError("asked")

    argv = ["run", "--region", "us-east-1", "--run-id", "cli-04", "--profile", "steady", "--auto-approve-under", "100"]
    assert cli.main(argv, pricing=StubPricing(), ask=never, root=tmp_path) == 0
    assert "auto" in capsys.readouterr().out


def test_resume_asks_again_when_the_prompt_was_left(tmp_path, capsys):
    def ctrl_c(prompt):
        raise KeyboardInterrupt

    argv = ["run", "--region", "us-east-1", "--run-id", "cli-07", "--profile", "steady"]
    with pytest.raises(KeyboardInterrupt):
        cli.main(argv, pricing=StubPricing(), ask=ctrl_c, root=tmp_path)
    capsys.readouterr()
    assert main(["resume", "cli-07"], tmp_path, answer="y") == 0
    out = capsys.readouterr().out
    assert "estimate" in out and "human: yes" in out


def test_resume_of_an_unknown_run_fails(tmp_path):
    assert main(["resume", "nope"], tmp_path) == 1


def test_unknown_cell_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        main(["run", "--region", "us-east-1", "--cells", "lambda"], tmp_path)


def test_output_has_no_account_id_or_email(tmp_path, capsys):
    argv = ["run", "--region", "us-east-1", "--run-id", "cli-06"]
    main(argv, tmp_path, answer="y")
    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert not ACCOUNT_ID.search(text) and not EMAIL.search(text)


def test_eof_at_the_prompt_is_a_no(tmp_path, capsys):
    def eof(prompt):
        raise EOFError

    argv = ["run", "--region", "us-east-1", "--run-id", "cli-08", "--profile", "steady"]
    assert cli.main(argv, pricing=StubPricing(), ask=eof, root=tmp_path) == 0
    assert "stopped (human: no)" in capsys.readouterr().out


# In files, a UUID tail (LangGraph task ids end in twelve zeros) is not an account id; an ARN's `::<id>:` still is.
FILE_ACCOUNT_ID = re.compile(r"(?<![\w-])\d{12}(?!\w)")


def test_run_folder_files_have_no_account_id_or_email(tmp_path):
    main(["run", "--region", "us-east-1", "--run-id", "cli-09"], tmp_path, answer="y")
    files = [p for p in (tmp_path / "runs").rglob("*") if p.is_file()]
    assert files  # the checkpoint at least
    for path in files:
        text = path.read_bytes().decode("latin-1")
        assert not FILE_ACCOUNT_ID.search(text) and not EMAIL.search(text), path
