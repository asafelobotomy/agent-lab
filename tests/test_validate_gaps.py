"""Gap tests for harness/validate.py — covers lines 87, 95, 108, 117, 137."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from harness.validate import validate_battery


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_battery(tmp: Path, *, tasks: list | None = None) -> Path:
    battery_dir = tmp / "gap-battery"
    ws_dir = battery_dir / "workspaces" / "blank"
    ws_dir.mkdir(parents=True)
    battery = {
        "name": "gap-battery",
        "tasks": tasks if tasks is not None else [
            {
                "name": "task-1",
                "workspace": "blank",
                "prompt": "Do it",
                "scoring_criteria": [
                    {"dimension": "responds", "type": "keyword", "terms": ["done"]},
                ],
            }
        ],
    }
    path = battery_dir / "battery.json"
    path.write_text(json.dumps(battery), encoding="utf-8")
    return path


# ── line 87: empty task name ──────────────────────────────────────────────────

def test_empty_task_name(tmp_path, capsys):
    """Task with empty name string triggers 'name is missing or empty' error."""
    path = _make_battery(tmp_path, tasks=[
        {
            "name": "",           # empty name → hits line 87
            "workspace": "blank",
            "prompt": "Do it",
            "scoring_criteria": [{"dimension": "x", "type": "keyword", "terms": ["y"]}],
        }
    ])
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(path))
    assert rc == 1
    out = capsys.readouterr().out
    assert "missing or empty" in out


# ── line 95: empty workspace string ──────────────────────────────────────────

def test_empty_workspace_string(tmp_path, capsys):
    """Task with workspace='' (empty string) triggers 'workspace is missing or empty'."""
    path = _make_battery(tmp_path, tasks=[
        {
            "name": "task-ws-empty",
            "workspace": "",      # empty workspace → hits line 95
            "prompt": "Do it",
            "scoring_criteria": [{"dimension": "x", "type": "keyword", "terms": ["y"]}],
        }
    ])
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(path))
    assert rc == 1
    out = capsys.readouterr().out
    assert "workspace" in out and "missing or empty" in out


# ── line 108: empty scoring_criteria list ────────────────────────────────────

def test_empty_scoring_criteria_list(tmp_path, capsys):
    """Task with scoring_criteria=[] triggers 'must be a non-empty list' error."""
    path = _make_battery(tmp_path, tasks=[
        {
            "name": "task-empty-criteria",
            "workspace": "blank",
            "prompt": "Do it",
            "scoring_criteria": [],     # empty list → hits line 108
        }
    ])
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(path))
    assert rc == 1
    out = capsys.readouterr().out
    assert "non-empty list" in out


def test_non_list_scoring_criteria(tmp_path, capsys):
    """Task with scoring_criteria as non-list also triggers line 108."""
    path = _make_battery(tmp_path, tasks=[
        {
            "name": "task-bad-criteria",
            "workspace": "blank",
            "prompt": "Do it",
            "scoring_criteria": "not-a-list",     # not a list → hits line 108
        }
    ])
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(path))
    assert rc == 1
    out = capsys.readouterr().out
    assert "non-empty list" in out


# ── line 117: empty dimension in criterion ────────────────────────────────────

def test_missing_dimension_in_criterion(tmp_path, capsys):
    """Criterion with empty dimension string triggers 'dimension is missing or empty'."""
    path = _make_battery(tmp_path, tasks=[
        {
            "name": "task-no-dim",
            "workspace": "blank",
            "prompt": "Do it",
            "scoring_criteria": [
                {"dimension": "", "type": "keyword", "terms": ["done"]},   # empty dim → line 117
            ],
        }
    ])
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(path))
    assert rc == 1
    out = capsys.readouterr().out
    assert "dimension" in out and "missing or empty" in out


# ── line 137: non-string values in terms ─────────────────────────────────────

def test_terms_non_string_values(tmp_path, capsys):
    """Criterion with terms=[1, 2, 3] (integers) triggers 'all terms entries must be strings'."""
    path = _make_battery(tmp_path, tasks=[
        {
            "name": "task-bad-terms",
            "workspace": "blank",
            "prompt": "Do it",
            "scoring_criteria": [
                {"dimension": "quality", "type": "keyword", "terms": [1, 2, 3]},   # ints → line 137
            ],
        }
    ])
    with patch("harness.validate.BATTERIES_DIR", tmp_path):
        rc = validate_battery(str(path))
    assert rc == 1
    out = capsys.readouterr().out
    assert "strings" in out
