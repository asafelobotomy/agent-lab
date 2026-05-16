"""harness/fetch.py — pull agents and related files from an external GitHub repo.

Discovers .agent.md, hooks/, skills/, and tool definition files.
Writes a registry.json manifest so bench runs know the provenance.

Usage (via CLI):
    lab fetch owner/repo
    lab fetch owner/repo --force
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"

# Patterns treated as "agent-related" assets worth copying
_AGENT_GLOB = "**/*.agent.md"
_HOOK_DIRS = ("hooks",)
_SKILL_DIRS = ("skills",)
_TOOL_DIRS = ("tools",)


def _git_clone(repo: str, dest: Path) -> subprocess.CompletedProcess[str]:
    token = os.getenv("GITHUB_TOKEN", "")
    url = f"https://{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
    return subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )


def _get_head_sha(clone_dir: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def _copy_agents(src: Path, dest: Path) -> list[dict]:
    """Copy all .agent.md files to dest/agents/ and return metadata."""
    agents_dir = dest / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    found: list[dict] = []
    for agent_file in sorted(src.glob(_AGENT_GLOB)):
        shutil.copy2(agent_file, agents_dir / agent_file.name)
        # Extract name from YAML frontmatter if present
        name = _extract_frontmatter_name(agent_file) or agent_file.stem.replace(".agent", "")
        found.append({"name": name, "file": f"agents/{agent_file.name}"})
    return found


def _copy_dir_if_exists(src: Path, dest: Path, dirname: str) -> bool:
    src_dir = src / dirname
    if src_dir.is_dir():
        shutil.copytree(src_dir, dest / dirname, dirs_exist_ok=True)
        return True
    return False


def _extract_frontmatter_name(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        body = text[3:]
        end = body.find("---")
        if end == -1:
            return None
        for line in body[:end].splitlines():
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def fetch_repo(repo: str, *, force: bool = False) -> int:
    """Clone repo, discover agents, copy to registry/. Returns 0 on success."""
    dest = REGISTRY_DIR / repo.replace("/", "/")  # keeps owner/repo structure
    if dest.exists() and not force:
        print(f"Registry entry already exists: registry/{repo}")
        print("Use --force to overwrite.")
        return 1

    print(f"Fetching {repo}...")
    with tempfile.TemporaryDirectory(prefix="agent-lab-fetch-") as tmp:
        clone_dir = Path(tmp) / "clone"
        r = _git_clone(repo, clone_dir)
        if r.returncode != 0:
            print(f"Clone failed:\n{r.stderr.strip()}")
            return 2

        sha = _get_head_sha(clone_dir)
        print(f"  cloned @ {sha[:8]}")

        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        agents = _copy_agents(clone_dir, dest)
        if not agents:
            print("  warning: no .agent.md files found in this repo")

        copied_extras: list[str] = []
        for d in (*_HOOK_DIRS, *_SKILL_DIRS, *_TOOL_DIRS):
            if _copy_dir_if_exists(clone_dir, dest, d):
                copied_extras.append(d)

        manifest = {
            "repo": repo,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha": sha,
            "agents": agents,
            "extras": copied_extras,
        }
        (dest / "registry.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    print(f"  {len(agents)} agent(s) registered: {[a['name'] for a in agents]}")
    if copied_extras:
        print(f"  extras copied: {copied_extras}")
    print(f"  saved to registry/{repo}")
    return 0
