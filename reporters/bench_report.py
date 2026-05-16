"""reporters/bench_report.py — render a benchmark run report from run.json + task JSONL logs.

Produces results/<run-id>/report.md — the initial structural report.
EvalJudge reads this and the task logs to produce the final scored report.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def _load_run(run_id: str) -> dict:
    path = RESULTS_DIR / run_id / "run.json"
    if not path.exists():
        raise FileNotFoundError(f"No run.json for run '{run_id}'")
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(total: int, max_score: int) -> str:
    if max_score == 0:
        return "—"
    return f"{round(100 * total / max_score)}%"


def _verdict(pct_str: str) -> str:
    if pct_str == "—":
        return "⏭️ n/a"
    pct = int(pct_str.rstrip("%"))
    if pct >= 80:
        return "✅ Pass"
    if pct >= 60:
        return "⚠️ Marginal"
    return "❌ Fail"


def _grade(pct_str: str) -> str:
    """Return letter grade: A ≥ 90 %, B ≥ 80 %, C ≥ 70 %, D ≥ 60 %, F < 60 %."""
    if pct_str == "—":
        return "?"
    pct = int(pct_str.rstrip("%"))
    if pct >= 90:
        return "A"
    if pct >= 80:
        return "B"
    if pct >= 70:
        return "C"
    if pct >= 60:
        return "D"
    return "F"


def render_report(run_id: str) -> int:
    try:
        run = _load_run(run_id)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    out_path = RESULTS_DIR / run_id / "report.md"
    lines: list[str] = []

    agent = run.get("agent", "unknown")
    battery = run.get("battery", "unknown")
    model = run.get("model", "unknown")
    isolation = run.get("isolation", "unknown")
    source_repo = run.get("source_repo") or "—"

    lines += [
        f"# Benchmark Report — {agent}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Run ID | `{run_id}` |",
        f"| Agent | {agent} |",
        f"| Source repo | {source_repo} |",
        f"| Battery | {battery} |",
        f"| Model | {model} |",
        f"| Isolation | {isolation} |",
        f"| Generated | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} |",
        "",
        "---",
        "",
        "## Score Summary",
        "",
    ]

    tasks: list[dict] = run.get("tasks", [])
    ok_tasks = [t for t in tasks if t.get("status") == "ok"]
    total_raw = sum(t["scores"].get("total", 0) for t in ok_tasks)
    total_max = sum(t["scores"].get("max_score", 0) for t in ok_tasks)
    overall_pct = _pct(total_raw, total_max)
    overall_grade = _grade(overall_pct)

    lines += [
        f"**Overall: {overall_pct} (Grade {overall_grade}) — {_verdict(overall_pct)}**",
        "",
        "| Task | Status | Score | Pct | Grade | Verdict | Turns | Tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for task in tasks:
        name = task.get("task", "?")
        status = task.get("status", "?")
        if status != "ok":
            lines.append(f"| {name} | {status} | — | — | — | ❌ Error | — | — |")
            continue
        scores = task.get("scores", {})
        raw = scores.get("total", 0)
        mx = scores.get("max_score", 0)
        pct = _pct(raw, mx)
        grade = _grade(pct)
        verdict = _verdict(pct)
        turns = task.get("turn_count", "?")
        tokens = task.get("prompt_tokens", 0) + task.get("completion_tokens", 0)
        lines.append(f"| {name} | ok | {raw}/{mx} | {pct} | {grade} | {verdict} | {turns} | {tokens} |")

    lines += [
        f"| **TOTAL** | — | **{total_raw}/{total_max}** | **{overall_pct}** | **{overall_grade}** | **{_verdict(overall_pct)}** | — | — |",
        "",
        "---",
        "",
        "## Per-Task Dimension Breakdown",
        "",
    ]

    for task in ok_tasks:
        name = task.get("task", "?")
        scores_block = task.get("scores", {})
        dims: dict = scores_block.get("dimensions", {})
        pass_rates: dict = scores_block.get("pass_rates", {})
        n_seeds: int = scores_block.get("seeds", 1)

        lines += [f"### {name}", ""]
        if not dims:
            lines += ["*(no dimensions scored)*", ""]
            continue

        if n_seeds > 1:
            lines += [
                f"*{n_seeds} seeds — majority-vote verdict shown; pass rate and 95% Wilson CI per dimension.*",
                "",
                "| Dimension | Pass Rate | 95% CI | Verdict |",
                "|---|---|---|---|",
            ]
            for dim, val in dims.items():
                pr = pass_rates.get(dim, {})
                rate_pct = f"{round(pr.get('rate', 0) * 100)}%"
                passes = pr.get("pass", 0)
                total_seeds = passes + pr.get("fail", 0) + pr.get("null", 0)
                ci_low = round(pr.get("ci_low", 0) * 100)
                ci_high = round(pr.get("ci_high", 0) * 100)
                icon = "✅" if val is True else ("❌" if val is False else "⏳ pending (LLM)")
                lines.append(
                    f"| `{dim}` | {passes}/{total_seeds} ({rate_pct})"
                    f" | [{ci_low}%–{ci_high}%] | {icon} |"
                )
        else:
            lines += [
                "| Dimension | Result |",
                "|---|---|",
            ]
            for dim, val in dims.items():
                icon = "✅" if val is True else ("❌" if val is False else "⏳ pending (LLM)")
                lines.append(f"| `{dim}` | {icon} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## EvalJudge Notes",
        "",
        "> This report contains structural scores only. LLM-judged dimensions",
        "> (marked ⏳ pending) must be scored by EvalJudge.",
        ">",
        "> To complete this report:",
        "> ```",
        f"> @EvalJudge  results/{run_id}/run.json",
        "> ```",
        "",
        "---",
        "",
        "## How to submit as a GitHub issue",
        "",
        "```",
        f"lab issue {run_id} --repo <owner/repo>",
        "```",
        "",
        "This prints a formatted issue body. Add `--submit` to open it via the GitHub API.",
    ]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  report: results/{run_id}/report.md")
    return 0
