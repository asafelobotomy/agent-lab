"""Tests for harness/invoke.py — agentic LLM loop."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from harness.invoke import _parse_tool_calls, run_task


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_workspace(diff_output: str = "") -> MagicMock:
    ws = MagicMock()
    ws.diff.return_value = diff_output
    return ws


def _make_logger() -> MagicMock:
    logger = MagicMock()
    return logger


def _text_resp(content: str, pt: int = 10, ct: int = 5) -> dict:
    return {
        "choices": [{"message": {"content": content, "tool_calls": None}}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct},
        "_latency_ms": 50.0,
    }


def _tool_resp(tool_calls: list[dict], pt: int = 10, ct: int = 5) -> dict:
    return {
        "choices": [{"message": {"content": "", "tool_calls": tool_calls}}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct},
        "_latency_ms": 60.0,
    }


def _make_tc(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


# ── _parse_tool_calls ─────────────────────────────────────────────────────────

def test_parse_tool_calls_with_calls():
    tcs = [{"id": "c1", "function": {"name": "read_file"}}]
    choice = {"message": {"content": None, "tool_calls": tcs}}
    result = _parse_tool_calls(choice)
    assert result == tcs


def test_parse_tool_calls_no_calls():
    choice = {"message": {"content": "hello", "tool_calls": []}}
    assert _parse_tool_calls(choice) == []


def test_parse_tool_calls_none():
    choice = {"message": {"content": "hi"}}  # no tool_calls key
    assert _parse_tool_calls(choice) == []


def test_parse_tool_calls_empty_choice():
    assert _parse_tool_calls({}) == []


# ── _llm_call (no GITHUB_TOKEN) ───────────────────────────────────────────────

def test_llm_call_no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from harness.invoke import _llm_call
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN not set"):
        _llm_call([], "model", [])


def test_llm_call_with_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok-test")
    fake_resp = {
        "choices": [{"message": {"content": "hi", "tool_calls": []}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }

    class _MockResp:
        def read(self):
            return json.dumps(fake_resp).encode()
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    from harness.invoke import _llm_call
    with patch("harness.invoke.time.sleep"), \
         patch("harness.invoke._last_call", 0.0, create=True), \
         patch("harness.invoke.urllib.request.urlopen", return_value=_MockResp()):
        data = _llm_call([{"role": "user", "content": "hi"}], "openai/gpt-4o-mini", [])
    assert data["choices"][0]["message"]["content"] == "hi"
    assert "_latency_ms" in data


def test_llm_call_with_tools(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok-test")
    fake_resp = {
        "choices": [{"message": {"content": "ok", "tool_calls": []}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    }

    class _MockResp:
        def read(self): return json.dumps(fake_resp).encode()
        def __enter__(self): return self
        def __exit__(self, *_): pass

    from harness.invoke import _llm_call
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    with patch("harness.invoke.time.sleep"), \
         patch("harness.invoke.urllib.request.urlopen", return_value=_MockResp()):
        data = _llm_call([], "model", tools)
    assert data["choices"][0]["message"]["content"] == "ok"


# ── _throttle ─────────────────────────────────────────────────────────────────

def test_throttle_sleeps_when_recent():
    import harness.invoke as m
    import time
    old_last = m._last_call
    try:
        m._last_call = time.monotonic()  # pretend we just called
        with patch("harness.invoke.time.sleep") as mock_sleep:
            m._throttle()
        mock_sleep.assert_called_once()
        slept = mock_sleep.call_args[0][0]
        assert 0 < slept <= m._MIN_CALL_INTERVAL
    finally:
        m._last_call = old_last


def test_throttle_no_sleep_when_old():
    import harness.invoke as m
    old_last = m._last_call
    try:
        m._last_call = 0.0  # very old timestamp
        with patch("harness.invoke.time.sleep") as mock_sleep:
            m._throttle()
        mock_sleep.assert_not_called()
    finally:
        m._last_call = old_last


# ── run_task: single-turn text response ───────────────────────────────────────

def test_run_task_single_turn_text():
    ws = _make_workspace()
    logger = _make_logger()
    task = {"prompt": "What is 2+2?"}

    responses = [_text_resp("The answer is 4.")]

    with patch("harness.invoke._llm_call", side_effect=responses), \
         patch("harness.invoke.tools_registry.build_tool_list", return_value=[]), \
         patch("harness.invoke.tools_registry.dispatch"):
        result = run_task(
            agent_system_prompt="You are helpful.",
            task=task,
            workspace=ws,
            model="openai/gpt-4o-mini",
            agent_tool_surfaces=[],
            logger=logger,
        )

    assert result["final_response"] == "The answer is 4."
    assert result["turn_count"] == 1
    assert result["tool_calls_made"] == []
    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 5


# ── run_task: tool call followed by text response ─────────────────────────────

def test_run_task_one_tool_then_text():
    ws = _make_workspace()
    logger = _make_logger()
    task = {"prompt": "Read billing.py and summarise it."}

    tc = _make_tc("c-1", "read_file", {"filePath": "/workspace/billing.py"})
    responses = [
        _tool_resp([tc]),
        _text_resp("The file defines calculate_total."),
    ]

    with patch("harness.invoke._llm_call", side_effect=responses), \
         patch("harness.invoke.tools_registry.build_tool_list", return_value=[{"type": "function"}]), \
         patch("harness.invoke.tools_registry.dispatch", return_value="def calculate_total(): ..."):
        result = run_task(
            agent_system_prompt="You are a code analyst.",
            task=task,
            workspace=ws,
            model="openai/gpt-4o-mini",
            agent_tool_surfaces=["codebase"],
            logger=logger,
        )

    assert result["final_response"] == "The file defines calculate_total."
    assert result["turn_count"] == 2
    assert len(result["tool_calls_made"]) == 1
    tc_record = result["tool_calls_made"][0]
    assert tc_record["name"] == "read_file"
    assert tc_record["result"] == "def calculate_total(): ..."


# ── run_task: LLM error path ──────────────────────────────────────────────────

def test_run_task_llm_error_breaks_loop():
    ws = _make_workspace()
    logger = _make_logger()
    task = {"prompt": "Do something."}

    with patch("harness.invoke._llm_call", side_effect=RuntimeError("network failure")), \
         patch("harness.invoke.tools_registry.build_tool_list", return_value=[]):
        result = run_task(
            agent_system_prompt="sys",
            task=task,
            workspace=ws,
            model="openai/gpt-4o-mini",
            agent_tool_surfaces=[],
            logger=logger,
        )

    # Should return an empty final_response and turn_count=1
    assert result["final_response"] == ""
    assert result["turn_count"] == 1
    logger.error.assert_called_once()


# ── run_task: tool dispatch error ─────────────────────────────────────────────

def test_run_task_tool_dispatch_error_is_recorded():
    ws = _make_workspace()
    logger = _make_logger()
    task = {"prompt": "Try a tool."}

    tc = _make_tc("e-1", "bad_tool", {})
    responses = [_tool_resp([tc]), _text_resp("Done.")]

    with patch("harness.invoke._llm_call", side_effect=responses), \
         patch("harness.invoke.tools_registry.build_tool_list", return_value=[]), \
         patch("harness.invoke.tools_registry.dispatch", return_value="[error] unknown tool: bad_tool"):
        result = run_task(
            agent_system_prompt="sys",
            task=task,
            workspace=ws,
            model="openai/gpt-4o-mini",
            agent_tool_surfaces=[],
            logger=logger,
        )

    assert result["tool_calls_made"][0]["result"] == "[error] unknown tool: bad_tool"
    # tool_result logged with error=True
    logger.tool_result.assert_called_with("e-1", "bad_tool", "[error] unknown tool: bad_tool", error=True)


# ── run_task: badly formed JSON in tool args ──────────────────────────────────

def test_run_task_bad_json_args():
    ws = _make_workspace()
    logger = _make_logger()
    task = {"prompt": "Do stuff."}

    tc = {"id": "b-1", "function": {"name": "read_file", "arguments": "not-json"}}
    responses = [_tool_resp([tc]), _text_resp("OK.")]

    with patch("harness.invoke._llm_call", side_effect=responses), \
         patch("harness.invoke.tools_registry.build_tool_list", return_value=[]), \
         patch("harness.invoke.tools_registry.dispatch", return_value="content"):
        result = run_task(
            agent_system_prompt="sys",
            task=task,
            workspace=ws,
            model="openai/gpt-4o-mini",
            agent_tool_surfaces=[],
            logger=logger,
        )

    # args should degrade to empty dict without crashing
    assert result["tool_calls_made"][0]["args"] == {}


# ── run_task: workspace diff captured ────────────────────────────────────────

def test_run_task_workspace_diff_captured():
    ws = _make_workspace(diff_output="+print('hello')")
    logger = _make_logger()
    task = {"prompt": "Make a change."}

    responses = [_text_resp("Done.")]

    with patch("harness.invoke._llm_call", side_effect=responses), \
         patch("harness.invoke.tools_registry.build_tool_list", return_value=[]):
        result = run_task(
            agent_system_prompt="sys",
            task=task,
            workspace=ws,
            model="openai/gpt-4o-mini",
            agent_tool_surfaces=[],
            logger=logger,
        )

    assert result["workspace_diff"] == "+print('hello')"


# ── run_task: token accumulation over turns ───────────────────────────────────

def test_run_task_token_accumulation():
    ws = _make_workspace()
    logger = _make_logger()
    task = {"prompt": "Multi-turn task."}

    tc = _make_tc("t1", "read_file", {})
    responses = [
        _tool_resp([tc], pt=20, ct=10),
        _text_resp("Result.", pt=30, ct=15),
    ]

    with patch("harness.invoke._llm_call", side_effect=responses), \
         patch("harness.invoke.tools_registry.build_tool_list", return_value=[]), \
         patch("harness.invoke.tools_registry.dispatch", return_value="file content"):
        result = run_task(
            agent_system_prompt="sys",
            task=task,
            workspace=ws,
            model="m",
            agent_tool_surfaces=[],
            logger=logger,
        )

    assert result["prompt_tokens"] == 50      # 20 + 30
    assert result["completion_tokens"] == 25  # 10 + 15
    assert result["total_latency_ms"] == pytest.approx(110.0)
