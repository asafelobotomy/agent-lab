#!/usr/bin/env python3
"""agent-lab CLI.

Commands:
  fetch  <owner/repo>              Pull agents + related files from a GitHub repo into registry/
  bench  <agent> [--battery NAME]  Run the named agent through a test battery in Docker
  report <run-id>                  Re-render the report for a completed run
  issue  <run-id>                  Print (and optionally submit) a GitHub issue from a report

Environment:
  GITHUB_TOKEN    — PAT with repo:read and (optionally) issues:write scope
  LAB_LLM_MODEL   — override the LLM model used during bench runs
  LAB_DOCKER_IMG  — override the Docker workspace image (default: agent-lab-workspace)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent


def _cmd_fetch(args: argparse.Namespace) -> int:
    from harness.fetch import fetch_repo
    return fetch_repo(args.repo, force=args.force)


def _cmd_bench(args: argparse.Namespace) -> int:
    from harness.bench import run_bench
    return run_bench(
        agent_name=args.agent,
        battery_name=args.battery,
        source_repo=args.repo,
        model=args.model,
        no_docker=args.no_docker,
    )


def _cmd_report(args: argparse.Namespace) -> int:
    from reporters.bench_report import render_report
    return render_report(run_id=args.run_id)


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

    # --- bench ---
    p_bench = sub.add_parser("bench", help="Run a benchmark")
    p_bench.add_argument("agent", help="Agent name (e.g. Triage, Review, Commit)")
    p_bench.add_argument("--battery", default=None, help="Battery name (default: auto-detected)")
    p_bench.add_argument("--repo", default=None, help="Restrict to this registry entry (owner/repo)")
    p_bench.add_argument("--model", default=None, help="Override LLM model")
    p_bench.add_argument("--no-docker", action="store_true", help="Run in tmpdir instead of Docker (dev mode)")
    p_bench.set_defaults(func=_cmd_bench)

    # --- report ---
    p_report = sub.add_parser("report", help="Re-render a run report")
    p_report.add_argument("run_id", help="Run ID (e.g. run-20260516-triage-abc123)")
    p_report.set_defaults(func=_cmd_report)

    # --- issue ---
    p_issue = sub.add_parser("issue", help="Generate a GitHub issue from a run report")
    p_issue.add_argument("run_id", help="Run ID")
    p_issue.add_argument("--repo", default=None, help="owner/repo to submit the issue to")
    p_issue.add_argument("--submit", action="store_true", help="Submit the issue via GitHub API")
    p_issue.set_defaults(func=_cmd_issue)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
