"""Tests for bench.py multi-seed helpers: _wilson_ci and _aggregate_seeds."""

from harness.bench import _aggregate_seeds, _wilson_ci


# ── _wilson_ci ────────────────────────────────────────────────────────────────

def test_wilson_ci_all_pass():
    lo, hi = _wilson_ci(10, 10)
    assert lo > 0.69  # well above 0
    assert hi == 1.0


def test_wilson_ci_none_pass():
    lo, hi = _wilson_ci(0, 10)
    assert lo == 0.0
    assert hi < 0.31  # well below 1


def test_wilson_ci_half():
    lo, hi = _wilson_ci(5, 10)
    assert lo < 0.5 < hi  # interval straddles 50%


def test_wilson_ci_zero_n():
    lo, hi = _wilson_ci(0, 0)
    assert lo == 0.0
    assert hi == 1.0


def test_wilson_ci_bounds():
    for passes in range(6):
        lo, hi = _wilson_ci(passes, 5)
        assert 0.0 <= lo <= hi <= 1.0


# ── _aggregate_seeds ──────────────────────────────────────────────────────────

def _make_scores(dims: dict, total: int, max_score: int) -> dict:
    return {"dimensions": dims, "total": total, "max_score": max_score}


def _make_result(turns: int = 2, prompt_tokens: int = 100, completion_tokens: int = 50) -> dict:
    return {
        "turn_count": turns,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_latency_ms": 1000,
    }


def test_aggregate_empty():
    result = _aggregate_seeds([], [])
    assert result["status"] == "run_failed"
    assert result["scores"]["total"] == 0


def test_aggregate_single_seed_pass():
    scores = [_make_scores({"dim_a": True}, 1, 1)]
    results = [_make_result()]
    agg = _aggregate_seeds(scores, results)
    assert agg["status"] == "ok"
    assert agg["scores"]["dimensions"]["dim_a"] is True
    assert agg["scores"]["seeds"] == 1
    assert agg["scores"]["pass_rates"]["dim_a"]["pass"] == 1
    assert agg["scores"]["pass_rates"]["dim_a"]["rate"] == 1.0


def test_aggregate_majority_vote_pass():
    # 3 pass, 1 fail → majority True
    scores = [
        _make_scores({"x": True}, 1, 1),
        _make_scores({"x": True}, 1, 1),
        _make_scores({"x": True}, 1, 1),
        _make_scores({"x": False}, 0, 1),
    ]
    results = [_make_result() for _ in scores]
    agg = _aggregate_seeds(scores, results)
    assert agg["scores"]["dimensions"]["x"] is True
    assert agg["scores"]["pass_rates"]["x"]["pass"] == 3
    assert agg["scores"]["pass_rates"]["x"]["fail"] == 1
    assert agg["scores"]["seeds"] == 4


def test_aggregate_majority_vote_fail():
    # 1 pass, 3 fail → majority False
    scores = [
        _make_scores({"x": True}, 1, 1),
        _make_scores({"x": False}, 0, 1),
        _make_scores({"x": False}, 0, 1),
        _make_scores({"x": False}, 0, 1),
    ]
    results = [_make_result() for _ in scores]
    agg = _aggregate_seeds(scores, results)
    assert agg["scores"]["dimensions"]["x"] is False


def test_aggregate_null_keeps_dim_null():
    # Any null → dimension stays null (pending LLM judge)
    scores = [
        _make_scores({"x": True}, 1, 1),
        _make_scores({"x": None}, 0, 1),
    ]
    results = [_make_result() for _ in scores]
    agg = _aggregate_seeds(scores, results)
    assert agg["scores"]["dimensions"]["x"] is None
    assert agg["scores"]["pass_rates"]["x"]["null"] == 1


def test_aggregate_mean_total():
    scores = [
        _make_scores({"x": True}, 3, 4),
        _make_scores({"x": False}, 1, 4),
    ]
    results = [_make_result() for _ in scores]
    agg = _aggregate_seeds(scores, results)
    assert agg["scores"]["total"] == 2  # round((3+1)/2)
    assert agg["scores"]["max_score"] == 4


def test_aggregate_token_sum():
    scores = [_make_scores({"x": True}, 1, 1), _make_scores({"x": True}, 1, 1)]
    results = [
        _make_result(prompt_tokens=100, completion_tokens=50),
        _make_result(prompt_tokens=200, completion_tokens=80),
    ]
    agg = _aggregate_seeds(scores, results)
    assert agg["prompt_tokens"] == 300   # summed
    assert agg["completion_tokens"] == 130  # summed
    assert agg["turn_count"] == 2  # mean(2,2) = 2


