"""Gap tests for judges/evaljudge.py — covers remaining uncovered paths."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch
from pathlib import Path

import pytest

import judges.evaljudge as evaljudge


# ── _llm_call HTTPError path ──────────────────────────────────────────────────

def test_llm_call_http_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    class _FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__(url="http://x", code=429, msg="Rate limited", hdrs={}, fp=None)
        def read(self):
            return b"Too many requests"

    with patch("judges.evaljudge.urllib.request.urlopen", side_effect=_FakeHTTPError()), \
         patch("judges.evaljudge.time.sleep"):
        with pytest.raises(RuntimeError, match="LLM API error 429"):
            evaljudge._llm_call([], "openai/gpt-4o")


# ── _llm_call no token ────────────────────────────────────────────────────────

def test_llm_call_no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN not set"):
        evaljudge._llm_call([], "openai/gpt-4o")


# ── _wilson_ci_lo / _wilson_ci_hi edge cases ─────────────────────────────────

def test_wilson_ci_lo_all_pass():
    lo = evaljudge._wilson_ci_lo(10, 10)
    assert lo > 0.69


def test_wilson_ci_hi_all_fail():
    hi = evaljudge._wilson_ci_hi(0, 10)
    assert hi < 0.31


def test_wilson_ci_lo_zero_n():
    assert evaljudge._wilson_ci_lo(0, 0) == 0.0


def test_wilson_ci_hi_zero_n():
    assert evaljudge._wilson_ci_hi(0, 0) == 1.0


def test_wilson_ci_lo_clamped_to_zero():
    # Should never go below 0
    lo = evaljudge._wilson_ci_lo(0, 100)
    assert lo >= 0.0


def test_wilson_ci_hi_clamped_to_one():
    # Should never exceed 1
    hi = evaljudge._wilson_ci_hi(100, 100)
    assert hi <= 1.0


# ── _build_conversation — extra event types ───────────────────────────────────

def test_build_conversation_with_tool_call_many_args():
    """Tool calls with >3 args should be truncated with '…'."""
    events = [
        {"event": "turn_start", "turn": 1},
        {"event": "tool_call", "name": "multi_replace", "arguments": {
            "a": "1", "b": "2", "c": "3", "d": "4",
        }},
    ]
    conv = evaljudge._build_conversation(events)
    assert "…" in conv


def test_build_conversation_llm_response_no_content():
    """llm_response with no content should not raise."""
    events = [
        {"event": "llm_response", "content": None},
    ]
    conv = evaljudge._build_conversation(events)
    # Should not crash; no agent response line added
    assert isinstance(conv, str)


def test_build_conversation_turn_tracking():
    """turn_start event updates the turn counter for subsequent tool calls."""
    events = [
        {"event": "turn_start", "turn": 5},
        {"event": "tool_call", "name": "read_file", "arguments": {}},
    ]
    conv = evaljudge._build_conversation(events)
    assert "[turn 5]" in conv


def test_build_conversation_workspace_snapshot_pre_not_in_body():
    """pre-task workspace_snapshot should not appear as diff in conversation body."""
    events = [
        {"event": "workspace_snapshot", "label": "pre-task", "diff": "pre-diff"},
    ]
    conv = evaljudge._build_conversation(events)
    assert "pre-diff" not in conv


def test_build_conversation_post_diff_added():
    """post-task snapshot diff should appear at the end of conversation."""
    events = [
        {"event": "workspace_snapshot", "label": "post-task", "diff": "+new line"},
    ]
    conv = evaljudge._build_conversation(events)
    assert "+new line" in conv


# ── _extract_task_prompt — no llm_request event ───────────────────────────────

def test_extract_task_prompt_no_user_message():
    """llm_request event with only system messages should still return unavailable."""
    events = [
        {"event": "llm_request", "messages": [
            {"role": "system", "content": "You are an agent."},
        ]},
    ]
    result = evaljudge._extract_task_prompt(events)
    assert "unavailable" in result


# ── _find_llm_criteria — bad JSON ────────────────────────────────────────────

def test_find_llm_criteria_bad_json(tmp_path, monkeypatch):
    battery_dir = tmp_path / "bad-battery"
    battery_dir.mkdir()
    (battery_dir / "battery.json").write_text("not valid json {{{{", encoding="utf-8")
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path)
    result = evaljudge._find_llm_criteria("bad-battery", "any-task")
    assert result == {}


# ── score_llm_dimensions — no criteria in battery ────────────────────────────

def _make_minimal_run(tmp_path: Path, monkeypatch, *, has_llm_criterion: bool = True) -> Path:
    run_id = "run-gap-test"
    run_dir = tmp_path / run_id
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True)

    run = {
        "run_id": run_id,
        "battery": "gap-battery",
        "model": "openai/gpt-4o",
        "tasks": [{
            "task": "gap-task",
            "status": "ok",
            "scores": {
                "dimensions": {"quality": None},
                "total": 0,
                "max_score": 1,
            },
        }],
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (tasks_dir / "gap-task.jsonl").write_text(
        json.dumps({"event": "llm_request", "messages": [{"role": "user", "content": "do it"}]}) + "\n"
    )

    battery_dir = tmp_path / "batteries" / "gap-battery"
    battery_dir.mkdir(parents=True)
    criteria: list = []
    if has_llm_criterion:
        criteria = [{"dimension": "quality", "type": "llm", "criterion": "Was it good?"}]
    else:
        criteria = [{"dimension": "quality", "type": "keyword", "terms": ["ok"]}]

    battery = {"tasks": [{"name": "gap-task", "scoring_criteria": criteria}]}
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")

    monkeypatch.setattr(evaljudge, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path / "batteries")

    import reporters.bench_report as bench_report
    monkeypatch.setattr(bench_report, "RESULTS_DIR", tmp_path)

    return tmp_path


def test_score_llm_no_criteria_in_battery(tmp_path, monkeypatch, capsys):
    """When battery has no llm-type criteria, the task is skipped with a message."""
    _make_minimal_run(tmp_path, monkeypatch, has_llm_criterion=False)
    called = []
    monkeypatch.setattr(evaljudge, "_llm_call", lambda *a, **kw: called.append(1) or "PASS")

    filled = evaljudge.score_llm_dimensions("run-gap-test")
    assert filled == 0
    assert called == []
    out = capsys.readouterr().out
    assert "no llm criteria" in out.lower()


def test_score_llm_criterion_text_missing_for_dim(tmp_path, monkeypatch, capsys):
    """When a pending dim has no criterion text in battery, it's skipped."""
    run_id = "run-nocrit"
    run_dir = tmp_path / run_id
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True)

    run = {
        "battery": "nc-battery",
        "model": "openai/gpt-4o",
        "tasks": [{"task": "t1", "status": "ok",
                   "scores": {"dimensions": {"quality": None}, "total": 0, "max_score": 1}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (tasks_dir / "t1.jsonl").write_text(
        json.dumps({"event": "llm_request", "messages": [{"role": "user", "content": "task"}]}) + "\n"
    )

    # Battery has llm type but with no criterion text
    battery_dir = tmp_path / "batteries" / "nc-battery"
    battery_dir.mkdir(parents=True)
    battery = {"tasks": [{"name": "t1", "scoring_criteria": [
        {"dimension": "quality", "type": "llm", "criterion": ""},  # empty criterion
    ]}]}
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")

    monkeypatch.setattr(evaljudge, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path / "batteries")

    import reporters.bench_report as bench_report
    monkeypatch.setattr(bench_report, "RESULTS_DIR", tmp_path)

    called = []
    monkeypatch.setattr(evaljudge, "_llm_call", lambda *a, **kw: called.append(1) or "PASS")
    filled = evaljudge.score_llm_dimensions("run-nocrit")
    assert filled == 0
    assert called == []


# ── _throttle sleep path (line 86) ───────────────────────────────────────────

def test_throttle_sleeps_when_recent():
    """When _last_call is recent, _throttle sleeps (line 86)."""
    import time
    import judges.evaljudge as m
    old_last_call = m._last_call
    try:
        m._last_call = time.monotonic()  # set to now → elapsed ≈ 0 → sleep fires
        with patch("judges.evaljudge.time.sleep") as mock_sleep:
            m._throttle()
        mock_sleep.assert_called_once()
    finally:
        m._last_call = old_last_call


# ── _llm_call success path (lines 113, 116) ──────────────────────────────────

def test_llm_call_success(monkeypatch):
    """Mock urlopen to return success → covers lines 113 and 116."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    fake_resp_data = json.dumps({
        "choices": [{"message": {"content": "PASS\nGood response."}}]
    }).encode()

    class _MockResp:
        def read(self):
            return fake_resp_data
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    import judges.evaljudge as m
    old_last_call = m._last_call
    try:
        m._last_call = 0.0  # force old timestamp so no sleep needed
        with patch("judges.evaljudge.urllib.request.urlopen", return_value=_MockResp()):
            result = m._llm_call([{"role": "user", "content": "hi"}], "openai/gpt-4o")
        assert result == "PASS\nGood response."
    finally:
        m._last_call = old_last_call


# ── _load_jsonl — non-existent path (line 123) ───────────────────────────────

def test_load_jsonl_nonexistent_path(tmp_path):
    """_load_jsonl with non-existent file returns empty list (line 123)."""
    result = evaljudge._load_jsonl(tmp_path / "does_not_exist.jsonl")
    assert result == []


# ── _load_jsonl — invalid JSON line (lines 130-131) ──────────────────────────

def test_load_jsonl_invalid_json_line(tmp_path):
    """_load_jsonl skips invalid JSON lines (lines 130-131 except clause)."""
    f = tmp_path / "test.jsonl"
    f.write_text('{"valid": true}\nnot valid json {\n{"also": "valid"}\n', encoding="utf-8")
    result = evaljudge._load_jsonl(f)
    assert len(result) == 2
    assert result[0] == {"valid": True}
    assert result[1] == {"also": "valid"}


# ── score_llm_dimensions — non-ok task skipped (line 244) ────────────────────

def test_score_llm_non_ok_task_skipped(tmp_path, monkeypatch, capsys):
    """Tasks with status != 'ok' are skipped via continue (line 244)."""
    run_id = "run-nonok"
    run_dir = tmp_path / run_id
    (run_dir / "tasks").mkdir(parents=True)
    run = {
        "battery": "b",
        "model": "openai/gpt-4o",
        "tasks": [
            {"task": "t1", "status": "provision_failed", "scores": {}},
            {"task": "t2", "status": "ok",
             "scores": {"dimensions": {"quality": None}, "total": 0, "max_score": 1}},
        ],
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (run_dir / "tasks" / "t2.jsonl").write_text(
        json.dumps({"event": "llm_request", "messages": [{"role": "user", "content": "task"}]}) + "\n"
    )
    battery_dir = tmp_path / "batteries" / "b"
    battery_dir.mkdir(parents=True)
    battery = {"tasks": [{"name": "t2", "scoring_criteria": [
        {"dimension": "quality", "type": "keyword", "terms": ["done"]},
    ]}]}
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")
    monkeypatch.setattr(evaljudge, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path / "batteries")
    import reporters.bench_report as bench_report
    monkeypatch.setattr(bench_report, "RESULTS_DIR", tmp_path)
    called = []
    monkeypatch.setattr(evaljudge, "_llm_call", lambda *a, **kw: called.append(1) or "PASS")
    evaljudge.score_llm_dimensions(run_id)
    # t1 is non-ok and skipped; t2 has keyword criterion only so also no llm call
    assert called == []


# ── score_llm_dimensions — criterion text missing for specific dim (lines 275-276) ──

def test_score_llm_criterion_missing_for_pending_dim(tmp_path, monkeypatch, capsys):
    """Battery has llm criteria for 'quality' but task has pending 'format' dim → skipped."""
    run_id = "run-nodim-crit"
    run_dir = tmp_path / run_id
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    run = {
        "battery": "mixed-battery",
        "model": "openai/gpt-4o",
        "tasks": [{"task": "t1", "status": "ok",
                   "scores": {
                       "dimensions": {"quality": None, "format": None},
                       "total": 0, "max_score": 2,
                   }}],
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (tasks_dir / "t1.jsonl").write_text(
        json.dumps({"event": "llm_request", "messages": [{"role": "user", "content": "task"}]}) + "\n"
    )
    # Battery only has llm criterion for 'quality', not 'format'
    battery_dir = tmp_path / "batteries" / "mixed-battery"
    battery_dir.mkdir(parents=True)
    battery = {"tasks": [{"name": "t1", "scoring_criteria": [
        {"dimension": "quality", "type": "llm", "criterion": "Was it good?"},
        # 'format' is missing from criteria → llm_criteria.get("format") is None
    ]}]}
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")
    monkeypatch.setattr(evaljudge, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path / "batteries")
    import reporters.bench_report as bench_report
    monkeypatch.setattr(bench_report, "RESULTS_DIR", tmp_path)
    called = []
    monkeypatch.setattr(evaljudge, "_llm_call", lambda *a, **kw: called.append("PASS") or "PASS")
    evaljudge.score_llm_dimensions(run_id)
    out = capsys.readouterr().out
    assert "criterion text missing" in out.lower() or "skipping" in out.lower()
    # Only 'quality' should have triggered an llm call, not 'format'
    assert len(called) == 1
