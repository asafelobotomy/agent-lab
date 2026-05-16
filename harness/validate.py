"""harness/validate.py — validate a battery.json before running a bench.

Catches malformed batteries early (missing fields, bad criterion types,
missing workspace directories) so failures don't surface mid-run.

Usage:
    lab validate <battery-name>
    lab validate path/to/battery.json

Exit codes:
    0 — valid (may have warnings)
    1 — one or more schema errors found
"""
from __future__ import annotations

import json
from pathlib import Path

BATTERIES_DIR = Path(__file__).resolve().parents[1] / "batteries"

VALID_CRITERION_TYPES = frozenset(
    {"keyword", "keyword_absent", "diff_contains", "diff_absent", "llm"}
)
TERM_REQUIRED_TYPES = frozenset(
    {"keyword", "keyword_absent", "diff_contains", "diff_absent"}
)


def _find_battery_file(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix == ".json" and p.exists():
        return p
    candidate = BATTERIES_DIR / name_or_path / "battery.json"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Battery '{name_or_path}' not found. "
        "Provide a battery name (e.g. triage-core) or a path to battery.json."
    )


def _workspace_exists(battery_dir: Path, workspace_name: str) -> bool:
    """Check battery-local workspaces, then the shared core workspaces."""
    return (
        (battery_dir / "workspaces" / workspace_name).is_dir()
        or (BATTERIES_DIR / "core" / "workspaces" / workspace_name).is_dir()
    )


def validate_battery(name_or_path: str) -> int:
    """Validate a battery file. Returns 0 if valid, 1 if errors found."""
    try:
        battery_file = _find_battery_file(name_or_path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    try:
        battery = json.loads(battery_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {battery_file}: {exc}")
        return 1

    battery_dir = battery_file.parent
    errors: list[str] = []
    warnings: list[str] = []

    # ── top-level ─────────────────────────────────────────────────────────────
    if not isinstance(battery.get("name"), str) or not battery["name"].strip():
        errors.append("top-level 'name' is missing or empty")
    if not isinstance(battery.get("target_agents"), list) or not battery["target_agents"]:
        errors.append("'target_agents' must be a non-empty list")
    if not isinstance(battery.get("tasks"), list) or not battery["tasks"]:
        errors.append("'tasks' must be a non-empty list")
    if "version" not in battery:
        warnings.append("no 'version' field — recommended for reproducibility")

    tasks = battery.get("tasks") if isinstance(battery.get("tasks"), list) else []
    task_names_seen: set[str] = set()

    for i, task in enumerate(tasks):
        raw_name = task.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        label = f"task[{i}] '{name}'" if name else f"task[{i}]"

        if not name:
            errors.append(f"{label}: 'name' is missing or empty")
        elif name in task_names_seen:
            errors.append(f"{label}: duplicate task name")
        task_names_seen.add(name)

        raw_ws = task.get("workspace")
        ws = raw_ws.strip() if isinstance(raw_ws, str) else ""
        if not ws:
            errors.append(f"{label}: 'workspace' is missing or empty")
        elif not _workspace_exists(battery_dir, ws):
            errors.append(
                f"{label}: workspace '{ws}' not found in "
                f"batteries/{battery_dir.name}/workspaces/ or batteries/core/workspaces/"
            )

        raw_prompt = task.get("prompt")
        if not isinstance(raw_prompt, str) or not raw_prompt.strip():
            errors.append(f"{label}: 'prompt' is missing or empty")

        criteria = task.get("scoring_criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{label}: 'scoring_criteria' must be a non-empty list")
        else:
            dims_seen: set[str] = set()
            for j, crit in enumerate(criteria):
                raw_dim = crit.get("dimension")
                dim = raw_dim.strip() if isinstance(raw_dim, str) else ""
                clabel = f"{label} criterion[{j}] '{dim}'" if dim else f"{label} criterion[{j}]"

                if not dim:
                    errors.append(f"{clabel}: 'dimension' is missing or empty")
                elif dim in dims_seen:
                    errors.append(f"{clabel}: duplicate dimension name in this task")
                dims_seen.add(dim)

                kind = crit.get("type")
                if kind not in VALID_CRITERION_TYPES:
                    errors.append(
                        f"{clabel}: type '{kind}' is invalid; "
                        f"must be one of: {', '.join(sorted(VALID_CRITERION_TYPES))}"
                    )
                    continue

                if kind in TERM_REQUIRED_TYPES:
                    terms = crit.get("terms")
                    if not isinstance(terms, list) or not terms:
                        errors.append(
                            f"{clabel}: type '{kind}' requires a non-empty 'terms' list"
                        )
                    elif not all(isinstance(t, str) for t in terms):
                        errors.append(f"{clabel}: all 'terms' entries must be strings")

                if kind == "llm" and (
                    not isinstance(crit.get("criterion"), str)
                    or not crit["criterion"].strip()
                ):
                    errors.append(
                        f"{clabel}: type 'llm' requires a non-empty 'criterion' string"
                    )

        if "notes" not in task:
            warnings.append(f"{label}: no 'notes' field (recommended)")

    # ── report ────────────────────────────────────────────────────────────────
    print(f"Validating: {battery_file}")
    print()
    for w in warnings:
        print(f"  ⚠  {w}")
    if warnings:
        print()
    for e in errors:
        print(f"  ✗  {e}")
    if errors:
        print()

    n_tasks = len(tasks)
    if errors:
        print(f"✗  {len(errors)} error(s), {len(warnings)} warning(s)  [{n_tasks} tasks checked]")
        return 1

    print(f"✓  {n_tasks} task(s) valid, {len(warnings)} warning(s)")
    return 0