def test_aggregate_ci_bounds_in_range():
    scores = [_make_scores({"x": True}, 1, 1)] * 5
    results = [_make_result() for _ in scores]
    agg = _aggregate_seeds(scores, results)
    pr = agg["scores"]["pass_rates"]["x"]
    assert 0.0 <= pr["ci_low"] <= pr["ci_high"] <= 1.0


# ── retry_start in _run_task_loop ─────────────────────────────────────────────

def test_retry_start_written_when_is_retry(tmp_path):
    """When is_retry=True, a retry_start event must appear at the start of the task log."""
    import json
    from harness.bench import _run_task_loop
    from harness.log import TaskLogger

    # Minimal stubs
    def _provision(battery_name, workspace_name, *, use_docker):
        class _FakeWS:
            diff = ""
            def destroy(self): pass
        return _FakeWS()

    def _run_task(*, agent_system_prompt, task, workspace, model,
                  agent_tool_surfaces, logger):
        return {
            "final_response": "done",
            "workspace_diff": "",
            "turn_count": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_latency_ms": 100,
        }

    def _score(task, result):
        return {"dimensions": {}, "total": 0, "max_score": 0}

    # Monkeypatch RESULTS_DIR so logs go to tmp_path
    import harness.log as log_mod
    original_results = log_mod.RESULTS_DIR
    log_mod.RESULTS_DIR = tmp_path
    try:
        _run_task_loop(
            tasks=[{"name": "my-task", "workspace": "blank", "scoring_criteria": []}],
            run_id="run-retry-test",
            battery_name="test-battery",
            system_prompt="sys",
            tool_surfaces=["codebase"],
            resolved_model="openai/gpt-4o-mini",
            no_docker=True,
            seeds=1,
            provision_fn=_provision,
            run_task_fn=_run_task,
            score_fn=_score,
            TaskLogger=TaskLogger,
            is_retry=True,
        )
    finally:
        log_mod.RESULTS_DIR = original_results

    log_path = tmp_path / "run-retry-test" / "tasks" / "my-task.jsonl"
    assert log_path.exists(), "task JSONL was not created"
    lines = [json.loads(raw) for raw in log_path.read_text().splitlines() if raw.strip()]
    event_types = [e["event"] for e in lines]
    assert event_types[0] == "retry_start", (
        f"First event should be retry_start, got: {event_types}"
    )


def test_retry_start_not_written_for_fresh_run(tmp_path):
    """When is_retry=False (default), no retry_start event is written."""
    import json
    from harness.bench import _run_task_loop
    from harness.log import TaskLogger

    def _provision(battery_name, workspace_name, *, use_docker):
        class _FakeWS:
            diff = ""
            def destroy(self): pass
        return _FakeWS()

    def _run_task(*, agent_system_prompt, task, workspace, model,
                  agent_tool_surfaces, logger):
        return {
            "final_response": "done", "workspace_diff": "",
            "turn_count": 1, "prompt_tokens": 10,
            "completion_tokens": 5, "total_latency_ms": 100,
        }

    def _score(task, result):
        return {"dimensions": {}, "total": 0, "max_score": 0}

    import harness.log as log_mod
    original_results = log_mod.RESULTS_DIR
    log_mod.RESULTS_DIR = tmp_path
    try:
        _run_task_loop(
            tasks=[{"name": "my-task", "workspace": "blank", "scoring_criteria": []}],
            run_id="run-fresh-test",
            battery_name="test-battery",
            system_prompt="sys",
            tool_surfaces=["codebase"],
            resolved_model="openai/gpt-4o-mini",
            no_docker=True,
            seeds=1,
            provision_fn=_provision,
            run_task_fn=_run_task,
            score_fn=_score,
            TaskLogger=TaskLogger,
            # is_retry defaults to False
        )
    finally:
        log_mod.RESULTS_DIR = original_results

    log_path = tmp_path / "run-fresh-test" / "tasks" / "my-task.jsonl"
    lines = [json.loads(raw) for raw in log_path.read_text().splitlines() if raw.strip()]
    event_types = [e["event"] for e in lines]
    assert "retry_start" not in event_types
