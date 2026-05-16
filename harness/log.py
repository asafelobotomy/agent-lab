"""harness/log.py — structured event logger for a benchmark run.

Every turn in the agentic loop, every tool call, and every tool result
is written as a JSONL event to results/<run-id>/tasks/<task-id>.jsonl
so EvalJudge (and humans) can replay exactly what happened.

Event types:
  turn_start         — beginning of a conversation turn
  llm_request        — full message list sent to the LLM
  llm_response       — full response received (text + tool_calls if any)
  tool_call          — single tool invocation (name + input)
  tool_result        — result returned to the LLM
  workspace_snapshot — git diff captured at a specific point
  workspace_setup    — output from a per-task setup command
  score              — dimension scores for this task
  error              — unexpected failure during a turn
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


class TaskLogger:
    """Append-only JSONL logger scoped to a single task within a run."""

    def __init__(self, run_id: str, task_name: str) -> None:
        self._run_id = run_id
        self._task_name = task_name
        task_dir = RESULTS_DIR / run_id / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        self._path = task_dir / f"{task_name}.jsonl"
        self._fh = self._path.open("a", encoding="utf-8")

    def _write(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **payload,
        }
        self._fh.write(json.dumps(record) + "\n")
        self._fh.flush()

    def turn_start(self, turn: int) -> None:
        self._write("turn_start", {"turn": turn})

    def llm_request(self, messages: list[dict], model: str, tools: list[dict]) -> None:
        self._write("llm_request", {
            "model": model,
            "message_count": len(messages),
            "tool_count": len(tools),
            "messages": messages,
        })

    def llm_response(self, content: str | None, tool_calls: list[dict],
                     prompt_tokens: int, completion_tokens: int, latency_ms: float) -> None:
        self._write("llm_response", {
            "content": content,
            "tool_calls": tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
        })

    def tool_call(self, call_id: str, name: str, arguments: dict) -> None:
        self._write("tool_call", {
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
        })

    def tool_result(self, call_id: str, name: str, result: str, error: bool = False) -> None:
        self._write("tool_result", {
            "call_id": call_id,
            "name": name,
            "result": result,
            "error": error,
        })

    def workspace_snapshot(self, label: str, diff: str) -> None:
        self._write("workspace_snapshot", {"label": label, "diff": diff})

    def workspace_setup(self, cmd: str, rc: int, stdout: str, stderr: str) -> None:
        """Record the outcome of a per-task setup command."""
        self._write("workspace_setup", {
            "cmd": cmd,
            "rc": rc,
            "stdout": stdout,
            "stderr": stderr,
        })

    def score(self, dimensions: dict[str, bool | None], total: int, max_score: int) -> None:
        self._write("score", {
            "dimensions": dimensions,
            "total": total,
            "max_score": max_score,
            "pct": round(100 * total / max_score, 1) if max_score else 0,
        })

    def retry_start(self, attempt: int = 1) -> None:
        """Mark the start of a retry attempt within the same log file."""
        self._write("retry_start", {"attempt": attempt})

    def error(self, message: str, exc: BaseException | None = None) -> None:
        self._write("error", {
            "message": message,
            "exc": repr(exc) if exc else None,
        })
        print(f"  [error] {message}", file=sys.stderr)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "TaskLogger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
