"""Tests for harness/bench.py — covering _find_agent_file, _parse_frontmatter,
_load_agent, _find_battery, _sha256, _write_manifest, _make_run_id,
run_bench, _run_task_loop, and resume_bench."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harness.bench import (
    _find_agent_file,
    _parse_frontmatter,
    _load_agent,
    _make_run_id,
    _sha256,
    _write_manifest,
    run_bench,
    resume_bench,
)
import harness.bench as bench_mod


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_agent(root: Path, name: str = "Test", subpath: str = "agents") -> Path:
    agent_dir = root / subpath
    agent_dir.mkdir(parents=True, exist_ok=True)
    f = agent_dir / f"{name.lower()}.agent.md"
    f.write_text(f"---\nname: {name}\nmodel: openai/gpt-4o-mini\n---\nDo the task.\n")
    return f


def _make_battery(root: Path, name: str = "test-bat", tasks: list | None = None) -> Path:
    battery_dir = root / name
    ws_dir = battery_dir / "workspaces" / "blank"
    ws_dir.mkdir(parents=True, exist_ok=True)
    battery = {
        "name": name,
        "tasks": tasks if tasks is not None else [
            {
                "name": "task1",
                "workspace": "blank",
                "prompt": "Do something",
                "scoring_criteria": [
                    {"dimension": "x", "type": "keyword", "terms": ["done"]},
                ],
            }
        ],
    }
    path = battery_dir / "battery.json"
    path.write_text(json.dumps(battery))
    return path


def _mock_workspace(tmp_path: Path) -> MagicMock:
    ws = MagicMock()
    ws.path = tmp_path / "workspace"
    ws.path.mkdir(exist_ok=True)
    ws.diff.return_value = ""
    ws.exec.return_value = (0, "", "")
    return ws


# ── _find_agent_file ──────────────────────────────────────────────────────────

def test_find_agent_file_with_source_repo(tmp_path, monkeypatch):
    """When source_repo is given, searches REGISTRY_DIR/source_repo directly."""
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    repo_dir = tmp_path / "registry" / "myorg" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "triage.agent.md").write_text("---\nname: Triage\n---\n")
    result = _find_agent_file("Triage", source_repo="myorg/repo")
    assert result.name == "triage.agent.md"


def test_find_agent_file_skips_non_dir_roots(tmp_path, monkeypatch):
    """source_repo pointing to non-existent path triggers line 40 continue."""
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    (tmp_path / "registry").mkdir()
    # source_repo path doesn't exist → root.is_dir() is False → continue
    with pytest.raises(FileNotFoundError, match="Agent 'Test'"):
        _find_agent_file("Test", source_repo="myorg/nonexistent")


def test_find_agent_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "empty-registry")
    (tmp_path / "empty-registry").mkdir()
    with pytest.raises(FileNotFoundError, match="Agent 'Missing'"):
        _find_agent_file("Missing", source_repo=None)


# ── _parse_frontmatter ────────────────────────────────────────────────────────

def test_parse_frontmatter_no_frontmatter():
    """Text without '---' → empty dict (line 53: return {})."""
    result = _parse_frontmatter("Just a description.\n")
    assert result == {}


def test_parse_frontmatter_unclosed():
    """Text with opening '---' but no closing → empty dict (line 57: return {})."""
    result = _parse_frontmatter("---\nname: Test\nno closing delimiter")
    assert result == {}


def test_parse_frontmatter_list_value():
    """Frontmatter with list value (line 67-68: list parsing)."""
    text = "---\nname: Test\ntools: [codebase, terminal]\n---\nBody.\n"
    result = _parse_frontmatter(text)
    assert result["name"] == "Test"
    assert result["tools"] == ["codebase", "terminal"]


def test_parse_frontmatter_normal():
    """Normal frontmatter with string values."""
    text = "---\nname: MyAgent\nmodel: openai/gpt-4o\n---\nDo things.\n"
    result = _parse_frontmatter(text)
    assert result["name"] == "MyAgent"
    assert result["model"] == "openai/gpt-4o"


# ── _load_agent ───────────────────────────────────────────────────────────────

def test_load_agent_with_frontmatter(tmp_path):
    """Agent with frontmatter: returns body and frontmatter dict."""
    f = tmp_path / "agent.agent.md"
    f.write_text("---\nname: Test\nmodel: openai/gpt-4o\n---\nDo the task.\n")
    body, fm = _load_agent(f)
    assert "Do the task." in body
    assert fm["name"] == "Test"


def test_load_agent_no_frontmatter(tmp_path):
    """Agent without frontmatter: returns full text and empty dict (line 84)."""
    f = tmp_path / "agent.agent.md"
    f.write_text("Just a plain description without frontmatter.\n")
    body, fm = _load_agent(f)
    assert "Just a plain description" in body
    assert fm == {}


# ── _sha256 ───────────────────────────────────────────────────────────────────

def test_sha256(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello world")
    result = _sha256(f)
    assert len(result) == 64  # SHA-256 hex digest


# ── _write_manifest ───────────────────────────────────────────────────────────

def test_write_manifest(tmp_path):
    run_dir = tmp_path / "run-xyz"
    run_dir.mkdir()
    agent_file = tmp_path / "registry" / "owner" / "repo" / "agents" / "test.agent.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text("---\nname: Test\n---\nBody\n")
    battery_file = tmp_path / "batteries" / "bat" / "battery.json"
    battery_file.parent.mkdir(parents=True)
    battery_file.write_text("{}")
    _write_manifest(run_dir, "run-xyz", agent_file, battery_file, "openai/gpt-4o-mini")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["run_id"] == "run-xyz"
    assert "agent_sha256" in manifest
    assert "battery_sha256" in manifest


def test_write_manifest_with_tags_and_seeds(tmp_path):
    run_dir = tmp_path / "run-abc"
    run_dir.mkdir()
    agent_file = tmp_path / "r" / "agents" / "a.agent.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text("x")
    battery_file = tmp_path / "b" / "battery.json"
    battery_file.parent.mkdir(parents=True)
    battery_file.write_text("{}")
    _write_manifest(run_dir, "run-abc", agent_file, battery_file, "m",
                    tags_filter=["smoke"], seeds=3)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["tags_filter"] == ["smoke"]
    assert manifest["seeds"] == 3


# ── _make_run_id ──────────────────────────────────────────────────────────────

def test_make_run_id():
    run_id = _make_run_id("Triage")
    assert run_id.startswith("run-")
    assert "triage" in run_id


# ── run_bench ─────────────────────────────────────────────────────────────────

def test_run_bench_no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    rc, run_id = run_bench(
        agent_name="Test", battery_name="b", source_repo=None,
        model=None, no_docker=True,
    )
    assert rc == 2
    assert run_id is None


def test_run_bench_agent_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    rc, run_id = run_bench(
        agent_name="Missing", battery_name=None, source_repo=None,
        model=None, no_docker=True,
    )
    assert rc == 2
    assert run_id is None


def test_run_bench_battery_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    # Create agent file
    agents_dir = tmp_path / "registry" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test.agent.md").write_text("---\nname: Test\n---\nBody.\n")
    (tmp_path / "batteries").mkdir(parents=True)
    rc, run_id = run_bench(
        agent_name="Test", battery_name="nonexistent-battery",
        source_repo=None, model=None, no_docker=True,
    )
    assert rc == 2
    assert run_id is None


def test_run_bench_empty_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")
    _make_agent(tmp_path / "registry")
    battery_dir = tmp_path / "batteries" / "empty-bat"
    battery_dir.mkdir(parents=True)
    (battery_dir / "battery.json").write_text(json.dumps({"name": "empty-bat", "tasks": []}))
    rc, run_id = run_bench(
        agent_name="Test", battery_name="empty-bat",
        source_repo=None, model=None, no_docker=True,
    )
    assert rc == 2
    assert run_id is None


def test_run_bench_basic(tmp_path, monkeypatch):
    """Full run_bench success path with mocked provision/run_task/score."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries")

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {
        "turn_count": 1, "prompt_tokens": 10,
        "completion_tokens": 10, "total_latency_ms": 100,
    }
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, run_id = run_bench(
            agent_name="Test", battery_name="test-bat",
            source_repo=None, model="openai/gpt-4o-mini",
            no_docker=True,
        )
    assert rc == 0
    assert run_id is not None
    run_json = tmp_path / "results" / run_id / "run.json"
    assert run_json.exists()
    data = json.loads(run_json.read_text())
    assert data["tasks"][0]["status"] == "ok"


