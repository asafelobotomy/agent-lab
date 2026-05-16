#!/usr/bin/env python3
"""agent-lab CLI.

Commands:
  validate <battery>               Validate a battery.json against the schema
  fetch  <owner/repo>              Pull agents + related files from a GitHub repo into registry/
  bench  <agent> [--battery NAME]  Run the named agent through a test battery in Docker
                                   (also runs the Control baseline and auto-compares by default)
  retry  <run-id>                  Re-run failed tasks from a previous run
  report <run-id>                  Re-render the report for a completed run
  judge  <run-id>                  Score LLM-judged dimensions via EvalJudge
  compare <run-a> <run-b>          Diff two runs dimension-by-dimension
  issue  <run-id>                  Print (and optionally submit) a GitHub issue from a report

Environment:
  GITHUB_TOKEN    — PAT with repo:read and (optionally) issues:write scope
  LAB_LLM_MODEL   — override the LLM model used during bench runs
  LAB_DOCKER_IMG  — override the Docker workspace image (default: agent-lab-workspace)
  LAB_DOCKER_CMD  — override the docker binary (default: docker; use "sudo docker" if
                    the current user is not yet in the docker group)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent


def _cmd_fetch(args: argparse.Namespace) -> int:
    from harness.fetch import fetch_repo
    return fetch_repo(args.repo, force=args.force)


def _cmd_validate(args: argparse.Namespace) -> int:
    from harness.validate import validate_battery
    return validate_battery(args.battery)


def _cmd_bench(args: argparse.Namespace) -> int:
    import json
    from harness.bench import run_bench, RESULTS_DIR
    from reporters.compare import compare_runs

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
    rc, run_id = run_bench(
        agent_name=args.agent,
        battery_name=args.battery,
        source_repo=args.repo,
        model=args.model,
        no_docker=args.no_docker,
        limit=args.limit,
        tags=tags,
        seeds=args.seeds,
    )
    if rc != 0 or run_id is None:
        return rc

    # Control baseline — skip only if benching Control itself or user opted out
    if not args.no_control and args.agent.lower() != "control":
        # Resolve which battery was used (handles auto-detection case)
        battery_used = args.battery
        if battery_used is None:
            run_json = RESULTS_DIR / run_id / "run.json"
            if run_json.exists():
                battery_used = json.loads(run_json.read_text(encoding="utf-8")).get("battery")

        print()
        print("── Control baseline " + "─" * 57)
        ctrl_rc, ctrl_run_id = run_bench(
            agent_name="Control",
            battery_name=battery_used,
            source_repo=None,
            model=args.model,
            no_docker=args.no_docker,
            limit=args.limit,
            tags=tags,
            seeds=args.seeds,
        )
        print()
        if ctrl_rc == 0 and ctrl_run_id:
            print("── Agent vs Control " + "─" * 57)
            compare_runs(run_a=ctrl_run_id, run_b=run_id)
        else:
            print(
                "  Control run did not complete — skipping comparison.\n"
                "  The Control agent lives at: registry/builtin/agents/control.agent.md"
            )

    return rc


def _cmd_retry(args: argparse.Namespace) -> int:
    from harness.bench import resume_bench
    rc, _ = resume_bench(
        run_id=args.run_id,
        model=args.model,
        no_docker=args.no_docker,
        seeds=args.seeds,
    )
    return rc


def _cmd_report(args: argparse.Namespace) -> int:
    from reporters.bench_report import render_report
    return render_report(run_id=args.run_id)


def _cmd_judge(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path
    from judges.evaljudge import score_llm_dimensions

    # Warn if judging with the same model used for the bench run (self-judge bias).
    run_json = Path(__file__).parent / "results" / args.run_id / "run.json"
    if run_json.exists():
        run_meta = json.loads(run_json.read_text(encoding="utf-8"))
        bench_model = run_meta.get("model", "")
        judge_model = args.model or os.getenv("LAB_LLM_MODEL") or ""
        if judge_model and judge_model == bench_model:
            print(
                f"Warning: judging with the same model used during the bench run "
                f"({bench_model}). Consider using a different model to avoid "
                f"self-evaluation bias (e.g. --model openai/gpt-4o).",
                file=sys.stderr,
            )

    try:
        filled = score_llm_dimensions(run_id=args.run_id, model=args.model)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"\nScored {filled} LLM dimension(s).")
    print(f"Report updated: results/{args.run_id}/report.md")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    from reporters.compare import compare_runs
    return compare_runs(run_a=args.run_a, run_b=args.run_b)


def _cmd_issue(args: argparse.Namespace) -> int:
    from reporters.github_issue import generate_issue
    return generate_issue(run_id=args.run_id, submit=args.submit, repo=args.repo)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lab",
        description="agent-lab — live agent benchmarking",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- fetch ---
    p_fetch = sub.add_parser("fetch", help="Pull agents from a GitHub repo")
    p_fetch.add_argument("repo", help="owner/repo (e.g. myorg/myproject)")
    p_fetch.add_argument("--force", action="store_true", help="Overwrite existing registry entry")
    p_fetch.set_defaults(func=_cmd_fetch)

    # --- validate ---
    p_validate = sub.add_parser("validate", help="Validate a battery.json against the schema")
    p_validate.add_argument("battery", help="Battery name (e.g. triage-core) or path to battery.json")
    p_validate.set_defaults(func=_cmd_validate)

    # --- bench ---
    p_bench = sub.add_parser("bench", help="Run a benchmark")
    p_bench.add_argument("agent", help="Agent name (e.g. Triage, Review, Commit)")
    p_bench.add_argument("--battery", default=None, help="Battery name (default: auto-detected)")
    p_bench.add_argument("--repo", default=None, help="Restrict to this registry entry (owner/repo)")
    p_bench.add_argument("--model", default=None, help="Override LLM model")
    p_bench.add_argument("--no-docker", action="store_true", help="Run in tmpdir instead of Docker (dev mode)")
    p_bench.add_argument("--limit", type=int, default=None, metavar="N",
                         help="Run only the first N tasks (quick smoke test)")
    p_bench.add_argument("--tags", default=None, metavar="TAGS",
                         help="Comma-separated tags to filter tasks (e.g. smoke,tier-blocked)")
    p_bench.add_argument("--seeds", type=int, default=1, metavar="N",
                         help="Run each task N times; report pass-rate + Wilson CI (default: 1)")
    p_bench.add_argument("--no-control", action="store_true",
                         help="Skip the automatic Control-agent baseline run and comparison")
    p_bench.set_defaults(func=_cmd_bench)

    # --- retry ---
    p_retry = sub.add_parser("retry", help="Re-run failed tasks from a previous run")
    p_retry.add_argument("run_id", help="Run ID to retry (e.g. run-20260516-143022-triage)")
    p_retry.add_argument("--model", default=None, help="Override LLM model")
    p_retry.add_argument("--no-docker", action="store_true", help="Use tmpdir instead of Docker")
    p_retry.add_argument("--seeds", type=int, default=1, metavar="N",
                         help="Number of seeds per task on retry")
    p_retry.set_defaults(func=_cmd_retry)

    # --- report ---
    p_report = sub.add_parser("report", help="Re-render a run report")
    p_report.add_argument("run_id", help="Run ID (e.g. run-20260516-triage-abc123)")
    p_report.set_defaults(func=_cmd_report)

    # --- judge ---
    p_judge = sub.add_parser("judge", help="Score LLM-judged dimensions for a completed run")
    p_judge.add_argument("run_id", help="Run ID (e.g. run-20260516-143022-triage)")
    p_judge.add_argument("--model", default=None, help="Override LLM model for judging")
    p_judge.set_defaults(func=_cmd_judge)

    # --- compare ---
    p_compare = sub.add_parser("compare", help="Diff two benchmark runs dimension-by-dimension")
    p_compare.add_argument("run_a", help="Baseline run ID")
    p_compare.add_argument("run_b", help="Candidate run ID")
    p_compare.set_defaults(func=_cmd_compare)

    # --- issue ---
    p_issue = sub.add_parser("issue", help="Generate a GitHub issue from a run report")
    p_issue.add_argument("run_id", help="Run ID")
    p_issue.add_argument("--repo", default=None, help="owner/repo to submit the issue to")
    p_issue.add_argument("--submit", action="store_true", help="Submit the issue via GitHub API")
    p_issue.set_defaults(func=_cmd_issue)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()  # pragma: no cover
