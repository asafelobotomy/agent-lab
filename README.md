# agent-lab

Live benchmarking for AI coding agents — provision isolated workspaces, run agents against scored task batteries, and generate structured reports.

```
lab fetch owner/repo   →   lab bench Agent   →   lab judge <run-id>   →   lab issue <run-id>
```

---

## How it works

1. **Fetch** — clone a GitHub repo, discover `.agent.md` definitions, write a registry entry
2. **Bench** — for each task in a battery: provision a workspace (Docker or tmpdir), run the agentic LLM loop with real tool calls, score the result, log everything as JSONL
3. **Judge** — call the LLM to fill in any deferred (`llm`-type) scoring dimensions
4. **Report** — render a markdown report; optionally open a GitHub issue

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python ≥ 3.11 | |
| Docker | Only needed for isolated runs; skip with `--no-docker` |
| `GITHUB_TOKEN` | PAT with `repo:read` scope; add `issues:write` to submit issue reports |
| `ripgrep` (`rg`) | Used by the `grep_search` tool surface |

---

## Installation

```bash
git clone https://github.com/your-org/agent-lab
cd agent-lab
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Build the Docker workspace image (one-time):

```bash
docker build -f docker/Dockerfile.workspace -t agent-lab-workspace .
```

---

## Quickstart

```bash
# 1. Pull an agent from GitHub into the registry
lab fetch myorg/myproject

# 2. Run the agent through its battery (auto-detected by target_agents)
lab bench Triage --repo myorg/myproject

# 3. Score any LLM-judged dimensions (requires GITHUB_TOKEN with models:read)
lab judge run-20260516-143022-triage

# 4. Generate a GitHub issue report (add --submit to open it)
lab issue run-20260516-143022-triage --repo myorg/myproject
```

Results land in `results/<run-id>/`:

```
results/run-20260516-143022-triage/
├── run.json          # structured metadata + per-task scores
├── report.md         # rendered markdown report
├── issue.md          # formatted GitHub issue body
└── tasks/
    ├── triage-typo-rename.jsonl
    └── ...           # full JSONL event log per task
```

---

## CLI reference

```
lab fetch  <owner/repo>             Pull agents from a GitHub repo
           --force                  Overwrite existing registry entry

lab bench  <agent>                  Run a benchmark
           --battery  NAME          Battery to use (default: auto-detected)
           --repo     OWNER/REPO    Restrict to this registry entry
           --model    MODEL         Override LLM model
           --no-docker              Run in tmpdir instead of Docker (dev mode)

lab judge  <run-id>                 Score LLM-judged dimensions
           --model    MODEL         Override LLM model for judging

lab report <run-id>                 Re-render report.md from run.json

lab issue  <run-id>                 Generate a GitHub issue report
           --repo     OWNER/REPO    Target repo for the issue
           --submit                 Submit the issue via GitHub API
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | **Required.** PAT with `repo:read`; add `issues:write` for `--submit` |
| `LAB_LLM_MODEL` | `openai/gpt-4o-mini` | Override the model used for bench runs |
| `LAB_REPO` | `your-org/agent-lab` | Repository shown in generated issue footers |
| `LAB_DOCKER_IMG` | `agent-lab-workspace` | Docker image for workspace containers |
| `LAB_DOCKER_CMD` | `docker` | Docker binary (e.g. `sudo docker`) |

---

## Batteries

A battery is a directory under `batteries/` containing `battery.json` and workspace templates:

```
batteries/
└── triage-core/
    ├── battery.json
    └── workspaces/
        └── blank/
            └── README.md
```

### `battery.json` schema

```jsonc
{
  "name": "triage-core",
  "version": "1.0.0",
  "target_agents": ["Triage"],          // agents this battery auto-attaches to
  "tasks": [
    {
      "name": "triage-typo-rename",
      "workspace": "blank",             // name of a folder under workspaces/
      "prompt": "Rename the function…",
      "scoring_criteria": [
        // Structural — scored instantly
        { "dimension": "format_valid", "type": "keyword",        "terms": ["Tier:"] },
        { "dimension": "tier_correct", "type": "keyword",        "terms": ["Simple"] },
        { "dimension": "no_blocker",   "type": "keyword_absent", "terms": ["Blocked"] },
        // Diff-based
        { "dimension": "renamed",      "type": "diff_contains",  "terms": ["calculate_total"] },
        // LLM-judged — deferred to `lab judge`
        { "dimension": "quality",      "type": "llm",
          "criterion": "Did the agent explain its reasoning clearly?" }
      ]
    }
  ]
}
```

### Criterion types

| Type | Passes when |
|---|---|
| `keyword` | Response contains **any** of `terms` (case-insensitive) |
| `keyword_absent` | Response contains **none** of `terms` |
| `diff_contains` | Workspace diff contains **all** of `terms` |
| `diff_absent` | Workspace diff contains **none** of `terms` |
| `llm` | LLM judge returns `PASS` (scored by `lab judge`) |

### `batteries/core/`

Reserved namespace for batteries that ship with agent-lab itself (as opposed to batteries fetched from external repos). Currently empty.

---

## Writing an agent

Agents are `.agent.md` files with YAML frontmatter followed by the system prompt body:

```markdown
---
name: Triage
description: "classifies task complexity and identifies blockers"
tools: [codebase]
model: openai/gpt-4o
---

You are a triage agent. For every task you receive, respond with...
```

| Frontmatter key | Description |
|---|---|
| `name` | Agent name — used by `lab bench <name>` and `target_agents` matching |
| `description` | Short summary shown in `@Agent` routing |
| `tools` | Tool surfaces: `codebase`, `filesystem`, `search`, `terminal` |
| `model` | Default model (overridden by `--model` or `LAB_LLM_MODEL`) |

---

## Development

```bash
# Run tests (no Docker, no network required)
python -m pytest

# Run with coverage
python -m pytest --cov=harness --cov=reporters --cov=judges

# Lint
ruff check .
```

---

## Project structure

```
agent-lab/
├── cli.py                  # Entry point — lab fetch / bench / judge / report / issue
├── harness/
│   ├── bench.py            # Benchmark orchestration
│   ├── fetch.py            # GitHub repo fetching + registry
│   ├── invoke.py           # Agentic LLM loop
│   ├── log.py              # JSONL event logger
│   ├── provision.py        # Docker + tmpdir workspace management
│   ├── score.py            # Structural scoring (keyword / diff)
│   └── tools/              # Tool surfaces exposed to the agent
│       ├── filesystem.py
│       ├── search.py
│       └── terminal.py
├── judges/
│   └── evaljudge.py        # LLM-judged dimension scoring
├── reporters/
│   ├── bench_report.py     # Markdown report renderer
│   └── github_issue.py     # GitHub issue formatter + API submission
├── batteries/              # Built-in task batteries
│   ├── core/               # Namespace for lab-shipped batteries
│   └── triage-core/        # Core triage classification battery (5 tasks)
├── docker/
│   └── Dockerfile.workspace
├── registry/               # Populated by `lab fetch` (git-ignored content)
├── results/                # Populated by `lab bench` (git-ignored content)
└── tests/
```
