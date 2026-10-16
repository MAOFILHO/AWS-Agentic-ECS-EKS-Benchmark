"""The advisor graph (docs/architecture.md, agent graph). Nodes later cards add are stubs here."""

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from advisor.state import RunState

# The steps after the gate, in order. A step that sets status "failed" goes straight to teardown.
STEPS = ("deployer", "load_runner", "collector", "analyst", "reporter")


def stub(state: RunState) -> dict:
    return {}


def build_graph(planner: Callable, gate: Callable, checkpointer: Any = None, **nodes: Callable):
    """planner -> gate; a no ends the run; a yes runs STEPS, and every path then ends in teardown."""
    graph = StateGraph(RunState)
    graph.add_node("planner", planner)
    graph.add_node("gate", gate)
    for name in (*STEPS, "teardown"):
        graph.add_node(name, nodes.pop(name, stub))
    if nodes:
        raise ValueError(f"unknown nodes: {sorted(nodes)}")

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "gate")
    graph.add_conditional_edges("gate", lambda s: STEPS[0] if s.approved else END, [STEPS[0], END])
    for step, nxt in zip(STEPS, (*STEPS[1:], "teardown")):
        route = lambda s, nxt=nxt: "teardown" if s.status == "failed" else nxt
        graph.add_conditional_edges(step, route, list(dict.fromkeys([nxt, "teardown"])))
    graph.add_edge("teardown", END)
    return graph.compile(checkpointer=checkpointer)
