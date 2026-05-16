"""Gap tests for reporters — bench_report F/D grade, compare edge cases, github_issue submit paths."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import urllib.error


from reporters.bench_report import _grade, render_report
from reporters.compare import compare_runs, _diff_tasks, _pct
import reporters.compare as compare_mod
import reporters.github_issue as github_issue
import reporters.bench_report as bench_report_mod


# ── bench_report._grade D and F ───────────────────────────────────────────────

def test_grade_D():
    assert _grade("65%") == "D"


def test_grade_F():
    assert _grade("40%") == "F"


def test_grade_F_zero():
    assert _grade("0%") == "F"


def test_grade_D_boundary():
    assert _grade("60%") == "D"


# ── compare.py — _diff_tasks dim-only-in-A and dim-only-in-B ─────────────────

def _make_task(name: str, dims: dict) -> dict:
    return {
        "task": name,
        "status": "ok",
        "scores": {
            "dimensions": dims,
            "total": sum(1 for v in dims.values() if v is True),
            "max_score": len(dims),
        },
    }


def test_diff_tasks_dim_only_in_a():
    """Dimension exists in A but not B → should show 'removed' symbol."""
    a = {"t1": _make_task("t1", {"removed_dim": True})}
    b = {"t1": _make_task("t1", {})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "removed" in joined.lower() or "➖" in joined


def test_diff_tasks_dim_only_in_b():
    """Dimension exists in B but not A → should show 'added' symbol."""
    a = {"t1": _make_task("t1", {})}
    b = {"t1": _make_task("t1", {"new_dim": True})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "added" in joined.lower() or "➕" in joined


def test_diff_tasks_regressed():
    """Dimension went from True (A) to False (B) → 'regressed'."""
    a = {"t1": _make_task("t1", {"quality": True})}
    b = {"t1": _make_task("t1", {"quality": False})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "regressed" in joined.lower() or "📉" in joined


def test_diff_tasks_improved():
    """Dimension went from False (A) to True (B) → 'improved'."""
    a = {"t1": _make_task("t1", {"quality": False})}
    b = {"t1": _make_task("t1", {"quality": True})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "improved" in joined.lower() or "📈" in joined


def test_diff_tasks_same_null():
    a = {"t1": _make_task("t1", {"quality": None})}
    b = {"t1": _make_task("t1", {"quality": None})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "⏳" in joined or "null" in joined.lower()


def test_diff_tasks_task_only_in_a():
    a = {"t1": _make_task("t1", {"x": True})}
    b: dict = {}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "only in run A" in joined


def test_diff_tasks_task_only_in_b():
    a: dict = {}
    b = {"t1": _make_task("t1", {"x": True})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "only in run B" in joined


# ── compare_runs — FileNotFoundError ─────────────────────────────────────────

def test_compare_runs_missing_run(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        with patch("reporters.compare.RESULTS_DIR", Path(tmp)):
            rc = compare_runs("nonexistent-a", "nonexistent-b")
    assert rc == 1


def test_compare_runs_output_written(tmp_path):
    """compare_runs should write a compare-*.md file and return 0."""
    def _write_run(run_id: str, tasks: list[dict]) -> None:
        d = tmp_path / run_id
        d.mkdir()
        run = {"run_id": run_id, "agent": "A", "battery": "b", "model": "m",
               "isolation": "t", "started_at": "x", "tasks": tasks}
        (d / "run.json").write_text(json.dumps(run))

    _write_run("run-a", [
        {"task": "t1", "status": "ok",
         "scores": {"dimensions": {"x": True}, "total": 1, "max_score": 1}},
    ])
    _write_run("run-b", [
        {"task": "t1", "status": "ok",
         "scores": {"dimensions": {"x": False}, "total": 0, "max_score": 1}},
    ])
    with patch("reporters.compare.RESULTS_DIR", tmp_path):
        rc = compare_runs("run-a", "run-b")
    assert rc == 0
    assert (tmp_path / "compare-run-a-vs-run-b.md").exists()


# ── github_issue — _submit_issue paths ───────────────────────────────────────

def test_submit_issue_no_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    rc = github_issue._submit_issue("org/repo", "title", "body")
    assert rc == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_submit_issue_success(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    fake_resp_data = json.dumps({"html_url": "https://github.com/org/repo/issues/42"}).encode()

    class _MockResp:
        def read(self): return fake_resp_data
        def __enter__(self): return self
        def __exit__(self, *_): pass

    with patch("reporters.github_issue.urllib.request.urlopen", return_value=_MockResp()):
        rc = github_issue._submit_issue("org/repo", "title", "body")
    assert rc == 0
    out = capsys.readouterr().out
    assert "42" in out


def test_submit_issue_http_error(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    class _FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://x", 403, "Forbidden", {}, None)
        def read(self): return b"not allowed"

    with patch("reporters.github_issue.urllib.request.urlopen", side_effect=_FakeHTTPError()):
        rc = github_issue._submit_issue("org/repo", "title", "body")
    assert rc == 1
    err = capsys.readouterr().err
    assert "403" in err


# ── github_issue.generate_issue — submit=True paths ──────────────────────────

def _setup_results(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-gi-001"
    run_dir.mkdir()
    run = {
        "run_id": "run-gi-001",
        "agent": "Triage",
        "battery": "triage-core",
        "model": "gpt-4o",
        "isolation": "tmpdir",
        "source_repo": "testorg/testrepo",
        "tasks": [{
            "task": "t1",
            "status": "ok",
            "scores": {"total": 3, "max_score": 3, "dimensions": {"x": True}},
        }],
    }
    (run_dir / "run.json").write_text(json.dumps(run))


def test_generate_issue_submit_no_repo(tmp_path, monkeypatch, capsys):
    """submit=True without --repo and without source_repo → error."""
    run_dir = tmp_path / "run-norepo"
    run_dir.mkdir()
    run = {
        "run_id": "run-norepo",
        "agent": "X",
        "battery": "b",
        "model": "m",
        "isolation": "t",
        "tasks": [],
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    monkeypatch.setattr(github_issue, "RESULTS_DIR", tmp_path)
    rc = github_issue.generate_issue("run-norepo", submit=True, repo=None)
    assert rc == 1
    assert "Specify" in capsys.readouterr().err


def test_generate_issue_submit_true_success(tmp_path, monkeypatch, capsys):
    _setup_results(tmp_path)
    monkeypatch.setattr(github_issue, "RESULTS_DIR", tmp_path)

    fake_resp_data = json.dumps({"html_url": "https://github.com/testorg/testrepo/issues/1"}).encode()

    class _MockResp:
        def read(self): return fake_resp_data
        def __enter__(self): return self
        def __exit__(self, *_): pass

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    with patch("reporters.github_issue.urllib.request.urlopen", return_value=_MockResp()):
        rc = github_issue.generate_issue("run-gi-001", submit=True, repo="testorg/testrepo")
    assert rc == 0


def test_generate_issue_no_report_md_uses_placeholder(tmp_path, monkeypatch):
    """When report.md doesn't exist, a placeholder text is used."""
    _setup_results(tmp_path)
    monkeypatch.setattr(github_issue, "RESULTS_DIR", tmp_path)
    rc = github_issue.generate_issue("run-gi-001", submit=False, repo=None)
    assert rc == 0
    body = (tmp_path / "run-gi-001" / "issue.md").read_text()
    # The placeholder text should be present (report not yet generated)
    assert "report" in body.lower() or "Triage" in body


