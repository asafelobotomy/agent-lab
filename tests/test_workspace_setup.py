"""Tests for workspace tailoring — setup commands, workspace templates, fetch context."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BATTERIES_DIR = REPO_ROOT / "batteries"


# ── Workspace templates exist and have content ────────────────────────────────

@pytest.mark.parametrize("workspace,expected_files", [
    ("billing-rename",  ["billing.py", "invoice.py", "tests/test_billing.py"]),
    ("orm-sqlite",      ["models/user.py", "db.py", "requirements.txt", "tests/test_models.py"]),
    ("jwt-auth",        ["api/auth.py", "api/routes.py", "core/__init__.py"]),
    ("legacy-db",       ["models/session.py", "migrations/001_sessions.sql"]),
])
def test_workspace_template_files(workspace: str, expected_files: list[str]):
    base = BATTERIES_DIR / "triage-core" / "workspaces" / workspace
    assert base.is_dir(), f"Workspace template '{workspace}' does not exist"
    for rel in expected_files:
        assert (base / rel).is_file(), f"{workspace}/{rel} is missing"


def test_billing_rename_has_typo():
    """billing-rename workspace must have the function name typo the task is about."""
    code = (BATTERIES_DIR / "triage-core/workspaces/billing-rename/billing.py").read_text()
    assert "claculate_total" in code, "billing.py must contain the typo 'claculate_total'"


def test_orm_sqlite_uses_sqlite3():
    """orm-sqlite workspace must use raw sqlite3 (pre-migration baseline)."""
    code = (BATTERIES_DIR / "triage-core/workspaces/orm-sqlite/models/user.py").read_text()
    assert "sqlite3" in code
    assert "sqlalchemy" not in code.lower()


def test_jwt_auth_has_validate_token():
    """jwt-auth workspace must contain the validate_token function to be extracted."""
    code = (BATTERIES_DIR / "triage-core/workspaces/jwt-auth/api/auth.py").read_text()
    assert "validate_token" in code
    assert "def validate_token" in code


def test_legacy_db_has_legacy_sessions_model():
    """legacy-db workspace must contain the LegacySession model."""
    code = (BATTERIES_DIR / "triage-core/workspaces/legacy-db/models/session.py").read_text()
    assert "LegacySession" in code
    assert "legacy_sessions" in code


# ── Battery tasks reference correct workspaces ────────────────────────────────

def test_battery_workspace_references():
    battery = json.loads(
        (BATTERIES_DIR / "triage-core/battery.json").read_text(encoding="utf-8")
    )
    expected = {
        "triage-typo-rename":   "billing-rename",
        "triage-orm-migration": "orm-sqlite",
        "triage-auth-refactor": "jwt-auth",
        "triage-prod-delete":   "legacy-db",
    }
    task_map = {t["name"]: t["workspace"] for t in battery["tasks"]}
    for task_name, expected_ws in expected.items():
        assert task_map.get(task_name) == expected_ws, (
            f"Task '{task_name}' should use workspace '{expected_ws}', "
            f"got '{task_map.get(task_name)}'"
        )


def test_vague_blocked_has_setup_commands():
    """triage-vague-blocked uses blank workspace with a setup command."""
    battery = json.loads(
        (BATTERIES_DIR / "triage-core/battery.json").read_text(encoding="utf-8")
    )
    task = next(t for t in battery["tasks"] if t["name"] == "triage-vague-blocked")
    assert task["workspace"] == "blank"
    assert "setup" in task and len(task["setup"]) > 0, (
        "triage-vague-blocked should have at least one setup command"
    )


# ── Setup commands run via workspace.exec ─────────────────────────────────────

def test_setup_commands_logged_in_task_loop(tmp_path, monkeypatch):
    """When a task has 'setup' commands they are executed and logged."""
    import harness.bench as bench_mod

    logged_setups: list[dict] = []

    class _MockLogger:
        def __init__(self, run_id, task_name):
            pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def retry_start(self, **_): pass
        def workspace_setup(self, cmd, rc, stdout, stderr):
            logged_setups.append({"cmd": cmd, "rc": rc})
        def workspace_snapshot(self, *_): pass
        def error(self, *_): pass

    class _MockWorkspace:
        path = tmp_path
        def exec(self, cmd):
            return 0, "ok", ""
        def diff(self): return ""
        def destroy(self): pass

    def _mock_provision(*a, **kw):
        return _MockWorkspace()

    def _mock_run_task(**kw):
        return {
            "final_response": "Tier: Simple. No blockers.",
            "tool_calls_made": [],
            "turn_count": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_latency_ms": 100.0,
            "workspace_diff": "",
        }

    def _mock_score(task, result):
        return {"dimensions": {"format_valid": True}, "total": 1, "max_score": 1}

    task_with_setup = {
        "name": "setup-test-task",
        "workspace": "blank",
        "setup": ["echo hello > test.txt", "ls"],
        "prompt": "Trivial task.",
        "scoring_criteria": [
            {"dimension": "format_valid", "type": "keyword", "terms": ["Tier:"]}
        ],
    }

    bench_mod._run_task_loop(
        tasks=[task_with_setup],
        run_id="run-test-setup",
        battery_name="test-battery",
        system_prompt="You are a triage agent.",
        tool_surfaces=["codebase"],
        resolved_model="openai/gpt-4o-mini",
        no_docker=True,
        seeds=1,
        provision_fn=_mock_provision,
        run_task_fn=_mock_run_task,
        score_fn=_mock_score,
        TaskLogger=_MockLogger,
    )

    assert len(logged_setups) == 2, f"Expected 2 setup events, got: {logged_setups}"
    assert logged_setups[0]["cmd"] == "echo hello > test.txt"
    assert logged_setups[0]["rc"] == 0
    assert logged_setups[1]["cmd"] == "ls"


def test_setup_failure_marks_provision_failed(tmp_path):
    """A failing setup command (rc != 0) marks the task as provision_failed."""
    import harness.bench as bench_mod

    class _MockLogger:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def retry_start(self, **_): pass
        def workspace_setup(self, *a, **kw): pass
        def workspace_snapshot(self, *_): pass
        def error(self, *a, **kw): pass

    class _FailingWorkspace:
        path = tmp_path
        def exec(self, cmd): return 1, "", "command not found"
        def diff(self): return ""
        def destroy(self): pass

    def _mock_provision(*a, **kw): return _FailingWorkspace()
    def _mock_run_task(**kw): return {}
    def _mock_score(task, result): return {}

    task = {
        "name": "bad-setup-task",
        "workspace": "blank",
        "setup": ["this-command-will-fail"],
        "prompt": "Irrelevant.",
        "scoring_criteria": [],
    }

    summaries = bench_mod._run_task_loop(
        tasks=[task],
        run_id="run-test-fail",
        battery_name="test",
        system_prompt="",
        tool_surfaces=[],
        resolved_model="openai/gpt-4o-mini",
        no_docker=True,
        seeds=1,
        provision_fn=_mock_provision,
        run_task_fn=_mock_run_task,
        score_fn=_mock_score,
        TaskLogger=_MockLogger,
    )

    assert summaries[0]["status"] == "provision_failed"


# ── fetch.py captures README ──────────────────────────────────────────────────

def test_copy_context_captures_readme(tmp_path):
    """_copy_context copies README.md into context/ and returns its relative path."""
    from harness.fetch import _copy_context

    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("# My Agent\nDoes useful things.", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()

    captured = _copy_context(src, dest)

    assert "context/README.md" in captured
    assert (dest / "context" / "README.md").is_file()
    assert "My Agent" in (dest / "context" / "README.md").read_text()


def test_copy_context_no_readme(tmp_path):
    """_copy_context returns an empty list when no README exists."""
    from harness.fetch import _copy_context

    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()

    captured = _copy_context(src, dest)
    assert captured == []


def test_copy_context_copies_docs(tmp_path):
    """_copy_context copies a docs/ directory when present."""
    from harness.fetch import _copy_context

    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "guide.md").write_text("# Guide", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()

    captured = _copy_context(src, dest)

    assert "context/docs/" in captured
    assert (dest / "context" / "docs" / "guide.md").is_file()


def test_fetch_registry_json_includes_context_field(tmp_path, monkeypatch):
    """fetch_repo writes a 'context' key in registry.json."""
    from harness import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "REGISTRY_DIR", tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake")

    def _mock_clone(repo, dest):
        dest.mkdir(parents=True)
        (dest / "README.md").write_text("# Test Repo", encoding="utf-8")
        (dest / "agent.agent.md").write_text(
            "---\nname: TestAgent\n---\nYou are a test agent.", encoding="utf-8"
        )
        r = type("R", (), {"returncode": 0, "stderr": ""})()  # noqa: PIE807
        return r

    def _mock_sha(clone_dir):
        return "abc1234567890"

    monkeypatch.setattr(fetch_mod, "_git_clone", _mock_clone)
    monkeypatch.setattr(fetch_mod, "_get_head_sha", _mock_sha)

    fetch_mod.fetch_repo("owner/testrepo")

    registry_json = json.loads(
        (tmp_path / "owner" / "testrepo" / "registry.json").read_text()
    )
    assert "context" in registry_json
    assert "context/README.md" in registry_json["context"]
