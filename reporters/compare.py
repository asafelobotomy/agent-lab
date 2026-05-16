"""reporters/compare.py — diff two benchmark runs dimension-by-dimension.

Outputs results/compare-<run-a>-vs-<run-b>.md  (or prints to stdout when
both runs are from the same agent and battery).

Usage:
  lab compare <run-a-id> <run-b-id>
"""
from __future__ import annotations

import json
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"

# Symbols used in the diff table
_SYM = {
    "improved":    "📈 improved",   # False/null → True
    "regressed":   "📉 regressed",  # True → False/null
    "same_pass":   "✅ same pass",
    "same_fail":   "❌ same fail",
    "same_null":   "⏳ same null",
    "added":       "➕ added",      # dim exists only in B
    "removed":     "➖ removed",    # dim exists only in A
}


def _load_run(run_id: str) -> dict:
    path = RESULTS_DIR / run_id / "run.json"
    if not path.exists():
        raise FileNotFoundError(f"No run.json for run '{run_id}'")
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(total: int, max_score: int) -> str:
    if max_score == 0:
        return "—"
    return f"{round(100 * total / max_score)}%"


def _task_index(run: dict) -> dict[str, dict]:
    """Return {task_name: task_dict} for ok tasks only."""
    return {
        t["task"]: t
        for t in run.get("tasks", [])
        if t.get("status") == "ok"
    }


def _diff_tasks(a_tasks: dict[str, dict], b_tasks: dict[str, dict]) -> list[str]:
    """Return markdown lines for the per-task dimension diff table."""
    lines: list[str] = []
    all_names = sorted(set(a_tasks) | set(b_tasks))

    for task_name in all_names:
        a_task = a_tasks.get(task_name)
        b_task = b_tasks.get(task_name)

        lines.append(f"### {task_name}")
        lines.append("")

        if a_task is None:
            lines.append("*Task only in run B (new task).*")
            lines.append("")
            continue
        if b_task is None:
            lines.append("*Task only in run A (removed task).*")
            lines.append("")
            continue

        a_dims: dict = a_task.get("scores", {}).get("dimensions", {})
        b_dims: dict = b_task.get("scores", {}).get("dimensions", {})
        all_dims = sorted(set(a_dims) | set(b_dims))

        if not all_dims:
            lines.append("*(no dimensions scored)*")
            lines.append("")
            continue

        lines += [
            "| Dimension | Run A | Run B | Change |",
            "|---|---|---|---|",
        ]
        for dim in all_dims:
            a_val = a_dims.get(dim)
            b_val = b_dims.get(dim)
            a_icon = "✅" if a_val is True else ("❌" if a_val is False else "⏳")
            b_icon = "✅" if b_val is True else ("❌" if b_val is False else "⏳")

            if dim not in a_dims:
                change = _SYM["added"]
            elif dim not in b_dims:
                change = _SYM["removed"]
            elif a_val is True and b_val is True:
                change = _SYM["same_pass"]
            elif a_val is False and b_val is False:
                change = _SYM["same_fail"]
            elif a_val is None and b_val is None:
                change = _SYM["same_null"]
            elif b_val is True and a_val is not True:
                change = _SYM["improved"]
            elif a_val is True and b_val is not True:
                change = _SYM["regressed"]
            else:
                change = f"{a_icon} → {b_icon}"

            lines.append(f"| `{dim}` | {a_icon} | {b_icon} | {change} |")
        lines.append("")

    return lines


def compare_runs(run_a: str, run_b: str) -> int:
    try:
        a = _load_run(run_a)
        b = _load_run(run_b)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    a_tasks = _task_index(a)
    b_tasks = _task_index(b)

    # Overall score delta
    a_raw = sum(t["scores"].get("total", 0) for t in a_tasks.values())
    a_max = sum(t["scores"].get("max_score", 0) for t in a_tasks.values())
    b_raw = sum(t["scores"].get("total", 0) for t in b_tasks.values())
    b_max = sum(t["scores"].get("max_score", 0) for t in b_tasks.values())

    a_pct = _pct(a_raw, a_max)
    b_pct = _pct(b_raw, b_max)

    if a_pct != "—" and b_pct != "—":
        delta = int(b_pct.rstrip("%")) - int(a_pct.rstrip("%"))
        delta_str = (f"+{delta}%" if delta > 0 else f"{delta}%") if delta != 0 else "no change"
    else:
        delta_str = "—"

    lines: list[str] = [
        f"# Comparison: {run_a} vs {run_b}",
        "",
        "| Field | Run A | Run B |",
        "|---|---|---|",
        f"| Run ID | `{run_a}` | `{run_b}` |",
        f"| Agent | {a.get('agent', '?')} | {b.get('agent', '?')} |",
        f"| Battery | {a.get('battery', '?')} | {b.get('battery', '?')} |",
        f"| Model | {a.get('model', '?')} | {b.get('model', '?')} |",
        f"| Score | {a_raw}/{a_max} ({a_pct}) | {b_raw}/{b_max} ({b_pct}) |",
        f"| Delta | | **{delta_str}** |",
        "",
        "---",
        "",
        "## Dimension Diff",
        "",
    ]
    lines += _diff_tasks(a_tasks, b_tasks)

    # Summary of changes
    all_a_dims = {
        (t, d): v
        for tname, task in a_tasks.items()
        for d, v in task.get("scores", {}).get("dimensions", {}).items()
        for t in [tname]
    }
    all_b_dims = {
        (t, d): v
        for tname, task in b_tasks.items()
        for d, v in task.get("scores", {}).get("dimensions", {}).items()
        for t in [tname]
    }
    improved = [(t, d) for (t, d), v in all_b_dims.items()
                if v is True and all_a_dims.get((t, d)) is not True]
    regressed = [(t, d) for (t, d), v in all_a_dims.items()
                 if v is True and all_b_dims.get((t, d)) is not True]

    lines += [
        "---",
        "",
        "## Summary",
        "",
        f"- **Improved dimensions:** {len(improved)}",
        f"- **Regressed dimensions:** {len(regressed)}",
        f"- **Overall delta:** {delta_str}",
        "",
    ]

    out_path = RESULTS_DIR / f"compare-{run_a}-vs-{run_b}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Comparison written: {out_path.relative_to(out_path.parents[1])}")
    return 0