def test_build_failing_section_no_failed_dims(tmp_path, monkeypatch):
    """Task with all passed dims and no pending → 'All tasks passed'."""
    tasks = [
        {
            "task": "t1", "status": "ok",
            "scores": {"total": 3, "max_score": 3, "dimensions": {"x": True, "y": True}},
        },
    ]
    result = github_issue._build_failing_section(tasks)
    assert "🎉" in result


def test_build_recommendations_no_failures(tmp_path):
    """No structural failures → generic message."""
    tasks = [
        {
            "task": "t1", "status": "ok",
            "scores": {"total": 3, "max_score": 3, "dimensions": {"x": True}},
        },
    ]
    result = github_issue._build_recommendations(tasks, "Triage")
    assert "No structural" in result


# ── bench_report.py lines 138-139: task with no dims ─────────────────────────

def test_render_report_task_no_dims(tmp_path):
    """ok task with empty dimensions dict hits 'no dimensions scored' path."""
    run_id = "run-nodims"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id,
        "agent": "Test",
        "battery": "b",
        "model": "m",
        "isolation": "tmpdir",
        "tasks": [{
            "task": "t1",
            "status": "ok",
            "scores": {"dimensions": {}, "total": 0, "max_score": 0},
            "turn_count": 1,
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_latency_ms": 100,
        }],
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    with patch.object(bench_report_mod, "RESULTS_DIR", tmp_path):
        rc = render_report(run_id)
    assert rc == 0
    report = (run_dir / "report.md").read_text()
    assert "no dimensions scored" in report


# ── compare.py line 37: _pct with zero max_score ─────────────────────────────

