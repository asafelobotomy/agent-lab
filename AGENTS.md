# Agent Routing — agent-lab

This repo has two purpose-built agents.

## Roster

| Agent | Use when |
|---|---|
| `Bench` | Fetching agents from an external repo, running benchmark tasks, provisioning Docker workspaces |
| `EvalJudge` | Scoring LLM-judged dimensions, analysing task logs, producing final health reports and recommendations |

## Workflow

```
User: "Bench the Triage agent from myorg/myproject"
  → Bench fetches the repo → provisions workspaces → runs tasks → writes results/
  → Bench delegates to EvalJudge

User: "@EvalJudge results/run-20260516-triage-abc123/run.json"
  → EvalJudge scores LLM dimensions → updates report.md → suggests: lab issue <run-id>
```

## Handoff rules

- `Bench` delegates to `EvalJudge` after every run — it does not interpret results.
- `EvalJudge` is read-only except for updating `results/<run-id>/report.md`.
- Neither agent modifies `registry/`, `batteries/`, or `agents/` during a run.
