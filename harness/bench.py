"""harness/bench.py — orchestrates a full benchmark run.

Workflow per run:
  1. Locate agent in registry (resolve from fetched repo)
  2. Load agent system prompt + tool surface from frontmatter
  3. Load battery (auto-detect or named)
  4. For each task:
     a. provision workspace
     b. invoke agentic loop
     c. score result
     d. log everything
     e. destroy workspace
  5. Write run.json summary to results/<run-id>/
  6. Render initial markdown report
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
BATTERIES_DIR = Path(__file__).resolve().parents[1] / "batteries"


# ── Agent resolution ──────────────────────────────────────────────────────────

def _find_agent_file(agent_name: str, source_repo: str | None) -> Path:
    """Locate the .agent.md file in the registry."""
    search_roots = (
        [REGISTRY_DIR / source_repo] if source_repo
        else list(REGISTRY_DIR.rglob("agents"))
    )
    for root in search_roots:
        if not root.is_dir():
            continue
        for f in root.glob("*.agent.md"):
            if f.stem.lower().replace(".agent", "") == agent_name.lower():
                return f
    raise FileNotFoundError(
        f"Agent '{agent_name}' not found in registry. "
        f"Run: lab fetch <owner/repo>"
    )


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter as a dict (minimal parser — no PyYAML needed)."""
    if not text.startswith("---"):
        return {}
    body = text[3:]
    end = body.find("---")
    if end == -1:
        return {}
    fm: dict = {}
    for line in body[:end].splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # Handle list values: tools: [codebase, terminal]
        if val.startswith("[") and val.endswith("]"):
            fm[key] = [v.strip().strip('"').strip("'")
                       for v in val[1:-1].split(",") if v.strip()]
        else:
            fm[key] = val
    return fm


def _load_agent(agent_file: Path) -> tuple[str, dict]:
    """Return (system_prompt, frontmatter_dict)."""
    text = agent_file.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    # Strip frontmatter to get the body
    if text.startswith("---"):
        body_after_first = text[3:]
        end = body_after_first.find("---")
        body = body_after_first[end + 3:].strip() if end != -1 else body_after_first.strip()
    else:
        body = text.strip()
    return body, fm


# ── Battery resolution ────────────────────────────────────────────────────────

def _find_battery(agent_name: str, battery_name: str | None) -> tuple[dict, Path]:
    """Load and return (battery_dict, battery_path)."""
    if battery_name:
        path = BATTERIES_DIR / battery_name / "battery.json"
        if not path.exists():
            raise FileNotFoundError(f"Battery '{battery_name}' not found at {path}")
        return json.loads(path.read_text(encoding="utf-8")), path

    # Auto-detect: battery whose target_agents includes this agent
    name_lower = agent_name.lower()
    for battery_json in BATTERIES_DIR.rglob("battery.json"):
        data = json.loads(battery_json.read_text(encoding="utf-8"))
        targets = [t.lower() for t in data.get("target_agents", [])]
        if name_lower in targets or "all" in targets:
            return data, battery_json

    raise FileNotFoundError(
        f"No battery found for agent '{agent_name}'. "
        f"Specify one with --battery or add a battery with target_agents: [{agent_name}]"
    )


# ── Reproducibility manifest ──────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    run_dir: Path,
    run_id: str,
    agent_file: Path,
    battery_file: Path,
    model: str,
    *,
    tags_filter: list[str] | None = None,
    seeds: int = 1,
) -> None:
    """Write manifest.json with SHA256 of every input for replay verification."""
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "agent_file": str(agent_file.relative_to(agent_file.parents[3])),
        "agent_sha256": _sha256(agent_file),
        "battery_file": str(battery_file.relative_to(battery_file.parents[2])),
        "battery_sha256": _sha256(battery_file),
        "note": "SHA256 digests capture all inputs. Re-run with the same model + same digest files to reproduce.",
    }
    if tags_filter:
        manifest["tags_filter"] = tags_filter
    if seeds > 1:
        manifest["seeds"] = seeds
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

# ── Multi-seed scoring ──────────────────────────────────────────────────

