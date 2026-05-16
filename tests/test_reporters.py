"""tests/test_reporters.py — unit tests for reporters/bench_report.py and reporters/github_issue.py."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

import reporters.bench_report as bench_report
import reporters.github_issue as github_issue

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_MINIMAL_RUN = {
    "run_id": "run-test-001",
    "agent": "Triage",
    "battery": "triage-core",
    "model": "gpt-4o",
    "isolation": "tmpdir",
    "source_repo": "testorg/testrepo",
    "tasks": [
        {
            "task": "triage-typo-rename",
            "status": "ok",
            "scores": {
                "dimensions": {
                    "format_valid": True,
                    "tier_correct": True,
                    "blocker_correct": True,
                },
                "total": 3,
                "max_score": 3,
            },
            "turn_count": 2,
            "prompt_tokens": 400,
            "completion_tokens": 120,
        },
        {
            "task": "triage-vague-blocked",
            "status": "ok",
            "scores": {
                "dimensions": {
                    "format_valid": True,
                    "tier_correct": False,
                    "quality": None,
                },
                "total": 1,
                "max_score": 3,
            },
            "turn_count": 3,
            "prompt_tokens": 500,
            "completion_tokens": 150,
        },
    ],
}


@pytest.fixture
def results_dir(tmp_path: Path, monkeypatch) -> Path:
    run_dir = tmp_path / "run-test-001"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(_MINIMAL_RUN), encoding="utf-8"
    )
    monkeypatch.setattr(bench_report, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(github_issue, "RESULTS_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# bench_report.render_report
# ---------------------------------------------------------------------------

def test_render_report_creates_file(results_dir):
    rc = bench_report.render_report("run-test-001")
    assert rc == 0
    assert (results_dir / "run-test-001" / "report.md").exists()


def test_render_report_contains_agent_and_tasks(results_dir):
    bench_report.render_report("run-test-001")
    report = (results_dir / "run-test-001" / "report.md").read_text(encoding="utf-8")
    assert "Triage" in report
    assert "triage-typo-rename" in report
    assert "triage-vague-blocked" in report


def test_render_report_pass_verdict(results_dir):
    bench_report.render_report("run-test-001")
    report = (results_dir / "run-test-001" / "report.md").read_text(encoding="utf-8")
    assert "✅ Pass" in report


def test_render_report_fail_verdict(results_dir):
    bench_report.render_report("run-test-001")
    report = (results_dir / "run-test-001" / "report.md").read_text(encoding="utf-8")
    assert "❌ Fail" in report


def test_render_report_pending_llm_dimension(results_dir):
    bench_report.render_report("run-test-001")
    report = (results_dir / "run-test-001" / "report.md").read_text(encoding="utf-8")
    assert "pending" in report


def test_render_report_missing_run(results_dir):
    rc = bench_report.render_report("run-does-not-exist")
    assert rc == 1


def test_render_report_errored_task(results_dir, tmp_path):
    run = {**_MINIMAL_RUN, "tasks": [
        {"task": "bad-task", "status": "provision_failed", "error": "docker not found"},
    ]}
    (tmp_path / "run-test-001" / "run.json").write_text(
        json.dumps(run), encoding="utf-8"
    )
    rc = bench_report.render_report("run-test-001")
    assert rc == 0
    report = (tmp_path / "run-test-001" / "report.md").read_text(encoding="utf-8")
    assert "bad-task" in report
    assert "Error" in report


# ---------------------------------------------------------------------------
# bench_report helpers
# ---------------------------------------------------------------------------

def test_pct_zero_max():
    assert bench_report._pct(0, 0) == "—"


def test_pct_calculation():
    assert bench_report._pct(3, 4) == "75%"


def test_verdict_pass():
    assert bench_report._verdict("80%") == "✅ Pass"


def test_verdict_marginal():
    assert bench_report._verdict("65%") == "⚠️ Marginal"


def test_verdict_fail():
    assert bench_report._verdict("40%") == "❌ Fail"


def test_verdict_na():
    assert bench_report._verdict("—") == "⏭️ n/a"


# ---------------------------------------------------------------------------
# github_issue.generate_issue
# ---------------------------------------------------------------------------

def test_generate_issue_creates_file(results_dir):
    rc = github_issue.generate_issue("run-test-001", submit=False, repo=None)
    assert rc == 0
    assert (results_dir / "run-test-001" / "issue.md").exists()


def test_generate_issue_contains_key_fields(results_dir):
    github_issue.generate_issue("run-test-001", submit=False, repo=None)
    issue = (results_dir / "run-test-001" / "issue.md").read_text(encoding="utf-8")
    assert "Triage" in issue
    assert "run-test-001" in issue
    assert "triage-core" in issue
    assert "testorg/testrepo" in issue


def test_generate_issue_uses_lab_repo_env(results_dir, monkeypatch):
    monkeypatch.setenv("LAB_REPO", "myorg/agent-lab")
    github_issue.generate_issue("run-test-001", submit=False, repo=None)
    issue = (results_dir / "run-test-001" / "issue.md").read_text(encoding="utf-8")
    assert "myorg/agent-lab" in issue


def test_generate_issue_missing_run(results_dir):
    rc = github_issue.generate_issue("run-missing", submit=False, repo=None)
    assert rc == 1


def test_generate_issue_submit_without_token(results_dir, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    rc = github_issue.generate_issue("run-test-001", submit=True, repo="testorg/testrepo")
    assert rc == 1


# ---------------------------------------------------------------------------
# github_issue helpers
# ---------------------------------------------------------------------------

def test_build_score_table_pass():
    tasks = [{"task": "t1", "status": "ok", "scores": {"total": 3, "max_score": 3}}]
    table = github_issue._build_score_table(tasks)
    assert "✅ Pass" in table
    assert "t1" in table


def test_build_score_table_fail():
    tasks = [{"task": "t1", "status": "ok", "scores": {"total": 0, "max_score": 3}}]
    table = github_issue._build_score_table(tasks)
    assert "❌ Fail" in table


def test_build_score_table_errored_task():
    tasks = [{"task": "t1", "status": "provision_failed"}]
    table = github_issue._build_score_table(tasks)
    assert "provision_failed" in table


def test_build_failing_section_all_pass():
    tasks = [
        {"task": "t1", "status": "ok",
         "scores": {"total": 3, "max_score": 3, "dimensions": {}}},
    ]
    result = github_issue._build_failing_section(tasks)
    assert "🎉" in result


def test_build_failing_section_shows_failed_dims():
    tasks = [
        {
            "task": "t1", "status": "ok",
            "scores": {"total": 0, "max_score": 3, "dimensions": {"tier_correct": False}},
        },
    ]
    result = github_issue._build_failing_section(tasks)
    assert "tier_correct" in result


def test_build_failing_section_shows_pending_dims():
    tasks = [
        {
            "task": "t1", "status": "ok",
            "scores": {"total": 1, "max_score": 2, "dimensions": {"quality": None}},
        },
    ]
    result = github_issue._build_failing_section(tasks)
    assert "quality" in result
    assert "Pending" in result or "pending" in result
