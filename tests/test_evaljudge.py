"""tests/test_evaljudge.py — unit tests for judges/evaljudge.py."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

import judges.evaljudge as evaljudge


# ---------------------------------------------------------------------------
# _parse_verdict
# ---------------------------------------------------------------------------

def test_parse_verdict_pass():
    assert evaljudge._parse_verdict("PASS\nThe agent correctly identified the tier.") is True


def test_parse_verdict_fail():
    assert evaljudge._parse_verdict("FAIL\nThe agent missed the blocker.") is False


def test_parse_verdict_pass_lowercase():
    # PASS must be at the start of the first line — case sensitive per spec,
    # but let's verify the exact behaviour: only True on "PASS" prefix
    assert evaljudge._parse_verdict("PASS — looks good") is True


def test_parse_verdict_ambiguous():
    assert evaljudge._parse_verdict("I think it passed.") is None


def test_parse_verdict_empty():
    assert evaljudge._parse_verdict("") is None


def test_parse_verdict_leading_whitespace():
    assert evaljudge._parse_verdict("  PASS\nreason") is True


# ---------------------------------------------------------------------------
# _build_conversation
# ---------------------------------------------------------------------------

_SAMPLE_EVENTS = [
    {"event": "workspace_snapshot", "label": "pre-task", "diff": ""},
    {"event": "turn_start", "turn": 1},
    {"event": "tool_call", "name": "read_file", "arguments": {"filePath": "billing.py"}},
    {"event": "tool_result", "name": "read_file", "result": "def claculate_total(): pass"},
    {"event": "tool_call", "name": "replace_string_in_file",
     "arguments": {"filePath": "billing.py", "oldString": "claculate", "newString": "calculate"}},
    {"event": "tool_result", "name": "replace_string_in_file", "result": "Replaced 1 occurrence"},
    {"event": "llm_response", "content": "Tier: Simple\nNo blockers.", "tool_calls": []},
    {"event": "workspace_snapshot", "label": "post-task",
     "diff": "-def claculate_total(): pass\n+def calculate_total(): pass"},
]


def test_build_conversation_includes_tool_calls():
    conv = evaljudge._build_conversation(_SAMPLE_EVENTS)
    assert "read_file" in conv
    assert "replace_string_in_file" in conv


def test_build_conversation_includes_final_response():
    conv = evaljudge._build_conversation(_SAMPLE_EVENTS)
    assert "Tier: Simple" in conv


def test_build_conversation_includes_diff():
    conv = evaljudge._build_conversation(_SAMPLE_EVENTS)
    assert "calculate_total" in conv


def test_build_conversation_no_events():
    conv = evaljudge._build_conversation([])
    assert "without any tool calls" in conv


def test_build_conversation_truncates_long_results():
    long_result = "x" * 1000
    events = [
        {"event": "turn_start", "turn": 1},
        {"event": "tool_result", "name": "read_file", "result": long_result},
    ]
    conv = evaljudge._build_conversation(events)
    assert "truncated" in conv
    # Should not be anywhere near the full 1000 chars in output
    assert len(conv) < 600


# ---------------------------------------------------------------------------
# _extract_task_prompt
# ---------------------------------------------------------------------------

def test_extract_task_prompt_found():
    events = [
        {"event": "llm_request", "messages": [
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "Rename the function."},
        ]},
    ]
    assert evaljudge._extract_task_prompt(events) == "Rename the function."


def test_extract_task_prompt_not_found():
    result = evaljudge._extract_task_prompt([])
    assert "unavailable" in result


# ---------------------------------------------------------------------------
# _find_llm_criteria
# ---------------------------------------------------------------------------

def test_find_llm_criteria_found(tmp_path, monkeypatch):
    battery_dir = tmp_path / "my-battery"
    battery_dir.mkdir()
    battery = {
        "name": "my-battery",
        "tasks": [{
            "name": "task-one",
            "scoring_criteria": [
                {"dimension": "quality", "type": "llm", "criterion": "Was the response concise?"},
                {"dimension": "format",  "type": "keyword", "terms": ["Tier:"]},
            ],
        }],
    }
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path)

    result = evaljudge._find_llm_criteria("my-battery", "task-one")
    assert result == {"quality": "Was the response concise?"}


def test_find_llm_criteria_no_llm_type(tmp_path, monkeypatch):
    battery_dir = tmp_path / "my-battery"
    battery_dir.mkdir()
    battery = {"name": "my-battery", "tasks": [{
        "name": "task-one",
        "scoring_criteria": [{"dimension": "fmt", "type": "keyword", "terms": ["Tier:"]}],
    }]}
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path)

    assert evaljudge._find_llm_criteria("my-battery", "task-one") == {}


def test_find_llm_criteria_battery_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path)
    assert evaljudge._find_llm_criteria("no-such-battery", "task-one") == {}


def test_find_llm_criteria_task_missing(tmp_path, monkeypatch):
    battery_dir = tmp_path / "my-battery"
    battery_dir.mkdir()
    (battery_dir / "battery.json").write_text(
        json.dumps({"tasks": [{"name": "other-task", "scoring_criteria": []}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path)
    assert evaljudge._find_llm_criteria("my-battery", "task-one") == {}


# ---------------------------------------------------------------------------
# score_llm_dimensions — end-to-end with mocked LLM
# ---------------------------------------------------------------------------

def _make_run(tmp_path: Path, monkeypatch) -> Path:
    """Set up a results dir with a run that has one pending LLM dimension."""
    run_id = "run-judge-test"
    run_dir = tmp_path / "results" / run_id
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True)

    run = {
        "run_id": run_id,
        "agent": "Triage",
        "battery": "test-battery",
        "model": "openai/gpt-4o",
        "isolation": "tmpdir",
        "tasks": [{
            "task": "task-alpha",
            "status": "ok",
            "scores": {
                "dimensions": {"format_valid": True, "quality": None},
                "total": 1,
                "max_score": 2,
            },
            "turn_count": 2,
            "prompt_tokens": 100,
            "completion_tokens": 50,
        }],
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")

    # Write minimal JSONL for the task
    events = [
        {"event": "llm_request", "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Do the task."},
        ]},
        {"event": "llm_response", "content": "Tier: Simple\nNo blockers."},
        {"event": "workspace_snapshot", "label": "post-task", "diff": ""},
    ]
    jsonl_path = tasks_dir / "task-alpha.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")

    # Battery with one llm criterion
    battery_dir = tmp_path / "batteries" / "test-battery"
    battery_dir.mkdir(parents=True)
    battery = {"name": "test-battery", "tasks": [{
        "name": "task-alpha",
        "scoring_criteria": [
            {"dimension": "format_valid", "type": "keyword", "terms": ["Tier:"]},
            {"dimension": "quality", "type": "llm", "criterion": "Was the tier label correct?"},
        ],
    }]}
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")

    monkeypatch.setattr(evaljudge, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path / "batteries")

    # Patch render_report to avoid FS side effects
    import reporters.bench_report as bench_report
    monkeypatch.setattr(bench_report, "RESULTS_DIR", tmp_path / "results")

    return tmp_path / "results"


def test_score_llm_dimensions_pass(tmp_path, monkeypatch):
    results_dir = _make_run(tmp_path, monkeypatch)
    monkeypatch.setattr(evaljudge, "_llm_call",
                        lambda messages, model: "PASS\nThe tier label was correct.")

    filled = evaljudge.score_llm_dimensions("run-judge-test")
    assert filled == 1

    run = json.loads((results_dir / "run-judge-test" / "run.json").read_text())
    task = run["tasks"][0]
    assert task["scores"]["dimensions"]["quality"] is True
    assert task["scores"]["total"] == 2  # was 1, now 2


def test_score_llm_dimensions_fail(tmp_path, monkeypatch):
    results_dir = _make_run(tmp_path, monkeypatch)
    monkeypatch.setattr(evaljudge, "_llm_call",
                        lambda messages, model: "FAIL\nThe tier label was wrong.")

    filled = evaljudge.score_llm_dimensions("run-judge-test")
    assert filled == 1

    run = json.loads((results_dir / "run-judge-test" / "run.json").read_text())
    task = run["tasks"][0]
    assert task["scores"]["dimensions"]["quality"] is False
    assert task["scores"]["total"] == 1  # unchanged


def test_score_llm_dimensions_ambiguous_leaves_null(tmp_path, monkeypatch):
    _make_run(tmp_path, monkeypatch)
    monkeypatch.setattr(evaljudge, "_llm_call",
                        lambda messages, model: "I'm not sure about this one.")

    filled = evaljudge.score_llm_dimensions("run-judge-test")
    assert filled == 0  # nothing scored


def test_score_llm_dimensions_llm_error_is_handled(tmp_path, monkeypatch):
    _make_run(tmp_path, monkeypatch)

    def _raise(messages, model):
        raise RuntimeError("API unavailable")

    monkeypatch.setattr(evaljudge, "_llm_call", _raise)
    # Should not raise — errors are caught and printed
    filled = evaljudge.score_llm_dimensions("run-judge-test")
    assert filled == 0


def test_score_llm_dimensions_no_pending(tmp_path, monkeypatch):
    results_dir = _make_run(tmp_path, monkeypatch)
    # Pre-fill the dimension so there's nothing pending
    run_path = results_dir / "run-judge-test" / "run.json"
    run = json.loads(run_path.read_text())
    run["tasks"][0]["scores"]["dimensions"]["quality"] = True
    run["tasks"][0]["scores"]["total"] = 2
    run_path.write_text(json.dumps(run))

    called = []
    monkeypatch.setattr(evaljudge, "_llm_call", lambda *a, **kw: called.append(1) or "PASS")

    filled = evaljudge.score_llm_dimensions("run-judge-test")
    assert filled == 0
    assert called == []  # LLM was not called


def test_score_llm_dimensions_missing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(evaljudge, "RESULTS_DIR", tmp_path / "results")
    with pytest.raises(FileNotFoundError):
        evaljudge.score_llm_dimensions("run-does-not-exist")


# ---------------------------------------------------------------------------
# score_llm_dimensions — multi-seed JSONL loading
# ---------------------------------------------------------------------------

def _make_run_multiseed(tmp_path: Path, monkeypatch, n_seeds: int = 3) -> Path:
    """Run where the task was executed with seeds > 1 (JSONL files per seed)."""
    run_id = "run-multiseed-test"
    run_dir = tmp_path / "results" / run_id
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True)

    run = {
        "run_id": run_id,
        "agent": "Triage",
        "battery": "test-battery",
        "model": "openai/gpt-4o",
        "isolation": "tmpdir",
        "tasks": [{
            "task": "task-alpha",
            "status": "ok",
            "scores": {
                "dimensions": {"quality": None},
                "total": 0,
                "max_score": 1,
                "seeds": n_seeds,
                "pass_rates": {
                    "quality": {"pass": 0, "fail": 0, "null": n_seeds,
                                "rate": 0.0, "ci_low": 0.0, "ci_high": 1.0},
                },
            },
            "turn_count": 2,
            "prompt_tokens": 100 * n_seeds,
            "completion_tokens": 50 * n_seeds,
        }],
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")

    # Write one JSONL per seed
    user_content = [f"seed-{i}-prompt" for i in range(n_seeds)]
    for i in range(n_seeds):
        events = [
            {"event": "llm_request", "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": user_content[i]},
            ]},
            {"event": "llm_response", "content": f"Response from seed {i}."},
            {"event": "workspace_snapshot", "label": "post-task", "diff": ""},
        ]
        seed_path = tasks_dir / f"task-alpha-seed-{i}.jsonl"
        with seed_path.open("w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")

    # Battery
    battery_dir = tmp_path / "batteries" / "test-battery"
    battery_dir.mkdir(parents=True)
    battery = {"name": "test-battery", "tasks": [{
        "name": "task-alpha",
        "scoring_criteria": [
            {"dimension": "quality", "type": "llm", "criterion": "Was it good?"},
        ],
    }]}
    (battery_dir / "battery.json").write_text(json.dumps(battery), encoding="utf-8")

    monkeypatch.setattr(evaljudge, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(evaljudge, "BATTERIES_DIR", tmp_path / "batteries")

    import reporters.bench_report as bench_report
    monkeypatch.setattr(bench_report, "RESULTS_DIR", tmp_path / "results")

    return tmp_path / "results"


def test_multiseed_judge_loads_all_seed_files(tmp_path, monkeypatch):
    """Confirm all seed JSONL files are loaded (conversation includes content from each seed)."""
    _make_run_multiseed(tmp_path, monkeypatch, n_seeds=3)

    captured: list[str] = []

    def _fake_llm(messages, model):
        # Capture the user message to inspect seed content
        for msg in messages:
            if msg["role"] == "user":
                captured.append(msg["content"])
        return "PASS\nLooks good."

    monkeypatch.setattr(evaljudge, "_llm_call", _fake_llm)
    evaljudge.score_llm_dimensions("run-multiseed-test")

    assert captured, "LLM was not called"
    combined = captured[0]
    # All three seed responses should appear in the merged conversation
    assert "Response from seed 0" in combined
    assert "Response from seed 1" in combined
    assert "Response from seed 2" in combined


def test_multiseed_judge_pass_updates_pass_rates(tmp_path, monkeypatch):
    """After PASS verdict, pass_rates[dim] should reflect N passes, not stale nulls."""
    n_seeds = 3
    results_dir = _make_run_multiseed(tmp_path, monkeypatch, n_seeds=n_seeds)
    monkeypatch.setattr(evaljudge, "_llm_call", lambda *a, **kw: "PASS\nGood.")

    evaljudge.score_llm_dimensions("run-multiseed-test")

    run = json.loads((results_dir / "run-multiseed-test" / "run.json").read_text())
    pr = run["tasks"][0]["scores"]["pass_rates"]["quality"]
    assert pr["pass"] == n_seeds
    assert pr["fail"] == 0
    assert pr["null"] == 0
    assert pr["rate"] == 1.0
    assert pr["ci_low"] > 0.0   # Wilson lower bound for 3/3


def test_multiseed_judge_fail_updates_pass_rates(tmp_path, monkeypatch):
    n_seeds = 3
    results_dir = _make_run_multiseed(tmp_path, monkeypatch, n_seeds=n_seeds)
    monkeypatch.setattr(evaljudge, "_llm_call", lambda *a, **kw: "FAIL\nBad.")

    evaljudge.score_llm_dimensions("run-multiseed-test")

    run = json.loads((results_dir / "run-multiseed-test" / "run.json").read_text())
    pr = run["tasks"][0]["scores"]["pass_rates"]["quality"]
    assert pr["fail"] == n_seeds
    assert pr["pass"] == 0
    assert pr["rate"] == 0.0
    assert pr["ci_high"] < 1.0   # Wilson upper bound for 0/3
