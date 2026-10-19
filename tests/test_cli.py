import re

import pytest

from advisor import cli, deployer
from advisor.planner import PROFILES
from tests.test_cost import StubPricing

ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class FakeBedrock:
    def converse(self, **kwargs):
        return {"output": {"message": {"content": [{"text": "## Verdict\nNo figures yet.\n"}]}}}


@pytest.fixture(autouse=True)
def no_aws(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("AWS call")

    def client(service, **kwargs):  # the reporter gets a fake Bedrock; every other client is refused
        return FakeBedrock() if service == "bedrock-runtime" else refuse()

    monkeypatch.setattr(cli.boto3, "client", client)
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


def test_profile_takes_a_list_in_the_order_typed(tmp_path, capsys):
    argv = ["run", "--cells", "ecs-fargate,eks-ec2", "--profile", "spike,scale-from-min", "--region", "us-east-1", "--run-id", "cli-10"]
    assert main(argv, tmp_path) == 0
    matrix = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("matrix:"))
    assert matrix == "matrix: ecs-fargate/spike, ecs-fargate/scale-from-min, eks-ec2/spike, eks-ec2/scale-from-min"


def test_unknown_profile_lists_the_valid_names_and_makes_no_aws_call(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["run", "--region", "us-east-1", "--profile", "spike,burst"], tmp_path)
    assert exit_.value.code != 0
    err = capsys.readouterr().err
    assert "burst" in err and all(p in err for p in PROFILES)
    assert not (tmp_path / "runs").exists()


def test_repeated_profile_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["run", "--region", "us-east-1", "--profile", "spike,spike"], tmp_path)
    assert exit_.value.code != 0
    assert "spike" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_one_profile_and_no_flag_behave_as_before(tmp_path, capsys):
    main(["run", "--cells", "ecs-fargate", "--profile", "steady", "--region", "us-east-1", "--run-id", "cli-11"], tmp_path)
    assert "matrix: ecs-fargate/steady\n" in capsys.readouterr().out
    main(["run", "--cells", "ecs-fargate", "--region", "us-east-1", "--run-id", "cli-12"], tmp_path)
    matrix = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("matrix:"))
    assert matrix == "matrix: " + ", ".join(f"ecs-fargate/{p}" for p in PROFILES)


def test_four_cell_two_profile_estimate_prints_before_the_prompt(tmp_path, capsys):
    # Issue #153: the full run adds eks-fargate; its priced lines must be on screen when the y/N is asked.
    seen = []

    def ask(prompt):
        seen.append(capsys.readouterr().out)
        return "n"

    argv = ["run", "--cells", "ecs-fargate,ecs-ec2,eks-ec2,eks-fargate", "--profile", "spike,scale-from-min",
            "--region", "us-east-1", "--run-id", "cli-4c"]
    assert cli.main(argv, pricing=StubPricing(), ask=ask, root=tmp_path) == 0
    (before,) = seen
    for cell in ("ecs-fargate", "ecs-ec2", "eks-ec2", "eks-fargate"):
        for profile in ("spike", "scale-from-min"):
            assert f"{cell}/{profile}" in before
    assert "eks-fargate/spike eks control plane: $" in before
    assert "eks-fargate/scale-from-min fargate vcpu: $" in before
    assert "shared nat gateway: $" in before and "shared vpc endpoints: $" in before
    assert re.search(r"^estimate: \$\d+\.\d\d$", before, re.MULTILINE)
    assert "stopped" in capsys.readouterr().out
