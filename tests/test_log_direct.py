"""Direct unit tests for harness/log.py — TaskLogger methods."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.log import TaskLogger


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_events(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _make_logger(tmp_path: Path, task: str = "t1") -> tuple[TaskLogger, Path]:
    run_id = "run-log-test"
    import harness.log as log_mod
    old_dir = log_mod.RESULTS_DIR
    log_mod.RESULTS_DIR = tmp_path
    try:
        logger = TaskLogger(run_id, task)
    finally:
        log_mod.RESULTS_DIR = old_dir
    log_path = tmp_path / run_id / "tasks" / f"{task}.jsonl"
    return logger, log_path


# ── turn_start ────────────────────────────────────────────────────────────────

def test_turn_start(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.turn_start(3)
    events = _read_events(path)
    assert events[0]["event"] == "turn_start"
    assert events[0]["turn"] == 3


# ── llm_request ───────────────────────────────────────────────────────────────

def test_llm_request(tmp_path):
    logger, path = _make_logger(tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    with logger:
        logger.llm_request(messages, "openai/gpt-4o-mini", tools)
    events = _read_events(path)
    ev = events[0]
    assert ev["event"] == "llm_request"
    assert ev["model"] == "openai/gpt-4o-mini"
    assert ev["tool_count"] == 1
    assert ev["message_count"] == 1
    assert ev["messages"] == messages


# ── llm_response ──────────────────────────────────────────────────────────────

def test_llm_response(tmp_path):
    logger, path = _make_logger(tmp_path)
    tcs = [{"id": "c1", "function": {"name": "read_file"}}]
    with logger:
        logger.llm_response(
            content="Here is the result.",
            tool_calls=tcs,
            prompt_tokens=20,
            completion_tokens=8,
            latency_ms=123.4,
        )
    ev = _read_events(path)[0]
    assert ev["event"] == "llm_response"
    assert ev["content"] == "Here is the result."
    assert ev["prompt_tokens"] == 20
    assert ev["completion_tokens"] == 8
    assert ev["latency_ms"] == 123.4
    assert ev["tool_calls"] == tcs


def test_llm_response_none_content(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.llm_response(None, [], 5, 0, 50.0)
    ev = _read_events(path)[0]
    assert ev["content"] is None


# ── tool_call / tool_result ───────────────────────────────────────────────────

def test_tool_call_and_result(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.tool_call("call-1", "read_file", {"path": "billing.py"})
        logger.tool_result("call-1", "read_file", "def foo(): pass", error=False)
    events = _read_events(path)
    tc, tr = events[0], events[1]
    assert tc["event"] == "tool_call"
    assert tc["call_id"] == "call-1"
    assert tc["name"] == "read_file"
    assert tc["arguments"] == {"path": "billing.py"}
    assert tr["event"] == "tool_result"
    assert tr["result"] == "def foo(): pass"
    assert tr["error"] is False


def test_tool_result_error_flag(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.tool_result("c2", "run_in_terminal", "[error] blocked", error=True)
    ev = _read_events(path)[0]
    assert ev["error"] is True


# ── workspace_snapshot ────────────────────────────────────────────────────────

def test_workspace_snapshot(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.workspace_snapshot("pre-task", "")
        logger.workspace_snapshot("post-task", "diff --git a/foo.py ...")
    events = _read_events(path)
    assert events[0]["label"] == "pre-task"
    assert events[0]["diff"] == ""
    assert events[1]["label"] == "post-task"
    assert "diff" in events[1]["diff"]


# ── workspace_setup ───────────────────────────────────────────────────────────

def test_workspace_setup(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.workspace_setup("echo hello > file.txt", 0, "hello", "")
    ev = _read_events(path)[0]
    assert ev["event"] == "workspace_setup"
    assert ev["cmd"] == "echo hello > file.txt"
    assert ev["rc"] == 0
    assert ev["stdout"] == "hello"
    assert ev["stderr"] == ""


# ── score ─────────────────────────────────────────────────────────────────────

def test_score(tmp_path):
    logger, path = _make_logger(tmp_path)
    dims = {"format_valid": True, "tier_correct": False, "blocker_correct": None}
    with logger:
        logger.score(dims, total=1, max_score=3)
    ev = _read_events(path)[0]
    assert ev["event"] == "score"
    assert ev["total"] == 1
    assert ev["max_score"] == 3
    assert ev["pct"] == pytest.approx(33.3, abs=0.1)
    assert ev["dimensions"] == dims


def test_score_zero_max(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.score({}, total=0, max_score=0)
    ev = _read_events(path)[0]
    assert ev["pct"] == 0


# ── retry_start ───────────────────────────────────────────────────────────────

def test_retry_start(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.retry_start(attempt=2)
    ev = _read_events(path)[0]
    assert ev["event"] == "retry_start"
    assert ev["attempt"] == 2


# ── error ─────────────────────────────────────────────────────────────────────

def test_error_with_exception(tmp_path):
    logger, path = _make_logger(tmp_path)
    try:
        raise ValueError("something broke")
    except ValueError as exc:
        with logger:
            logger.error("unexpected failure", exc)
    ev = _read_events(path)[0]
    assert ev["event"] == "error"
    assert "unexpected failure" in ev["message"]
    assert "ValueError" in ev["exc"]


def test_error_without_exception(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.error("plain error message")
    ev = _read_events(path)[0]
    assert ev["exc"] is None


# ── context manager + multiple events ────────────────────────────────────────

def test_context_manager_closes_file(tmp_path):
    """Logger used as context manager should flush and close cleanly."""
    import harness.log as log_mod
    old_dir = log_mod.RESULTS_DIR
    log_mod.RESULTS_DIR = tmp_path
    run_id = "run-cm-test"
    try:
        with TaskLogger(run_id, "task-a") as logger:
            logger.turn_start(1)
            logger.turn_start(2)
    finally:
        log_mod.RESULTS_DIR = old_dir
    log_path = tmp_path / run_id / "tasks" / "task-a.jsonl"
    events = _read_events(log_path)
    assert len(events) == 2


def test_events_have_timestamp(tmp_path):
    logger, path = _make_logger(tmp_path)
    with logger:
        logger.turn_start(1)
    ev = _read_events(path)[0]
    assert "ts" in ev
    # Should be ISO format with T separator
    assert "T" in ev["ts"]