def test_run_bench_provision_fails(tmp_path, monkeypatch):
    """Provision failure → task status = provision_failed."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries")

    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", side_effect=RuntimeError("Docker unavailable")), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, run_id = run_bench(
            agent_name="Test", battery_name="test-bat",
            source_repo=None, model="openai/gpt-4o-mini",
            no_docker=False,
        )
    assert rc == 0
    data = json.loads((tmp_path / "results" / run_id / "run.json").read_text())
    assert data["tasks"][0]["status"] == "provision_failed"


def test_run_bench_run_task_fails(tmp_path, monkeypatch):
    """run_task raises → task status = run_failed."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries")

    mock_ws = _mock_workspace(tmp_path)
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", side_effect=RuntimeError("LLM error")), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, run_id = run_bench(
            agent_name="Test", battery_name="test-bat",
            source_repo=None, model="openai/gpt-4o-mini",
            no_docker=True,
        )
    assert rc == 0
    data = json.loads((tmp_path / "results" / run_id / "run.json").read_text())
    assert data["tasks"][0]["status"] == "run_failed"


def test_run_bench_with_tags_no_match(tmp_path, monkeypatch):
    """--tags filter with no matching tasks returns rc=2."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries")  # tasks have no tags

    rc, run_id = run_bench(
        agent_name="Test", battery_name="test-bat",
        source_repo=None, model="openai/gpt-4o-mini",
        no_docker=True, tags=["nonexistent-tag"],
    )
    assert rc == 2


def test_run_bench_with_tags_and_limit(tmp_path, monkeypatch):
    """--tags matching + --limit applied → tasks filtered then limited."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries", tasks=[
        {"name": "t1", "workspace": "blank", "prompt": "A", "tags": ["smoke"],
         "scoring_criteria": [{"dimension": "x", "type": "keyword", "terms": ["y"]}]},
        {"name": "t2", "workspace": "blank", "prompt": "B", "tags": ["smoke"],
         "scoring_criteria": [{"dimension": "x", "type": "keyword", "terms": ["y"]}]},
    ])

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {"turn_count": 1, "prompt_tokens": 5, "completion_tokens": 5, "total_latency_ms": 50}
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, run_id = run_bench(
            agent_name="Test", battery_name="test-bat",
            source_repo=None, model="openai/gpt-4o-mini",
            no_docker=True, tags=["smoke"], limit=1,
        )
    assert rc == 0
    data = json.loads((tmp_path / "results" / run_id / "run.json").read_text())
    assert len(data["tasks"]) == 1


