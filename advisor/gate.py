"""Approval gate (SPEC section 8): nothing is spent until a yes."""

from collections.abc import Callable

from langgraph.types import interrupt

from advisor.state import RunState


def make_gate(auto_approve_under: float | None = None) -> Callable[[RunState], dict]:
    """Build the gate node. It pauses the graph for a y/N answer, unless the estimate is
    complete, error-free and below `auto_approve_under` USD."""

    def gate(state: RunState) -> dict:
        usd = state.estimate_usd
        if (
            auto_approve_under is not None
            and usd is not None
            and usd < auto_approve_under
            and not state.gaps
            and not state.errors
        ):
            note = f"auto: ${usd:.2f} under --auto-approve-under {auto_approve_under:.2f}"
            return {"approved": True, "status": "approved", "approval": note}
        answer = interrupt({"estimate_usd": usd, "gaps": state.gaps, "errors": state.errors})
        if str(answer).strip().lower() in ("y", "yes"):
            return {"approved": True, "status": "approved", "approval": "human: yes"}
        return {"approved": False, "status": "stopped", "approval": "human: no"}

    return gate
