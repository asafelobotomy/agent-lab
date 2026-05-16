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

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
BATTERIES_DIR = Path(__file__).resolve().parents[1] / "batteries"


# ── Agent resolution ──────────────────────────────────────────────────────────

def _find_agent_file(agent_name: str, source_repo: str | None) -> Path:
    """Locate the .agent.md file in the registry."""
    pattern = f"{agent_name.lower()}.agent.md"
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

def _find_battery(agent_name: str, battery_name: str | None) -> dict:
    """Load and return a battery JSON dict."""
    if battery_name:
        path = BATTERIES_DIR / battery_name / "battery.json"
        if not path.exists():
            raise FileNotFoundError(f"Battery '{battery_name}' not found at {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # Auto-detect: battery whose target_agents includes this agent
    name_lower = agent_name.lower()
    for battery_json in BATTERIES_DIR.rglob("battery.json"):
        data = json.loads(battery_json.read_text(encoding="utf-8"))
        targets = [t.lower() for t in data.get("target_agents", [])]
        if name_lower in targets or "all" in targets:
            return data

    raise FileNotFoundError(
        f"No battery found for agent '{agent_name}'. "
        f"Specify one with --battery or add a battery with target_agents: [{agent_name}]"
    )


# ── Run ID ────────────────────────────────────────────────────────────────────

def _make_run_id(agent_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{ts}-{agent_name.lower()}"


# ── Main entry point ──────────────────────────────────────────────────────────

def run_bench(
    *,
    agent_name: str,
    battery_name: str | None,
    source_repo: str | None,
    model: str | None,
    no_docker: bool,
) -> int:
    if not os.getenv("GITHUB_TOKEN"):
        print("GITHUB_TOKEN is not set. Provide a token with models:read scope.",
              file=sys.stderr)
        return 2

    # --- Resolve agent ---
    try:
        agent_file = _find_agent_file(agent_name, source_repo)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

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
        battery = _find_battery(agent_name, battery_name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    tasks: list[dict] = battery.get("tasks", [])
    if not tasks:
        print(f"Battery '{battery.get('name')}' has no tasks.", file=sys.stderr)
        return 2

    run_id = _make_run_id(agent_name)
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True)

    print(f"Run: {run_id}")
    print(f"Agent: {agent_name}  model: {resolved_model}  tools: {tool_surfaces}")
    print(f"Battery: {battery.get('name')}  tasks: {len(tasks)}")
    print(f"Isolation: {'Docker' if not no_docker else 'tmpdir (--no-docker)'}")
    print()

    # Deferred imports (keep startup fast)
    from harness.provision import provision
    from harness.invoke import run_task
    from harness.score import score
    from harness.log import TaskLogger
    from reporters.bench_report import render_report

    task_summaries: list[dict] = []

    for i, task in enumerate(tasks, 1):
        task_name = task.get("name", f"task-{i:02d}")
        workspace_name = task.get("workspace", "blank")
        print(f"  [{i}/{len(tasks)}] {task_name}  workspace={workspace_name}")

        with TaskLogger(run_id, task_name) as logger:
            try:
                workspace = provision(
                    battery.get("name", "unknown"),
                    workspace_name,
                    use_docker=not no_docker,
                )
            except Exception as exc:
                logger.error(f"Workspace provision failed: {exc}", exc)
                task_summaries.append({
                    "task": task_name, "status": "provision_failed", "scores": {}
                })
                continue

            try:
                result = run_task(
                    agent_system_prompt=system_prompt,
                    task=task,
                    workspace=workspace,
                    model=resolved_model,
                    agent_tool_surfaces=tool_surfaces,
                    logger=logger,
                )
                scores = score(task, result)
                logger.score(
                    dimensions=scores["dimensions"],
                    total=scores["total"],
                    max_score=scores["max_score"],
                )
                pct = round(100 * scores["total"] / scores["max_score"], 1) if scores["max_score"] else 0
                verdict = "✅" if pct >= 80 else ("⚠️" if pct >= 60 else "❌")
                print(f"         {verdict} {scores['total']}/{scores['max_score']} ({pct}%)"
                      f"  turns={result['turn_count']}"
                      f"  tokens={result['prompt_tokens']+result['completion_tokens']}")

                task_summaries.append({
                    "task": task_name,
                    "status": "ok",
                    "scores": scores,
                    "turn_count": result["turn_count"],
                    "prompt_tokens": result["prompt_tokens"],
                    "completion_tokens": result["completion_tokens"],
                    "total_latency_ms": result["total_latency_ms"],
                })
            except Exception as exc:
                logger.error(f"Task run failed: {exc}", exc)
                task_summaries.append({
                    "task": task_name, "status": "run_failed",
                    "error": str(exc), "scores": {}
                })
            finally:
                workspace.destroy()

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

    # --- Initial report ---
    render_report(run_id=run_id)

    # Print summary
    ok = sum(1 for t in task_summaries if t["status"] == "ok")
    total = len(task_summaries)
    raw = sum(t["scores"].get("total", 0) for t in task_summaries if t["status"] == "ok")
    max_pts = sum(t["scores"].get("max_score", 0) for t in task_summaries if t["status"] == "ok")
    overall = round(100 * raw / max_pts, 1) if max_pts else 0

    print()
    print(f"Completed {ok}/{total} tasks  —  {raw}/{max_pts} pts  ({overall}%)")
    print(f"Results: results/{run_id}/")
    print()
    print("Next steps:")
    print(f"  @EvalJudge  results/{run_id}/run.json   ← score LLM-judged dimensions")
    print(f"  lab issue {run_id} [--repo owner/repo]  ← generate GitHub issue")
    return 0
