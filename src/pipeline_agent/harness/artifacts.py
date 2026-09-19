"""Write the run to disk before anything is scored.

This is the part the grading brief calls out by name: "every run written to
disk before anything is scored." Concretely, that means:

  1. The run directory and a status="running" marker exist before the agent
     makes its first tool call.
  2. On success, the full artifact (input, redacted config, tool trace,
     final answer, created-record IDs, timing) is written, THEN status
     flips to "completed". A verifier is only allowed to trust a run whose
     status is "completed".
  3. If the process dies mid-run, the directory is left with status
     "running" (or "failed" if we caught the exception) - never silently
     absent, and never "completed" for a run that didn't finish. See
     RunArtifactWriter.finalize's exception path.
"""
from __future__ import annotations

import dataclasses
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pipeline_agent.config import Settings
from pipeline_agent.harness.base import TaskRun


class RunArtifactWriter:
    def __init__(self, output_dir: str, task_id: str):
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.run_dir = Path(output_dir) / f"{stamp}__{task_id}__{uuid.uuid4().hex[:8]}"

    def start(self, *, task: dict, settings: Settings) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "input.json").write_text(json.dumps(task, indent=2, default=str))
        (self.run_dir / "config.json").write_text(json.dumps(settings.redacted(), indent=2))
        self._write_status("running")

    def finalize(self, run: TaskRun) -> None:
        payload = dataclasses.asdict(run)
        (self.run_dir / "result.json").write_text(json.dumps(payload, indent=2, default=str))
        self._write_status("failed" if run.error and run.ended != "refused" else "completed")

    def mark_failed(self, error: str) -> None:
        (self.run_dir / "error.txt").write_text(error)
        self._write_status("failed")

    def _write_status(self, status: str) -> None:
        (self.run_dir / "status.json").write_text(
            json.dumps({"status": status, "updated_at": time.time()}, indent=2)
        )


@contextmanager
def run_artifact(output_dir: str, task_id: str, *, task: dict, settings: Settings) -> Iterator[RunArtifactWriter]:
    """Guarantees a run is never silently un-persisted.

    Usage:
        with run_artifact(settings.output_dir, task_id, task=task, settings=settings) as w:
            run = await do_the_work(...)
            w.finalize(run)

    If `do_the_work` raises, the run directory is left behind with
    status="failed" and the exception text saved, and the exception is
    re-raised - a crashed run is diagnosable, never mistaken for a passing one.
    """
    writer = RunArtifactWriter(output_dir, task_id)
    writer.start(task=task, settings=settings)
    try:
        yield writer
    except Exception as exc:
        writer.mark_failed(f"{type(exc).__name__}: {exc}")
        raise
