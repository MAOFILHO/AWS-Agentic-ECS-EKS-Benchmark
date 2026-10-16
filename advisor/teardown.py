"""Teardown node (SPEC section 10): the last node on every path after the gate's yes.

Calls delete_run.py once per cell run id. A non-zero exit means stacks or tagged resources are
left: the run status becomes "leftovers" and delete_run.py's leftover ARNs print masked.
"""

import sys
from collections.abc import Callable
from pathlib import Path

from advisor import deployer as d
from advisor.state import RunState


def make_teardown(profile: str | None, root: Path, run: d.Runner | None = None) -> Callable[[RunState], dict]:
    def teardown(state: RunState) -> dict:
        runner = run or d.subprocess_runner
        env = {"AWS_PROFILE": profile} if profile else {}
        left = []
        for cell in d.cells_of(state):
            rid = d.cell_run_id(state.run_id, cell)
            argv = [sys.executable, "scripts/delete_run.py", "--run-id", rid, "--region", state.region, "--yes"]
            code, _ = d.run_logged(runner, root, state.run_id, argv, env)
            if code:
                left.append(rid)
        if left:
            why = f"teardown: delete_run.py failed for {', '.join(left)}; check the leftovers above"
            print(why, file=sys.stderr)
            return {"status": "leftovers", "errors": [*state.errors, why]}
        # A failed or interrupted run keeps that status after a clean teardown.
        return {} if state.status in ("failed", "interrupted") else {"status": "done"}

    return teardown
