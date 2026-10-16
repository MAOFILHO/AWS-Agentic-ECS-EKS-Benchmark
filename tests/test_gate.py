from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from advisor.gate import make_gate
from advisor.graph import build_graph
from advisor.state import Capacity, CellSpec, RunState

CAPACITY = Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4)
STATE = RunState(run_id="gate-01", cells=[], capacity=CAPACITY, region="us-east-1")
CFG = {"configurable": {"thread_id": "gate-01"}}


def planner_with(estimate_usd=1.0, gaps=()):
    def planner(state):
        cells = [CellSpec(name="ecs-fargate", profile="steady")]
        return {"cells": cells, "estimate_usd": estimate_usd, "gaps": list(gaps)}

    return planner


class Recorder:
    """Stub node that records each call and returns a fixed update."""

    def __init__(self, update=None):
        self.calls = 0
        self.update = update or {}

    def __call__(self, state):
        self.calls += 1
        return self.update


def run(gate, planner=None, answer=None, **nodes):
    graph = build_graph(planner or planner_with(), gate, checkpointer=InMemorySaver(), **nodes)
    out = graph.invoke(STATE, CFG)
    if answer is not None:
        assert "__interrupt__" in out
        out = graph.invoke(Command(resume=answer), CFG)
    return out


def test_no_ends_the_run_with_zero_deploy_calls():
    deployer, teardown = Recorder(), Recorder()
    out = run(make_gate(), answer="n", deployer=deployer, teardown=teardown)
    assert deployer.calls == 0 and teardown.calls == 0
    assert out["status"] == "stopped" and out["approved"] is False


def test_yes_deploys_then_tears_down():
    deployer, teardown = Recorder(), Recorder()
    out = run(make_gate(), answer="y", deployer=deployer, teardown=teardown)
    assert deployer.calls == 1 and teardown.calls == 1
    assert out["approved"] is True and out["approval"] == "human: yes"


def test_auto_approve_below_the_limit_and_records_it():
    deployer = Recorder()
    out = run(make_gate(auto_approve_under=3.0), planner=planner_with(2.99), deployer=deployer)
    assert "__interrupt__" not in out and deployer.calls == 1
    assert out["approved"] is True and "auto" in out["approval"]


def test_auto_approve_not_at_or_above_the_limit():
    for usd in (3.0, 3.01):
        deployer = Recorder()
        out = run(make_gate(auto_approve_under=3.0), planner=planner_with(usd), deployer=deployer)
        assert "__interrupt__" in out and deployer.calls == 0


def test_gap_blocks_auto_approval():
    deployer = Recorder()
    out = run(make_gate(auto_approve_under=3.0), planner=planner_with(0.5, ["nat_hour"]), deployer=deployer)
    assert "__interrupt__" in out and deployer.calls == 0


def test_stub_failure_edge_reaches_teardown():
    failed = Recorder({"status": "failed", "errors": ["deploy failed"]})
    load_runner, teardown = Recorder(), Recorder()
    out = run(make_gate(), answer="y", deployer=failed, load_runner=load_runner, teardown=teardown)
    assert load_runner.calls == 0 and teardown.calls == 1
    assert out["status"] == "failed"


def test_every_step_after_the_gate_fails_to_teardown():
    for step in ("deployer", "load_runner", "collector", "analyst", "reporter"):
        teardown = Recorder()
        nodes = {step: Recorder({"status": "failed"}), "teardown": teardown}
        run(make_gate(), answer="y", **nodes)
        assert teardown.calls == 1, step


def planner_with_errors(estimate_usd=0.5, errors=("pricing: throttled",)):
    def planner(state):
        cells = [CellSpec(name="ecs-fargate", profile="steady")]
        return {"cells": cells, "estimate_usd": estimate_usd, "errors": list(errors)}

    return planner


def test_errors_block_auto_approval():
    deployer = Recorder()
    out = run(make_gate(auto_approve_under=3.0), planner=planner_with_errors(), deployer=deployer)
    assert "__interrupt__" in out and deployer.calls == 0


def test_unknown_estimate_blocks_auto_approval():
    deployer = Recorder()
    out = run(make_gate(auto_approve_under=3.0), planner=planner_with(None), deployer=deployer)
    assert "__interrupt__" in out and deployer.calls == 0


def test_blank_or_unclear_answer_is_a_no():
    for answer in ("", " ", "N", "no", "maybe", "yess"):
        deployer, teardown = Recorder(), Recorder()
        out = run(make_gate(), answer=answer, deployer=deployer, teardown=teardown)
        assert deployer.calls == 0 and teardown.calls == 0, answer
        assert out["status"] == "stopped" and out["approval"] == "human: no", answer


def test_yes_is_case_and_space_insensitive():
    for answer in ("Y", " yes ", "YES"):
        deployer = Recorder()
        out = run(make_gate(), answer=answer, deployer=deployer)
        assert deployer.calls == 1 and out["approval"] == "human: yes", answer
