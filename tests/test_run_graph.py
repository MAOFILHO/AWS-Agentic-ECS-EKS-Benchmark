"""The run graph past the collector: analyst and reporter wired in by cli.deploy_nodes (issue #109). No AWS."""

import shutil
from pathlib import Path

import pytest

from advisor import cli, deployer
from advisor.graph import build_graph
from advisor.schemas import RunConfig
from advisor.state import Capacity, RunState
from tests.test_analyst import REAL, REAL_CELLS

CONFIG = RunConfig.model_validate_json(cli.RUN_CONFIG.read_text()).model_dump()
ACCOUNT = "1234" * 3  # built at runtime: tracked files must hold no account id
REPORT = "## Verdict\nECS is simpler to run.\n\n### spike\n\n### steady\n"  # one section per profile (#134)


class Bedrock:
    def __init__(self, error=None):
        self.error, self.regions = error, []

    def converse(self, **kwargs):
        if self.error:
            raise self.error
        return {"output": {"message": {"content": [{"text": REPORT}]}}}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "test")
    monkeypatch.setenv("IMAGE_URI", "repo:tag")
    monkeypatch.setenv("K6_IMAGE_URI", "repo:k6-tag")
    deletes = []

    def runner(argv, env):
        if any(a.endswith("delete_run.py") for a in argv):
            deletes.append(argv)
        return 0, ""

    monkeypatch.setattr(deployer, "subprocess_runner", runner)
    return deletes


def bedrock(monkeypatch, fake):
    def client(service, region_name=None):
        assert service == "bedrock-runtime", f"AWS call: {service}"
        fake.regions.append(region_name)
        return fake

    monkeypatch.setattr(cli.boto3, "client", client)


def run_graph(tmp_path, run_id="graph-01"):
    """planner and gate say yes; deployer, load runner and collector are fakes; the collector copies real raw files."""

    def collector(state):
        shutil.copytree(REAL, tmp_path / "results" / state.run_id / "raw", dirs_exist_ok=True)
        return {"status": "collected"}

    nodes = cli.deploy_nodes(tmp_path, CONFIG)
    nodes |= {"deployer": lambda s: {"status": "deployed"}, "load_runner": lambda s: {"status": "loaded"},
              "collector": collector}
    graph = build_graph(lambda s: {}, lambda s: {"approved": True}, **nodes)
    state = RunState(run_id=run_id, cells=REAL_CELLS, capacity=Capacity(**CONFIG["capacity"]), region="eu-west-1")
    return graph.invoke(state)


def test_deploy_nodes_has_analyst_and_reporter(env):
    nodes = cli.deploy_nodes(Path("."), CONFIG)
    assert set(nodes) == {"deployer", "load_runner", "collector", "analyst", "reporter", "teardown"}


def test_deploy_nodes_makes_no_bedrock_client(env, monkeypatch):
    monkeypatch.setattr(cli.boto3, "client", lambda *a, **k: pytest.fail("AWS call"))
    cli.deploy_nodes(Path("."), CONFIG)


def test_run_past_the_collector_writes_metrics_and_report(env, tmp_path, monkeypatch):
    fake = Bedrock()
    bedrock(monkeypatch, fake)
    result = run_graph(tmp_path)
    results = tmp_path / "results" / "graph-01"
    assert '"figures"' in (results / "metrics.json").read_text()
    assert (results / "report.md").read_text() == REPORT
    assert fake.regions == ["eu-west-1"]  # the run's region
    assert result["status"] == "done" and env  # teardown ran


@pytest.mark.parametrize("error", [RuntimeError(f"AccessDeniedException for arn:aws:iam::{ACCOUNT}:role/x"), None])
def test_failed_reporter_still_ends_in_teardown(env, tmp_path, monkeypatch, error):
    fake = Bedrock(error)
    if error is None:  # a draft with a number not in metrics.json, three times
        fake.converse = lambda **kwargs: {"output": {"message": {"content": [{"text": "p95 is 9999 ms"}]}}}
    bedrock(monkeypatch, fake)
    result = run_graph(tmp_path)
    assert result["status"] == "report_failed"
    assert [a[a.index("--run-id") + 1] for a in env] == ["graph-01-ef", "graph-01-ke"]
    assert not (tmp_path / "results" / "graph-01" / "report.md").exists()
    assert result["errors"][-1].startswith("reporter:")
    assert ACCOUNT not in result["errors"][-1]


def test_resume_builds_the_same_nodes_as_run(env, tmp_path, monkeypatch):
    from tests.test_cost import StubPricing

    seen = []
    real = cli.build_graph

    def spy(planner, gate, checkpointer=None, **nodes):
        seen.append(sorted(nodes))
        return real(planner, gate, checkpointer, **nodes)

    monkeypatch.setattr(cli, "build_graph", spy)
    argv = ["run", "--region", "us-east-1", "--run-id", "graph-02", "--profile", "steady"]
    assert cli.main(argv, pricing=StubPricing(), ask=lambda p: "n", root=tmp_path) == 0
    assert cli.main(["resume", "graph-02"], ask=lambda p: "n", root=tmp_path) == 0
    assert len(seen) == 2 and seen[0] == seen[1]
    assert {"analyst", "reporter"} <= set(seen[0])
