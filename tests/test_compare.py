"""Tests for reporters/compare.py and bench_report._grade."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from reporters.bench_report import _grade
from reporters.compare import _diff_tasks, _task_index, compare_runs


# ── _grade ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pct_str,expected", [
    ("95%", "A"),
    ("90%", "A"),
    ("89%", "B"),
    ("80%", "B"),
    ("79%", "C"),
    ("70%", "C"),
    ("69%", "D"),
    ("60%", "D"),
    ("59%", "F"),
    ("0%",  "F"),
    ("—",   "?"),
])
def test_grade_thresholds(pct_str, expected):
    assert _grade(pct_str) == expected


# ── _task_index ───────────────────────────────────────────────────────────────

def test_task_index_includes_only_ok():
    run = {
        "tasks": [
            {"task": "a", "status": "ok", "scores": {"dimensions": {"x": True}}},
            {"task": "b", "status": "run_failed", "scores": {}},
        ]
    }
    idx = _task_index(run)
    assert list(idx.keys()) == ["a"]


# ── _diff_tasks ───────────────────────────────────────────────────────────────

def _make_task(name: str, dims: dict) -> dict:
    return {"task": name, "status": "ok", "scores": {"dimensions": dims, "total": 0, "max_score": 0}}


def test_diff_same_pass():
    a = {"t": _make_task("t", {"x": True})}
    b = {"t": _make_task("t", {"x": True})}
    lines = _diff_tasks(a, b)
    assert any("same pass" in ln for ln in lines)


def test_diff_improved():
    a = {"t": _make_task("t", {"x": False})}
    b = {"t": _make_task("t", {"x": True})}
    lines = _diff_tasks(a, b)
    assert any("improved" in ln for ln in lines)


def test_diff_regressed():
    a = {"t": _make_task("t", {"x": True})}
    b = {"t": _make_task("t", {"x": False})}
    lines = _diff_tasks(a, b)
    assert any("regressed" in ln for ln in lines)


def test_diff_added_dim():
    a = {"t": _make_task("t", {})}
    b = {"t": _make_task("t", {"x": True})}
    lines = _diff_tasks(a, b)
    assert any("added" in ln for ln in lines)


def test_diff_removed_dim():
    a = {"t": _make_task("t", {"x": True})}
    b = {"t": _make_task("t", {})}
    lines = _diff_tasks(a, b)
    assert any("removed" in ln for ln in lines)


def test_diff_task_only_in_a():
    a = {"t": _make_task("t", {"x": True})}
    b: dict = {}
    lines = _diff_tasks(a, b)
    assert any("only in run A" in ln for ln in lines)


def test_diff_task_only_in_b():
    a: dict = {}
    b = {"t": _make_task("t", {"x": True})}
    lines = _diff_tasks(a, b)
    assert any("only in run B" in ln for ln in lines)


# ── compare_runs integration ──────────────────────────────────────────────────

def _write_run(run_dir: Path, run_id: str, tasks: list[dict]) -> None:
    run_dir.mkdir(parents=True)
    run = {
        "run_id": run_id,
        "agent": "test-agent",
        "battery": "test-battery",
        "model": "openai/gpt-4o-mini",
        "isolation": "tmpdir",
        "started_at": "20260101-000000",
        "tasks": tasks,
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")


def test_compare_runs_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        run_a = "run-a"
        run_b = "run-b"
        _write_run(tmp_path / run_a, run_a, [
            {"task": "t1", "status": "ok", "scores": {"dimensions": {"x": True}, "total": 1, "max_score": 1}},
        ])
        _write_run(tmp_path / run_b, run_b, [
            {"task": "t1", "status": "ok", "scores": {"dimensions": {"x": False}, "total": 0, "max_score": 1}},
        ])

        with patch("reporters.compare.RESULTS_DIR", tmp_path):
            rc = compare_runs(run_a, run_b)

        assert rc == 0
        out = (tmp_path / f"compare-{run_a}-vs-{run_b}.md").read_text()
        assert "regressed" in out
        assert "-100%" in out or "−" in out or "delta" in out.lower()


def test_compare_runs_missing_run():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("reporters.compare.RESULTS_DIR", tmp_path):
            rc = compare_runs("nonexistent-a", "nonexistent-b")
        assert rc == 1