def test_run_bench_multi_seed(tmp_path, monkeypatch):
    """Multi-seed run aggregates scores (covers line 250, 316, 343-351)."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries")

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {"turn_count": 1, "prompt_tokens": 5, "completion_tokens": 5, "total_latency_ms": 50}
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, run_id = run_bench(
            agent_name="Test", battery_name="test-bat",
            source_repo=None, model="openai/gpt-4o-mini",
            no_docker=True, seeds=2,
        )
    assert rc == 0
    data = json.loads((tmp_path / "results" / run_id / "run.json").read_text())
    assert data["tasks"][0]["status"] == "ok"
    assert data["tasks"][0]["scores"]["seeds"] == 2


# ── resume_bench ──────────────────────────────────────────────────────────────

def test_resume_bench_no_run_json(tmp_path, monkeypatch, capsys):
    """No run.json for run_id → returns 2."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    rc, run_id = resume_bench(run_id="nonexistent-run")
    assert rc == 2
    assert run_id is None
    assert "no run.json" in capsys.readouterr().err.lower()


def test_resume_bench_no_failed_tasks(tmp_path, monkeypatch, capsys):
    """All tasks ok → prints 'No failed tasks', returns (0, run_id)."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    run_id = "run-all-ok"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id, "agent": "Test", "battery": "b", "model": "m",
        "tasks": [{"task": "t1", "status": "ok",
                   "scores": {"dimensions": {"x": True}, "total": 1, "max_score": 1}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    rc, out_id = resume_bench(run_id=run_id)
    assert rc == 0
    assert out_id == run_id
    assert "No failed tasks" in capsys.readouterr().out


def test_resume_bench_agent_not_found(tmp_path, monkeypatch, capsys):
    """resume_bench with nonexistent agent → returns 2."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "empty-reg")
    (tmp_path / "empty-reg").mkdir()
    run_id = "run-resume-noagent"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id, "agent": "MissingAgent", "battery": "b", "model": "m",
        "tasks": [{"task": "t1", "status": "provision_failed", "scores": {}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    rc, out_id = resume_bench(run_id=run_id)
    assert rc == 2
    assert out_id is None


def test_resume_bench_success(tmp_path, monkeypatch):
    """resume_bench success: re-runs failed tasks and merges results."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries")

    run_id = "run-resume-ok"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id, "agent": "Test", "battery": "test-bat", "model": "m",
        "source_repo": None,
        "tasks": [{"task": "task1", "status": "provision_failed", "scores": {}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {"turn_count": 1, "prompt_tokens": 5, "completion_tokens": 5, "total_latency_ms": 50}
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, out_id = resume_bench(run_id=run_id, model="openai/gpt-4o-mini", no_docker=True)

    assert rc == 0
    assert out_id == run_id
    updated = json.loads((run_dir / "run.json").read_text())
    assert updated["tasks"][0]["status"] == "ok"


def test_resume_bench_battery_not_found(tmp_path, monkeypatch, capsys):
    """resume_bench when battery doesn't exist → returns 2."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")

    _make_agent(tmp_path / "registry")
    (tmp_path / "batteries").mkdir(parents=True)

    run_id = "run-resume-nobat"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id, "agent": "Test", "battery": "nonexistent-bat", "model": "m",
        "source_repo": None,
        "tasks": [{"task": "task1", "status": "provision_failed", "scores": {}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    rc, out_id = resume_bench(run_id=run_id)
    assert rc == 2
    assert out_id is None


# ── _find_battery auto-detect ─────────────────────────────────────────────────

def test_find_battery_auto_detect(tmp_path, monkeypatch):
    """When battery_name is None, auto-detects battery matching agent name."""
    from harness.bench import _find_battery
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path)
    bat_dir = tmp_path / "auto-bat"
    bat_dir.mkdir()
    battery = {"name": "auto-bat", "target_agents": ["TestAgent"], "tasks": []}
    (bat_dir / "battery.json").write_text(json.dumps(battery))
    result_bat, result_path = _find_battery("TestAgent", battery_name=None)
    assert result_bat["name"] == "auto-bat"


def test_find_battery_auto_detect_all(tmp_path, monkeypatch):
    """Battery with target_agents: ['all'] matches any agent."""
    from harness.bench import _find_battery
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path)
    bat_dir = tmp_path / "all-bat"
    bat_dir.mkdir()
    battery = {"name": "all-bat", "target_agents": ["all"], "tasks": []}
    (bat_dir / "battery.json").write_text(json.dumps(battery))
    result_bat, _ = _find_battery("AnyAgent", battery_name=None)
    assert result_bat["name"] == "all-bat"


def test_find_battery_not_found(tmp_path, monkeypatch):
    """No matching battery → FileNotFoundError."""
    from harness.bench import _find_battery
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="No battery found"):
        _find_battery("Unknown", battery_name=None)


# ── line 382: run_bench with string tool_surfaces ────────────────────────────

def test_run_bench_string_tools(tmp_path, monkeypatch):
    """Agent frontmatter with tools as a string → converted to list (line 382)."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path / "results")

    # Agent with tools as a string (not a list)
    agents_dir = tmp_path / "registry" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test.agent.md").write_text(
        "---\nname: Test\nmodel: openai/gpt-4o-mini\ntools: codebase\n---\nBody.\n"
    )
    _make_battery(tmp_path / "batteries")

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {"turn_count": 1, "prompt_tokens": 5, "completion_tokens": 5, "total_latency_ms": 50}
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, run_id = run_bench(
            agent_name="Test", battery_name="test-bat",
            source_repo=None, model="openai/gpt-4o-mini",
            no_docker=True,
        )
    assert rc == 0


# ── lines 547, 563, 600: resume_bench edge cases ─────────────────────────────

def test_resume_bench_string_tools(tmp_path, monkeypatch):
    """resume_bench with agent tools as a string → converted to list (line 547)."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")

    agents_dir = tmp_path / "registry" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test.agent.md").write_text(
        "---\nname: Test\nmodel: openai/gpt-4o-mini\ntools: codebase\n---\nBody.\n"
    )
    _make_battery(tmp_path / "batteries")

    run_id = "run-resume-str-tools"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id, "agent": "Test", "battery": "test-bat", "model": "m",
        "source_repo": None,
        "tasks": [{"task": "task1", "status": "provision_failed", "scores": {}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {"turn_count": 1, "prompt_tokens": 5, "completion_tokens": 5, "total_latency_ms": 50}
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, out_id = resume_bench(run_id=run_id, model="openai/gpt-4o-mini", no_docker=True)
    assert rc == 0


def test_resume_bench_multi_seed_warning(tmp_path, monkeypatch, capsys):
    """resume_bench with seeds=2 triggers the multi-seed warning (line 563)."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")

    _make_agent(tmp_path / "registry")
    _make_battery(tmp_path / "batteries")

    run_id = "run-resume-seeds"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id, "agent": "Test", "battery": "test-bat", "model": "m",
        "source_repo": None,
        "tasks": [{"task": "task1", "status": "provision_failed", "scores": {}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {"turn_count": 1, "prompt_tokens": 5, "completion_tokens": 5, "total_latency_ms": 50}
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, out_id = resume_bench(run_id=run_id, seeds=2, no_docker=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Warning" in out


def test_resume_bench_new_task_name(tmp_path, monkeypatch):
    """Resume where retry task name differs from prior summary key → append (line 600)."""
    monkeypatch.setattr(bench_mod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(bench_mod, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(bench_mod, "BATTERIES_DIR", tmp_path / "batteries")

    _make_agent(tmp_path / "registry")

    # Battery task with no "name" field → _run_task_loop generates "task-01" as task key
    battery_dir = tmp_path / "batteries" / "test-bat"
    ws_dir = battery_dir / "workspaces" / "blank"
    ws_dir.mkdir(parents=True)
    battery = {
        "name": "test-bat",
        "tasks": [
            # No "name" field — _run_task_loop will use "task-01"
            {"workspace": "blank", "prompt": "Do something",
             "scoring_criteria": [{"dimension": "x", "type": "keyword", "terms": ["done"]}]},
        ],
    }
    (battery_dir / "battery.json").write_text(json.dumps(battery))

    run_id = "run-resume-newname"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    # Prior summary has task=None (from nameless task), status=provision_failed
    run = {
        "run_id": run_id, "agent": "Test", "battery": "test-bat", "model": "m",
        "source_repo": None,
        "tasks": [{"task": None, "status": "provision_failed", "scores": {}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))

    mock_ws = _mock_workspace(tmp_path)
    mock_result = {"turn_count": 1, "prompt_tokens": 5, "completion_tokens": 5, "total_latency_ms": 50}
    mock_score_result = {"dimensions": {"x": True}, "total": 1, "max_score": 1}
    mock_logger = MagicMock()
    mock_logger.__enter__ = MagicMock(return_value=mock_logger)
    mock_logger.__exit__ = MagicMock(return_value=False)

    with patch("harness.provision.provision", return_value=mock_ws), \
         patch("harness.invoke.run_task", return_value=mock_result), \
         patch("harness.score.score", return_value=mock_score_result), \
         patch("harness.log.TaskLogger", return_value=mock_logger), \
         patch("reporters.bench_report.render_report", return_value=0):
        rc, out_id = resume_bench(run_id=run_id, no_docker=True)
    assert rc == 0
    updated = json.loads((run_dir / "run.json").read_text())
    # The new summary "task-01" was appended since it wasn't in summary_idx
    task_names = [t["task"] for t in updated["tasks"]]
    assert "task-01" in task_names