def _wilson_ci(passes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% confidence interval for a proportion."""
    if n == 0:
        return 0.0, 1.0
    p = passes / n
    z2 = z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    margin = z * ((p * (1 - p) / n) + z2 / (4 * n * n)) ** 0.5 / (1 + z2 / n)
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _aggregate_seeds(
    seed_scores: list[dict],
    seed_results: list[dict],
) -> dict:
    """Aggregate multi-seed dimension results into a single task summary.

    Dimensions use majority vote; pass_rates records the full distribution
    with Wilson 95% CI for each dimension.
    """
    if not seed_scores:
        return {"status": "run_failed", "scores": {"dimensions": {}, "total": 0, "max_score": 0}}

    n = len(seed_scores)
    max_score = seed_scores[0].get("max_score", 0)

    all_dims: set[str] = set()
    for s in seed_scores:
        all_dims.update(s.get("dimensions", {}).keys())

    pass_rates: dict[str, dict] = {}
    majority_dims: dict[str, bool | None] = {}

    for dim in sorted(all_dims):
        vals = [s.get("dimensions", {}).get(dim) for s in seed_scores]
        passes = sum(1 for v in vals if v is True)
        failures = sum(1 for v in vals if v is False)
        nulls = sum(1 for v in vals if v is None)
        rate = passes / n
        lo, hi = _wilson_ci(passes, n)
        pass_rates[dim] = {
            "pass": passes, "fail": failures, "null": nulls,
            "rate": round(rate, 4),
            "ci_low": round(lo, 4), "ci_high": round(hi, 4),
        }
        # Majority vote; any null keeps the dimension null (pending LLM judge)
        if nulls > 0:
            majority_dims[dim] = None
        else:
            majority_dims[dim] = passes > n / 2

    mean_total = round(sum(s.get("total", 0) for s in seed_scores) / n)
    nr = max(len(seed_results), 1)

    return {
        "status": "ok",
        "scores": {
            "dimensions": majority_dims,
            "total": mean_total,
            "max_score": max_score,
            "seeds": n,
            "pass_rates": pass_rates,
        },
        "turn_count": round(sum(r.get("turn_count", 0) for r in seed_results) / nr),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in seed_results),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in seed_results),
        "total_latency_ms": round(sum(r.get("total_latency_ms", 0) for r in seed_results) / nr),
    }

# ── Run ID ────────────────────────────────────────────────────────────────────

def _make_run_id(agent_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{ts}-{agent_name.lower()}"


# ── Main entry point ──────────────────────────────────────────────────────────

def _run_task_loop(
    *,
    tasks: list[dict],
    run_id: str,
    battery_name: str,
    system_prompt: str,
    tool_surfaces: list[str],
    resolved_model: str,
    no_docker: bool,
    seeds: int,
    provision_fn,
    run_task_fn,
    score_fn,
    TaskLogger,
    is_retry: bool = False,
) -> list[dict]:
    """Execute tasks (with optional multi-seed), return task_summaries list."""
    task_summaries: list[dict] = []

    for i, task in enumerate(tasks, 1):
        task_name = task.get("name", f"task-{i:02d}")
        workspace_name = task.get("workspace", "blank")
        if seeds > 1:
            print(f"  [{i}/{len(tasks)}] {task_name}  workspace={workspace_name}  ×{seeds} seeds")
        else:
            print(f"  [{i}/{len(tasks)}] {task_name}  workspace={workspace_name}")

        seed_scores: list[dict] = []
        seed_run_results: list[dict] = []
        provision_error = False
        run_error: str | None = None

        for seed_idx in range(seeds):
            log_name = f"{task_name}-seed-{seed_idx}" if seeds > 1 else task_name
            with TaskLogger(run_id, log_name) as logger:
                if is_retry:
                    logger.retry_start(attempt=1)
                try:
                    workspace = provision_fn(
                        battery_name,
                        workspace_name,
                        use_docker=not no_docker,
                    )
                except Exception as exc:
                    logger.error(f"Workspace provision failed: {exc}", exc)
                    provision_error = True
                    break

                # Run any per-task setup commands before the agent starts
                setup_commands: list[str] = task.get("setup", [])
                setup_failed = False
                for cmd in setup_commands:
                    rc, stdout, stderr = workspace.exec(cmd)
                    logger.workspace_setup(cmd, rc, stdout, stderr)
                    if rc != 0:
                        logger.error(
                            f"Setup command failed (rc={rc}): {cmd!r}\n{stderr.strip()}"
                        )
                        setup_failed = True
                        break
                if setup_failed:
                    workspace.destroy()
                    provision_error = True
                    break

                try:
                    result = run_task_fn(
                        agent_system_prompt=system_prompt,
                        task=task,
                        workspace=workspace,
                        model=resolved_model,
                        agent_tool_surfaces=tool_surfaces,
                        logger=logger,
                    )
                    s = score_fn(task, result)
                    logger.score(
                        dimensions=s["dimensions"],
                        total=s["total"],
                        max_score=s["max_score"],
                    )
                    pct = round(100 * s["total"] / s["max_score"], 1) if s["max_score"] else 0
                    verdict = "✅" if pct >= 80 else ("⚠️" if pct >= 60 else "❌")
                    if seeds == 1:
                        print(
                            f"         {verdict} {s['total']}/{s['max_score']} ({pct}%)"
                            f"  turns={result['turn_count']}"
                            f"  tokens={result['prompt_tokens'] + result['completion_tokens']}"
                        )
                    else:
                        print(f"         seed {seed_idx}: {verdict} {s['total']}/{s['max_score']} ({pct}%)")
                    seed_scores.append(s)
                    seed_run_results.append(result)
                except Exception as exc:
                    logger.error(f"Task run failed: {exc}", exc)
                    run_error = str(exc)
                finally:
                    workspace.destroy()

        # ── assemble task summary ─────────────────────────────────────────────
        if provision_error:
            task_summaries.append({"task": task_name, "status": "provision_failed", "scores": {}})
        elif not seed_scores:
            task_summaries.append({
                "task": task_name, "status": "run_failed",
                "error": run_error or "unknown", "scores": {},
            })
        elif seeds == 1:
            task_summaries.append({
                "task": task_name, "status": "ok",
                "scores": seed_scores[0],
                "turn_count": seed_run_results[0]["turn_count"],
                "prompt_tokens": seed_run_results[0]["prompt_tokens"],
                "completion_tokens": seed_run_results[0]["completion_tokens"],
                "total_latency_ms": seed_run_results[0]["total_latency_ms"],
            })
        else:
            agg = _aggregate_seeds(seed_scores, seed_run_results)
            max_s = seed_scores[0].get("max_score", 0)
            mean_pct = round(100 * agg["scores"]["total"] / max_s, 1) if max_s else 0
            verdict = "✅" if mean_pct >= 80 else ("⚠️" if mean_pct >= 60 else "❌")
            print(
                f"         {verdict} mean {agg['scores']['total']}/{max_s} ({mean_pct}%)"
                f"  ({len(seed_scores)}/{seeds} seeds ok)"
            )
            task_summaries.append({"task": task_name, **agg})

    return task_summaries


def run_bench(
    *,
    agent_name: str,
    battery_name: str | None,
    source_repo: str | None,
    model: str | None,
    no_docker: bool,
    limit: int | None = None,
    tags: list[str] | None = None,
    seeds: int = 1,
) -> tuple[int, str | None]:
    if not os.getenv("GITHUB_TOKEN"):
        print("GITHUB_TOKEN is not set. Provide a token with models:read scope.",
              file=sys.stderr)
        return 2, None

    # --- Resolve agent ---
    try:
        agent_file = _find_agent_file(agent_name, source_repo)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2, None

    system_prompt, fm = _load_agent(agent_file)
    tool_surfaces: list[str] = fm.get("tools", ["codebase"])
    if isinstance(tool_surfaces, str):
        tool_surfaces = [tool_surfaces]

    # Model precedence: CLI flag > agent frontmatter > env > default
    resolved_model = (
        model
        or os.getenv("LAB_LLM_MODEL")
        or (fm.get("model") if isinstance(fm.get("model"), str) else None)
        or "openai/gpt-4o-mini"
    )

    # --- Resolve battery ---
    try:
        battery, battery_file = _find_battery(agent_name, battery_name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2, None

    tasks: list[dict] = battery.get("tasks", [])
    if not tasks:
        print(f"Battery '{battery.get('name')}' has no tasks.", file=sys.stderr)
        return 2, None

    # Tag filtering applied before limit so --limit N means "first N matching tasks"
    if tags:
        tag_set = {t.strip().lower() for t in tags}
        tasks = [t for t in tasks if any(tg.lower() in tag_set for tg in t.get("tags", []))]
        if not tasks:
            print(f"No tasks matched tags: {', '.join(tags)}", file=sys.stderr)
            return 2, None
        print(f"(--tags {','.join(tags)}: {len(tasks)} task(s) matched)")

    if limit is not None:
        tasks = tasks[:limit]
        print(f"(--limit {limit}: running {len(tasks)} of {len(battery.get('tasks', []))} tasks)")

    run_id = _make_run_id(agent_name)
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True)

    total_invocations = len(tasks) * seeds
    print(f"Run: {run_id}")
    print(f"Agent: {agent_name}  model: {resolved_model}  tools: {tool_surfaces}")
    print(
        f"Battery: {battery.get('name')}  tasks: {len(tasks)}"
        + (f"  seeds: {seeds}  ({total_invocations} invocations)" if seeds > 1 else "")
    )
    if seeds > 1:
        print(f"Warning: {total_invocations} total LLM task invocations ({len(tasks)} tasks × {seeds} seeds)")
    print(f"Isolation: {'Docker' if not no_docker else 'tmpdir (--no-docker)'}")
    print()

    # Deferred imports (keep startup fast)
    from harness.provision import provision
    from harness.invoke import run_task
    from harness.score import score
    from harness.log import TaskLogger
    from reporters.bench_report import render_report

    task_summaries = _run_task_loop(
        tasks=tasks,
        run_id=run_id,
        battery_name=battery.get("name", "unknown"),
        system_prompt=system_prompt,
        tool_surfaces=tool_surfaces,
        resolved_model=resolved_model,
        no_docker=no_docker,
        seeds=seeds,
        provision_fn=provision,
        run_task_fn=run_task,
        score_fn=score,
        TaskLogger=TaskLogger,
    )

    # --- Write run summary ---
    run_meta = {
        "run_id": run_id,
        "agent": agent_name,
        "source_repo": source_repo,
        "battery": battery.get("name"),
        "model": resolved_model,
        "tool_surfaces": tool_surfaces,
        "isolation": "docker" if not no_docker else "tmpdir",
        "started_at": run_id.split("-", 1)[1][:15],   # YYYYMMDD-HHMMSS
        "tasks": task_summaries,
    }
    (run_dir / "run.json").write_text(
        json.dumps(run_meta, indent=2) + "\n", encoding="utf-8"
    )

    # --- Reproducibility manifest ---
    _write_manifest(
        run_dir, run_id, agent_file, battery_file, resolved_model,
        tags_filter=tags, seeds=seeds,
    )

    # --- Initial report ---
    render_report(run_id=run_id)

    # Print summary
    ok = sum(1 for t in task_summaries if t.get("status") == "ok")
    total = len(task_summaries)
    raw = sum(t["scores"].get("total", 0) for t in task_summaries if t.get("status") == "ok")
    max_pts = sum(t["scores"].get("max_score", 0) for t in task_summaries if t.get("status") == "ok")
    overall = round(100 * raw / max_pts, 1) if max_pts else 0

    print()
    print(f"Completed {ok}/{total} tasks  —  {raw}/{max_pts} pts  ({overall}%)")
    print(f"Results: results/{run_id}/")
    print()
    print("Next steps:")
    print(f"  @EvalJudge  results/{run_id}/run.json   ← score LLM-judged dimensions")
    print(f"  lab compare {run_id} <other-run-id>     ← diff against another run")
    print(f"  lab issue {run_id} [--repo owner/repo]  ← generate GitHub issue")
    return 0, run_id


# ── Resume failed tasks ───────────────────────────────────────────────────────

def resume_bench(
    *,
    run_id: str,
    model: str | None = None,
    no_docker: bool = False,
    seeds: int = 1,
) -> tuple[int, str | None]:
    """Re-run failed tasks from a previous run, merging results in-place."""
    run_dir = RESULTS_DIR / run_id
    run_json_path = run_dir / "run.json"
    if not run_json_path.exists():
        print(f"Error: no run.json found for '{run_id}'", file=sys.stderr)
        return 2, None

    run_meta = json.loads(run_json_path.read_text(encoding="utf-8"))

    agent_name = run_meta["agent"]
    battery_name = run_meta.get("battery")
    source_repo = run_meta.get("source_repo")

    # Model: CLI flag > env > original run
    resolved_model = (
        model
        or os.getenv("LAB_LLM_MODEL")
        or run_meta.get("model", "openai/gpt-4o-mini")
    )

    prior_summaries: list[dict] = run_meta.get("tasks", [])
    failed = [t for t in prior_summaries if t.get("status") != "ok"]

    if not failed:
        print(f"No failed tasks in {run_id}.")
        return 0, run_id

    failed_names = {t["task"] for t in failed}
    print(f"Re-running {len(failed_names)} failed task(s) from {run_id}")
    print()

    try:
        agent_file = _find_agent_file(agent_name, source_repo)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2, None

    system_prompt, fm = _load_agent(agent_file)
    tool_surfaces: list[str] = fm.get("tools", ["codebase"])
    if isinstance(tool_surfaces, str):
        tool_surfaces = [tool_surfaces]

    try:
        battery, battery_file = _find_battery(agent_name, battery_name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2, None

    tasks_to_retry = [t for t in battery.get("tasks", []) if t.get("name") in failed_names]

    print(f"Agent: {agent_name}  model: {resolved_model}")
    print(
        f"Battery: {battery.get('name')}  retrying: {len(tasks_to_retry)} task(s)"
        + (f"  seeds: {seeds}  ({len(tasks_to_retry) * seeds} invocations)" if seeds > 1 else "")
    )
    if seeds > 1:
        print(
            f"Warning: {len(tasks_to_retry) * seeds} total LLM invocations "
            f"({len(tasks_to_retry)} tasks × {seeds} seeds)"
        )
    print(f"Isolation: {'Docker' if not no_docker else 'tmpdir (--no-docker)'}")
    print()

    from harness.provision import provision
    from harness.invoke import run_task
    from harness.score import score
    from harness.log import TaskLogger
    from reporters.bench_report import render_report

    # Run the failed tasks (retry logs append to existing JSONL; retry_start event marks boundary)
    new_summaries = _run_task_loop(
        tasks=tasks_to_retry,
        run_id=run_id,
        battery_name=battery.get("name", "unknown"),
        system_prompt=system_prompt,
        tool_surfaces=tool_surfaces,
        resolved_model=resolved_model,
        no_docker=no_docker,
        seeds=seeds,
        is_retry=True,
        provision_fn=provision,
        run_task_fn=run_task,
        score_fn=score,
        TaskLogger=TaskLogger,
    )

    # Merge new results in-place into prior_summaries
    summary_idx = {t["task"]: i for i, t in enumerate(prior_summaries)}
    for new in new_summaries:
        task_name = new["task"]
        if task_name in summary_idx:
            prior_summaries[summary_idx[task_name]] = new
        else:
            prior_summaries.append(new)

    # Re-write run.json
    run_meta["tasks"] = prior_summaries
    run_json_path.write_text(json.dumps(run_meta, indent=2) + "\n", encoding="utf-8")

    # Re-write manifest (timestamps update; no new tags/seeds info)
    _write_manifest(run_dir, run_id, agent_file, battery_file, resolved_model, seeds=seeds)

    render_report(run_id=run_id)

    ok = sum(1 for t in prior_summaries if t.get("status") == "ok")
    total = len(prior_summaries)
    print()
    print(f"Completed {ok}/{total} tasks after retry.")
    print(f"Results: results/{run_id}/")
    return 0, run_id
