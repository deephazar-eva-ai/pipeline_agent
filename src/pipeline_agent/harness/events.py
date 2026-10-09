"""Optional, low-detail live events for external test coordinators.

The run artifact remains the authoritative, complete record. This stream is
only for a local helper that must react while a run is in progress (for
example, a concurrent-writer integration test). It deliberately contains no
tool arguments, results, prompts, or credentials.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class HarnessEventSink:
    """Append compact JSONL lifecycle events when ``PIPELINE_EVENT_LOG`` is set."""

    def __init__(self, path: Path | None, *, task_id: str, run_id: str):
        self.path = path
        self.task_id = task_id
        self.run_id = run_id

    @classmethod
    def from_env(cls, task: dict[str, Any]) -> "HarnessEventSink":
        raw_path = os.environ.get("PIPELINE_EVENT_LOG", "").strip()
        path = Path(raw_path) if raw_path else None
        return cls(path, task_id=str(task.get("id", "")), run_id=str(task.get("run_id", "")))

    def emit(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "event": event,
            "timestamp": time.time(),
            "task_id": self.task_id,
            "run_id": self.run_id,
            **fields,
        }
        line = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
        # One append syscall per small event keeps separate writer processes
        # from interleaving a JSON record on normal local filesystems.
        fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
