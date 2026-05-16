"""Tests for harness/validate.py."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.validate import (
    _find_battery_file,
    _workspace_exists,
    validate_battery,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_battery(tmp: Path, name: str = "test-battery", **overrides) -> Path:
    """Write a minimal valid battery.json and return the path."""
    battery_dir = tmp / name
    ws_dir = battery_dir / "workspaces" / "blank"
    ws_dir.mkdir(parents=True)

    battery = {
        "name": name,
        "version": "1.0.0",
        "target_agents": ["TestAgent"],
        "tasks": [
            {
                "name": "task-1",
                "workspace": "blank",
                "prompt": "Do something",
                "notes": "A note",
                "scoring_criteria": [
                    {"dimension": "responds", "type": "keyword", "terms": ["done"]},
                ],
            }
        ],
    }
    battery.update(overrides)
    path = battery_dir / "battery.json"
    path.write_text(json.dumps(battery), encoding="utf-8")
    return path


# ── _find_battery_file ────────────────────────────────────────────────────────

def test_find_battery_file_by_path(tmp_path):
    f = tmp_path / "battery.json"
    f.write_text("{}", encoding="utf-8")
    assert _find_battery_file(str(f)) == f


def test_find_battery_file_by_name(tmp_path):
    candidate = tmp_path / "my-battery" / "battery.json"
    candidate.parent.mkdir()
    candidate.write_text("{}", encoding="utf-8")
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        assert _find_battery_file("my-battery") == candidate


def test_find_battery_file_not_found(tmp_path):
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        with pytest.raises(FileNotFoundError):
            _find_battery_file("nonexistent")


# ── _workspace_exists ─────────────────────────────────────────────────────────

def test_workspace_exists_local(tmp_path):
    ws = tmp_path / "workspaces" / "blank"
    ws.mkdir(parents=True)
    assert _workspace_exists(tmp_path, "blank") is True


def test_workspace_missing(tmp_path):
    assert _workspace_exists(tmp_path, "nonexistent") is False


def test_workspace_exists_in_core(tmp_path):
    core_ws = tmp_path / "core" / "workspaces" / "shared"
    core_ws.mkdir(parents=True)
    battery_dir = tmp_path / "my-battery"
    battery_dir.mkdir()
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        assert _workspace_exists(battery_dir, "shared") is True


# ── validate_battery — happy path ─────────────────────────────────────────────

def test_valid_battery(tmp_path, capsys):
    f = _make_battery(tmp_path)
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 0
    out = capsys.readouterr().out
    assert "✓" in out
    assert "1 task(s) valid" in out


def test_valid_battery_by_name(tmp_path, capsys):
    _make_battery(tmp_path, "mytest")
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery("mytest")
    assert rc == 0


# ── validate_battery — missing top-level fields ───────────────────────────────

def test_missing_name(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    del data["name"]
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "name" in capsys.readouterr().out


def test_empty_target_agents(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["target_agents"] = []
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "target_agents" in capsys.readouterr().out


def test_missing_tasks(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"] = []
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1


# ── validate_battery — task-level errors ─────────────────────────────────────

def test_missing_workspace(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"][0]["workspace"] = "does-not-exist"
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "workspace" in capsys.readouterr().out


def test_empty_prompt(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"][0]["prompt"] = "   "
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "prompt" in capsys.readouterr().out


def test_duplicate_task_names(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"].append(dict(data["tasks"][0]))  # duplicate
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "duplicate" in capsys.readouterr().out


# ── validate_battery — criterion errors ───────────────────────────────────────

def test_invalid_criterion_type(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"][0]["scoring_criteria"][0]["type"] = "regex"
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "invalid" in capsys.readouterr().out.lower()


def test_keyword_missing_terms(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    del data["tasks"][0]["scoring_criteria"][0]["terms"]
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "terms" in capsys.readouterr().out


def test_llm_missing_criterion_text(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"][0]["scoring_criteria"][0] = {
        "dimension": "quality",
        "type": "llm",
    }
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "criterion" in capsys.readouterr().out


def test_llm_valid_criterion(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"][0]["scoring_criteria"][0] = {
        "dimension": "quality",
        "type": "llm",
        "criterion": "Did the agent explain its reasoning?",
    }
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 0


def test_duplicate_dimension(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    data["tasks"][0]["scoring_criteria"].append(
        {"dimension": "responds", "type": "keyword", "terms": ["yes"]}
    )
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 1
    assert "duplicate" in capsys.readouterr().out


# ── validate_battery — warnings (non-fatal) ───────────────────────────────────

def test_missing_version_is_warning_not_error(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    del data["version"]
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 0
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "version" in out


def test_missing_notes_is_warning(tmp_path, capsys):
    f = _make_battery(tmp_path)
    data = json.loads(f.read_text())
    del data["tasks"][0]["notes"]
    f.write_text(json.dumps(data))
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(f))
    assert rc == 0
    out = capsys.readouterr().out
    assert "notes" in out


# ── battery file not found ────────────────────────────────────────────────────

def test_battery_file_not_found(tmp_path, capsys):
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery("does-not-exist")
    assert rc == 1
    assert "not found" in capsys.readouterr().out


def test_invalid_json(tmp_path, capsys):
    f = tmp_path / "battery.json"
    f.write_text("{ not valid json", encoding="utf-8")
    rc = validate_battery(str(f))
    assert rc == 1
    assert "invalid JSON" in capsys.readouterr().out
