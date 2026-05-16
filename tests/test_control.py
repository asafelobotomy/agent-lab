"""Tests for the Control baseline agent and related bench return-type changes."""
from __future__ import annotations

import json
from unittest.mock import patch

from harness.bench import _find_agent_file, resume_bench, run_bench


# ── Control agent is resolvable ───────────────────────────────────────────────

def test_control_agent_exists():
    """registry/builtin/agents/control.agent.md must be present."""
    path = _find_agent_file("Control", None)
    assert path.exists()
    assert path.stem == "control.agent"


def test_control_agent_has_no_tools():
    """Control frontmatter must declare tools: [] (empty — no tool surface)."""
    from harness.bench import _load_agent
    path = _find_agent_file("Control", None)
    _, fm = _load_agent(path)
    tools = fm.get("tools", ["codebase"])
    # Parsed as list; must be empty (no tools for the baseline)
    assert isinstance(tools, list)
    assert tools == [], f"Control agent should have no tools, got: {tools}"


def test_control_agent_has_system_prompt():
    """Control must have a non-empty body so the LLM gets some instruction."""
    from harness.bench import _load_agent
    path = _find_agent_file("Control", None)
    system_prompt, _ = _load_agent(path)
    assert system_prompt.strip(), "Control agent system prompt must not be empty"


# ── run_bench returns tuple[int, str | None] ──────────────────────────────────

def test_run_bench_returns_tuple_on_missing_token(monkeypatch):
    """run_bench returns (2, None) when GITHUB_TOKEN is absent."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = run_bench(
        agent_name="Triage",
        battery_name=None,
        source_repo=None,
        model=None,
        no_docker=True,
    )
    assert isinstance(result, tuple), "run_bench must return a tuple"
    rc, run_id = result
    assert rc == 2
    assert run_id is None


def test_run_bench_returns_tuple_on_missing_agent(monkeypatch, tmp_path):
    """run_bench returns (2, None) when the agent file is not found."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    # Point registry at empty tmp dir so no agents exist
    with patch("harness.bench.REGISTRY_DIR", tmp_path):
        result = run_bench(
            agent_name="NonExistentAgent",
            battery_name=None,
            source_repo=None,
            model=None,
            no_docker=True,
        )
    rc, run_id = result
    assert rc == 2
    assert run_id is None


# ── resume_bench returns tuple[int, str | None] ───────────────────────────────

def test_resume_bench_returns_tuple_on_missing_run(tmp_path, monkeypatch):
    """resume_bench returns (2, None) when run.json is not found."""
    monkeypatch.setattr("harness.bench.RESULTS_DIR", tmp_path)
    rc, run_id = resume_bench(run_id="run-nonexistent")
    assert rc == 2
    assert run_id is None


def test_resume_bench_returns_run_id_when_no_failures(tmp_path, monkeypatch):
    """resume_bench returns (0, run_id) when all tasks already passed."""
    monkeypatch.setattr("harness.bench.RESULTS_DIR", tmp_path)
    run_dir = tmp_path / "run-done"
    run_dir.mkdir()
    run_meta = {
        "run_id": "run-done",
        "agent": "Triage",
        "battery": "triage-core",
        "model": "openai/gpt-4o-mini",
        "tasks": [{"task": "t1", "status": "ok", "scores": {"total": 1, "max_score": 1}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run_meta), encoding="utf-8")

    rc, run_id = resume_bench(run_id="run-done")
    assert rc == 0
    assert run_id == "run-done"


# ── --no-control flag ─────────────────────────────────────────────────────────

def test_no_control_flag_skips_control_run(monkeypatch, tmp_path):
    """When --no-control is set, _cmd_bench must not invoke run_bench for Control."""
    import argparse

    from cli import _cmd_bench

    call_log: list[str] = []

    def _fake_run_bench(*, agent_name, **kwargs):
        call_log.append(agent_name)
        run_id = f"run-fake-{agent_name.lower()}"
        # Write run.json for the main agent so battery lookup works
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(
            json.dumps({"battery": "triage-core", "tasks": []}), encoding="utf-8"
        )
        return 0, run_id

    # _cmd_bench uses `from harness.bench import run_bench, RESULTS_DIR`
    # at call time — patch the module-level name that gets imported
    import harness.bench as bench_mod
    monkeypatch.setattr(bench_mod, "run_bench", _fake_run_bench)
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)

    args = argparse.Namespace(
        agent="Triage",
        battery="triage-core",
        repo=None,
        model=None,
        no_docker=True,
        limit=None,
        tags=None,
        seeds=1,
        no_control=True,   # ← the flag under test
    )

    rc = _cmd_bench(args)

    assert rc == 0
    assert "Control" not in call_log, (
        f"Control should NOT be benchmarked when --no-control is set. Calls: {call_log}"
    )


def test_control_bench_called_without_no_control_flag(monkeypatch, tmp_path):
    """Without --no-control, _cmd_bench must run the Control agent after the main agent."""
    import argparse

    from cli import _cmd_bench

    call_log: list[str] = []

    def _fake_run_bench(*, agent_name, **kwargs):
        call_log.append(agent_name)
        run_id = f"run-fake-{agent_name.lower()}"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(
            json.dumps({"battery": "triage-core", "tasks": []}), encoding="utf-8"
        )
        return 0, run_id

    def _fake_compare(*, run_a, run_b):
        pass  # suppress output

    import harness.bench as bench_mod
    import reporters.compare as compare_mod
    monkeypatch.setattr(bench_mod, "run_bench", _fake_run_bench)
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(compare_mod, "compare_runs", _fake_compare)

    args = argparse.Namespace(
        agent="Triage",
        battery="triage-core",
        repo=None,
        model=None,
        no_docker=True,
        limit=None,
        tags=None,
        seeds=1,
        no_control=False,  # ← control should run
    )

    rc = _cmd_bench(args)

    assert rc == 0
    assert call_log == ["Triage", "Control"], (
        f"Expected Triage then Control; got: {call_log}"
    )
