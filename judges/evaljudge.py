"""judges/evaljudge.py — harness implementation of EvalJudge scoring.

For each null (pending) LLM-judged dimension in a completed run:
  1. Locate the criterion text from the battery JSON
  2. Reconstruct the agent's conversation from the task JSONL
  3. Call the LLM with a structured judge prompt
  4. Parse PASS / FAIL → fill in the dimension score
  5. Patch run.json and re-render report.md

Usage (harness):
    from judges.evaljudge import score_llm_dimensions
    filled = score_llm_dimensions("run-20260516-143022-triage", model="openai/gpt-4o")

Usage (CLI):
    lab judge <run-id> [--model MODEL]
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
BATTERIES_DIR = Path(__file__).resolve().parents[1] / "batteries"

_ENDPOINT = "https://models.github.ai/inference/chat/completions"
_MIN_CALL_INTERVAL = 5.0
_last_call: float = 0.0


def _wilson_ci_lo(passes: int, n: int, z: float = 1.96) -> float:
    """Lower bound of Wilson score 95% CI (used to update pass_rates after LLM judging)."""
    if n == 0:
        return 0.0
    p = passes / n
    z2 = z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    margin = z * ((p * (1 - p) / n) + z2 / (4 * n * n)) ** 0.5 / (1 + z2 / n)
    return max(0.0, centre - margin)


def _wilson_ci_hi(passes: int, n: int, z: float = 1.96) -> float:
    """Upper bound of Wilson score 95% CI."""
    if n == 0:
        return 1.0
    p = passes / n
    z2 = z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    margin = z * ((p * (1 - p) / n) + z2 / (4 * n * n)) ** 0.5 / (1 + z2 / n)
    return min(1.0, centre + margin)

_JUDGE_SYSTEM = (
    "You are an expert benchmark evaluator reviewing an AI agent's performance "
    "on a coding task.\n\n"
    "You will be given the agent's full conversation log and a scoring criterion. "
    "Decide whether the agent satisfies the criterion.\n\n"
    "Your response MUST start with exactly 'PASS' or 'FAIL' on the first line "
    "(case-sensitive), followed by a single sentence of justification. "
    "Do not add any other text before the verdict."
)

_JUDGE_USER_TMPL = """\
## Task prompt given to the agent

{task_prompt}

## Agent conversation

{conversation}

## Scoring criterion