def test_pct_zero_max_score():
    """_pct(total, 0) returns '—'."""
    assert _pct(0, 0) == "—"
    assert _pct(5, 0) == "—"


# ── compare.py lines 76-78: both tasks exist but no dims ─────────────────────

def test_diff_tasks_both_no_dims():
    """Both tasks exist but have no dims → 'no dimensions scored' path."""
    a = {"t1": _make_task("t1", {})}
    b = {"t1": _make_task("t1", {})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    assert "no dimensions scored" in joined


# ── compare.py line 97: both dims False ──────────────────────────────────────

def test_diff_tasks_both_false():
    """Both A and B have False → same_fail symbol."""
    a = {"t1": _make_task("t1", {"quality": False})}
    b = {"t1": _make_task("t1", {"quality": False})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    # same_fail symbol or some indicator both failed
    assert any(s in joined for s in ("same_fail", "🔴", "❌", "➡️"))


# ── compare.py line 105: else branch (False→None or None→False) ──────────────

def test_diff_tasks_false_to_none():
    """A=False, B=None → else branch (f'{a_icon} → {b_icon}')."""
    a = {"t1": _make_task("t1", {"quality": False})}
    b = {"t1": _make_task("t1", {"quality": None})}
    lines = _diff_tasks(a, b)
    joined = "\n".join(lines)
    # Should contain some icon transition
    assert "❌" in joined or "⏳" in joined


# ── compare.py line 137: delta_str = '—' when max_score == 0 ─────────────────

def test_compare_runs_zero_max_score(tmp_path):
    """When tasks have max_score=0, _pct returns '—' and delta_str is '—'."""
    def _write_run(run_id: str, tasks: list) -> None:
        d = tmp_path / run_id
        d.mkdir()
        run = {"run_id": run_id, "agent": "A", "battery": "b", "model": "m",
               "isolation": "t", "started_at": "x", "tasks": tasks}
        (d / "run.json").write_text(json.dumps(run))

    _write_run("run-zero-a", [
        {"task": "t1", "status": "ok",
         "scores": {"dimensions": {}, "total": 0, "max_score": 0}},
    ])
    _write_run("run-zero-b", [
        {"task": "t1", "status": "ok",
         "scores": {"dimensions": {}, "total": 0, "max_score": 0}},
    ])
    with patch.object(compare_mod, "RESULTS_DIR", tmp_path):
        rc = compare_runs("run-zero-a", "run-zero-b")
    assert rc == 0
    compare_file = tmp_path / "compare-run-zero-a-vs-run-zero-b.md"
    assert compare_file.exists()
    content = compare_file.read_text()
    assert "—" in content


# ── github_issue.py line 71: _load_report_md with existing report.md ─────────

def test_load_report_md_exists(tmp_path, monkeypatch):
    """generate_issue reads existing report.md → covers line 71."""
    run_id = "run-with-report"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "run_id": run_id, "agent": "X", "battery": "b", "model": "m",
        "isolation": "t",
        "tasks": [{"task": "t1", "status": "ok",
                   "scores": {"total": 1, "max_score": 1, "dimensions": {"x": True}}}],
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    (run_dir / "report.md").write_text("# Report\n\nSome content.")
    monkeypatch.setattr(github_issue, "RESULTS_DIR", tmp_path)
    rc = github_issue.generate_issue(run_id, submit=False, repo=None)
    assert rc == 0
    issue_body = (run_dir / "issue.md").read_text()
    assert "Some content" in issue_body


# ── github_issue.py lines 108-109: _build_failing_section with non-ok task ───

def test_build_failing_section_non_ok_task():
    """A task with status='provision_failed' is included via non-ok branch."""
    tasks = [
        {
            "task": "t1",
            "status": "provision_failed",
            "error": "Docker not found",
            "scores": {},
        },
    ]
    result = github_issue._build_failing_section(tasks)
    assert "provision_failed" in result
    assert "Docker not found" in result


# ── github_issue.py line 131: _build_recommendations with non-ok task ─────────

def test_build_recommendations_non_ok_task():
    """Non-ok task is skipped in _build_recommendations via continue (line 131)."""
    tasks = [
        {"task": "t1", "status": "provision_failed", "error": "x", "scores": {}},
        {"task": "t2", "status": "ok", "scores": {"dimensions": {}, "total": 0, "max_score": 1}},
    ]
    result = github_issue._build_recommendations(tasks, "Agent")
    # t1 is skipped (non-ok), t2 has no failed dims → "No structural" message
    assert "No structural" in result

