"""tests/test_score.py — unit tests for harness/score.py."""
from __future__ import annotations

from harness.score import score


def _result(response: str = "", diff: str = "") -> dict:
    return {"final_response": response, "workspace_diff": diff}


def _task(*criteria: dict) -> dict:
    return {"scoring_criteria": list(criteria)}


# ---------------------------------------------------------------------------
# keyword
# ---------------------------------------------------------------------------

def test_keyword_hit():
    task = _task({"dimension": "fmt", "type": "keyword", "terms": ["Tier:"]})
    s = score(task, _result("Tier: Simple"))
    assert s["dimensions"]["fmt"] is True
    assert s["total"] == 1
    assert s["max_score"] == 1


def test_keyword_miss():
    task = _task({"dimension": "fmt", "type": "keyword", "terms": ["Tier:"]})
    s = score(task, _result("no match here"))
    assert s["dimensions"]["fmt"] is False
    assert s["total"] == 0


def test_keyword_case_insensitive():
    task = _task({"dimension": "fmt", "type": "keyword", "terms": ["TIER:"]})
    s = score(task, _result("tier: Simple"))
    assert s["dimensions"]["fmt"] is True


def test_keyword_any_term_sufficient():
    task = _task({"dimension": "tier", "type": "keyword", "terms": ["Trivial", "Simple"]})
    s = score(task, _result("Tier: Simple"))
    assert s["dimensions"]["tier"] is True


# ---------------------------------------------------------------------------
# keyword_absent
# ---------------------------------------------------------------------------

def test_keyword_absent_pass():
    task = _task({"dimension": "no_blocked", "type": "keyword_absent", "terms": ["Blocked"]})
    s = score(task, _result("Tier: Simple"))
    assert s["dimensions"]["no_blocked"] is True
    assert s["total"] == 1


def test_keyword_absent_fail():
    task = _task({"dimension": "no_blocked", "type": "keyword_absent", "terms": ["Blocked"]})
    s = score(task, _result("Tier: Blocked — missing info"))
    assert s["dimensions"]["no_blocked"] is False
    assert s["total"] == 0


# ---------------------------------------------------------------------------
# diff_contains
# ---------------------------------------------------------------------------

def test_diff_contains_pass():
    task = _task({"dimension": "renamed", "type": "diff_contains", "terms": ["calculate_total"]})
    s = score(task, _result(diff="+def calculate_total():"))
    assert s["dimensions"]["renamed"] is True
    assert s["total"] == 1


def test_diff_contains_fail():
    task = _task({"dimension": "renamed", "type": "diff_contains", "terms": ["calculate_total"]})
    s = score(task, _result(diff=""))
    assert s["dimensions"]["renamed"] is False


def test_diff_contains_requires_all_terms():
    task = _task({"dimension": "both", "type": "diff_contains", "terms": ["foo", "bar"]})
    s = score(task, _result(diff="foo only"))
    assert s["dimensions"]["both"] is False


# ---------------------------------------------------------------------------
# diff_absent
# ---------------------------------------------------------------------------

def test_diff_absent_pass():
    task = _task({"dimension": "no_rm", "type": "diff_absent", "terms": ["-def old_fn"]})
    s = score(task, _result(diff="+def new_fn():"))
    assert s["dimensions"]["no_rm"] is True


def test_diff_absent_fail():
    task = _task({"dimension": "no_rm", "type": "diff_absent", "terms": ["-def old_fn"]})
    s = score(task, _result(diff="-def old_fn():"))
    assert s["dimensions"]["no_rm"] is False


# ---------------------------------------------------------------------------
# llm (deferred)
# ---------------------------------------------------------------------------

def test_llm_dimension_is_none():
    task = _task({"dimension": "quality", "type": "llm", "criterion": "Was the response good?"})
    s = score(task, _result("some response"))
    assert s["dimensions"]["quality"] is None
    assert s["total"] == 0
    assert s["max_score"] == 1  # still counts toward max


# ---------------------------------------------------------------------------
# unknown type
# ---------------------------------------------------------------------------

def test_unknown_type_skipped():
    task = _task({"dimension": "x", "type": "unknown_type", "terms": []})
    s = score(task, _result("anything"))
    assert s["max_score"] == 0
    assert "x" not in s["dimensions"]


# ---------------------------------------------------------------------------
# multiple criteria
# ---------------------------------------------------------------------------

def test_multiple_criteria_all_pass():
    task = _task(
        {"dimension": "fmt",      "type": "keyword",         "terms": ["Tier:"]},
        {"dimension": "tier",     "type": "keyword",         "terms": ["Simple"]},
        {"dimension": "no_block", "type": "keyword_absent",  "terms": ["Blocked"]},
    )
    s = score(task, _result("Tier: Simple"))
    assert s["total"] == 3
    assert s["max_score"] == 3
    assert all(v is True for v in s["dimensions"].values())


def test_multiple_criteria_partial():
    task = _task(
        {"dimension": "fmt",  "type": "keyword", "terms": ["Tier:"]},
        {"dimension": "tier", "type": "keyword", "terms": ["Compound"]},
    )
    s = score(task, _result("Tier: Simple"))
    assert s["total"] == 1
    assert s["max_score"] == 2
    assert s["dimensions"]["fmt"] is True
    assert s["dimensions"]["tier"] is False


def test_empty_criteria():
    s = score(_task(), _result("anything"))
    assert s["total"] == 0
    assert s["max_score"] == 0
    assert s["dimensions"] == {}