{criterion}
"""


# ── LLM call ──────────────────────────────────────────────────────────────────

def _throttle() -> None:
    global _last_call
    elapsed = time.monotonic() - _last_call
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)
    _last_call = time.monotonic()


def _llm_call(messages: list[dict], model: str) -> str:
    """Call the GitHub Models API; return the response text."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")
    _throttle()
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.0,
    }).encode()
    req = urllib.request.Request(
        _ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-ms-model-mesh-model-name": model,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"LLM API error {exc.code}: {exc.read().decode()}") from exc
    return data["choices"][0]["message"]["content"] or ""


# ── JSONL helpers ─────────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def _build_conversation(events: list[dict]) -> str:
    """Build a compact, human-readable conversation summary from JSONL events."""
    lines: list[str] = []
    turn = 0
    post_diff = ""

    for ev in events:
        kind = ev.get("event")

        if kind == "turn_start":
            turn = ev.get("turn", 0)

        elif kind == "tool_call":
            name = ev.get("name", "?")
            args = ev.get("arguments", {})
            # Show up to 3 args to keep the prompt concise
            shown = list(args.items())[:3]
            args_str = ", ".join(f'{k}="{v}"' for k, v in shown)
            if len(args) > 3:
                args_str += ", …"
            lines.append(f"[turn {turn}] tool_call: {name}({args_str})")

        elif kind == "tool_result":
            result = str(ev.get("result", ""))
            if len(result) > 400:
                result = result[:400] + " …(truncated)"
            lines.append(f"           result: {result!r}")

        elif kind == "llm_response":
            content = ev.get("content")
            if content:
                lines.append(f"[turn {turn}] agent response: {content}")

        elif kind == "workspace_snapshot" and ev.get("label") == "post-task":
            post_diff = ev.get("diff", "")

    body = "\n".join(lines) if lines else "(agent responded without any tool calls)"
    if post_diff:
        body += f"\n\n### Workspace diff (post-task)\n```diff\n{post_diff}\n```"
    return body


def _extract_task_prompt(events: list[dict]) -> str:
    """Pull the first user message content from the conversation log."""
    for ev in events:
        if ev.get("event") == "llm_request":
            for msg in ev.get("messages", []):
                if msg.get("role") == "user":
                    return str(msg.get("content", ""))
    return "(task prompt unavailable)"


# ── Battery lookup ────────────────────────────────────────────────────────────

def _find_llm_criteria(battery_name: str, task_name: str) -> dict[str, str]:
    """Return {dimension: criterion_text} for all llm-type criteria in a task."""
    battery_path = BATTERIES_DIR / battery_name / "battery.json"
    if not battery_path.exists():
        return {}
    try:
        battery = json.loads(battery_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    for task in battery.get("tasks", []):
        if task.get("name") == task_name:
            return {
                c["dimension"]: c.get("criterion", "")
                for c in task.get("scoring_criteria", [])
                if c.get("type") == "llm" and c.get("criterion")
            }
    return {}


# ── Verdict parsing ───────────────────────────────────────────────────────────

def _parse_verdict(response: str) -> bool | None:
    """Return True (PASS), False (FAIL), or None (ambiguous) from LLM response."""
    first_line = response.strip().splitlines()[0].strip() if response.strip() else ""
    if first_line.startswith("PASS"):
        return True
    if first_line.startswith("FAIL"):
        return False
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def score_llm_dimensions(run_id: str, model: str | None = None) -> int:
    """Fill in null LLM dimensions for a completed run.

    Patches run.json in-place and re-renders report.md.
    Returns the number of dimensions successfully scored.
    """
    run_path = RESULTS_DIR / run_id / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"No run.json for run '{run_id}'")

    run = json.loads(run_path.read_text(encoding="utf-8"))
    resolved_model = (
        model
        or os.getenv("LAB_LLM_MODEL")
        or run.get("model")
        or "openai/gpt-4o"
    )
    battery_name: str = run.get("battery", "")
    total_filled = 0

    for task_summary in run.get("tasks", []):
        if task_summary.get("status") != "ok":
            continue

        task_name: str = task_summary.get("task", "")
        scores = task_summary.setdefault("scores", {})
        dims: dict[str, bool | None] = scores.setdefault("dimensions", {})
        n_seeds: int = scores.get("seeds", 1)
        pending = [d for d, v in dims.items() if v is None]
        if not pending:
            continue

        # Find criterion texts from the battery
        llm_criteria = _find_llm_criteria(battery_name, task_name)
        if not llm_criteria:
            print(f"  [{task_name}] no llm criteria found in battery '{battery_name}' — skipping")
            continue

        # Reconstruct conversation from JSONL.
        # Multi-seed runs log to {task}-seed-{idx}.jsonl — merge all seeds for full context.
        tasks_dir = RESULTS_DIR / run_id / "tasks"
        if n_seeds > 1:
            events: list[dict] = []
            for seed_idx in range(n_seeds):
                events.extend(_load_jsonl(tasks_dir / f"{task_name}-seed-{seed_idx}.jsonl"))
        else:
            events = _load_jsonl(tasks_dir / f"{task_name}.jsonl")
        task_prompt = _extract_task_prompt(events)
        conversation = _build_conversation(events)

        for dim in pending:
            criterion = llm_criteria.get(dim)
            if not criterion:
                print(f"  [{task_name}/{dim}] criterion text missing in battery — skipping")
                continue

            seed_label = f" ({n_seeds} seeds merged)" if n_seeds > 1 else ""
            print(f"  [{task_name}/{dim}]{seed_label} judging …", end=" ", flush=True)
            messages = [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": _JUDGE_USER_TMPL.format(
                    task_prompt=task_prompt,
                    conversation=conversation,
                    criterion=criterion,
                )},
            ]
            try:
                response = _llm_call(messages, resolved_model)
                verdict = _parse_verdict(response)
                if verdict is True:
                    dims[dim] = True
                    scores["total"] = scores.get("total", 0) + 1
                    print("PASS ✅")
                    total_filled += 1
                    # Keep pass_rates consistent: treat the single verdict as applying to all seeds
                    if n_seeds > 1 and "pass_rates" in scores:
                        lo = _wilson_ci_lo(n_seeds, n_seeds)
                        scores["pass_rates"][dim] = {
                            "pass": n_seeds, "fail": 0, "null": 0,
                            "rate": 1.0, "ci_low": round(lo, 4), "ci_high": 1.0,
                        }
                elif verdict is False:
                    dims[dim] = False
                    print("FAIL ❌")
                    total_filled += 1
                    if n_seeds > 1 and "pass_rates" in scores:
                        hi = _wilson_ci_hi(0, n_seeds)
                        scores["pass_rates"][dim] = {
                            "pass": 0, "fail": n_seeds, "null": 0,
                            "rate": 0.0, "ci_low": 0.0, "ci_high": round(hi, 4),
                        }
                else:
                    print(f"ambiguous response — leaving as null\n  response: {response[:120]!r}")
            except Exception as exc:
                print(f"error — {exc}")

    # Write patched run.json
    run_path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")

    # Re-render structural report with updated scores
    from reporters.bench_report import render_report
    render_report(run_id)

    return total_filled
