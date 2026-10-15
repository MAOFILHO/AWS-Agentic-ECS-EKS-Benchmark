"""Run folder layout and the LangGraph SQLite checkpointer."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from advisor.state import RUN_ID_PATTERN


@dataclass(frozen=True)
class RunPaths:
    raw: Path
    metrics: Path
    report: Path
    checkpoint: Path


def create_run_dirs(run_id: str, root: Path) -> RunPaths:
    if not re.fullmatch(RUN_ID_PATTERN, run_id):
        raise ValueError(f"run_id must match {RUN_ID_PATTERN}: {run_id!r}")
    results = root / "results" / run_id
    paths = RunPaths(
        raw=results / "raw",
        metrics=results / "metrics.json",
        report=results / "report.md",
        checkpoint=root / "runs" / run_id / "state.sqlite",
    )
    paths.raw.mkdir(parents=True, exist_ok=True)
    paths.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    return paths


@contextmanager
def open_checkpointer(paths: RunPaths) -> Iterator[SqliteSaver]:
    # check_same_thread=False is safe: SqliteSaver serialises access with its own lock.
    conn = sqlite3.connect(paths.checkpoint, check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        yield saver
    finally:
        conn.close()
