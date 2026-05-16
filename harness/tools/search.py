"""harness/tools/search.py — search tools backed by the workspace."""
from __future__ import annotations

import subprocess
from pathlib import Path

from harness.tools import register
from harness.provision import Workspace


def _workspace_root(workspace: Workspace) -> Path:
    return workspace.path if isinstance(workspace.path, Path) else Path(workspace.path)


@register("grep_search", {
    "name": "grep_search",
    "description": "Search for a text pattern across workspace files.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Plain text or regex pattern."},
            "isRegexp": {"type": "boolean", "default": False},
            "includePattern": {"type": "string", "description": "Glob to restrict search (optional)."},
            "maxResults": {"type": "integer", "default": 50},
        },
        "required": ["query", "isRegexp"],
    },
})
def grep_search(*, workspace: Workspace, query: str, isRegexp: bool = False,
                includePattern: str | None = None, maxResults: int = 50) -> str:
    root = _workspace_root(workspace)
    cmd = ["rg", "--line-number", "--color=never", f"--max-count={maxResults}"]
    if not isRegexp:
        cmd.append("--fixed-strings")
    if includePattern:
        cmd += ["--glob", includePattern]
    cmd += [query, str(root)]

    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode == 0:
        # Make paths relative to workspace root
        output = r.stdout.replace(str(root) + "/", "")
        lines = output.splitlines()
        if len(lines) >= maxResults:
            lines.append(f"... (truncated at {maxResults} results)")
        return "\n".join(lines)
    if r.returncode == 1:
        return "(no matches)"
    return f"[error] grep failed: {r.stderr.strip()}"


@register("file_search", {
    "name": "file_search",
    "description": "Find files by name pattern in the workspace.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Glob pattern (e.g. **/*.py)."},
            "maxResults": {"type": "integer", "default": 50},
        },
        "required": ["query"],
    },
})
def file_search(*, workspace: Workspace, query: str, maxResults: int = 50) -> str:
    root = _workspace_root(workspace)
    matches = sorted(root.glob(query))[:maxResults]
    if not matches:
        return "(no matches)"
    lines = [str(p.relative_to(root)) for p in matches]
    if len(matches) == maxResults:
        lines.append(f"... (truncated at {maxResults} results)")
    return "\n".join(lines)


@register("semantic_search", {
    "name": "semantic_search",
    "description": "Keyword-based search across workspace files (semantic search not available in harness).",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
        },
        "required": ["query"],
    },
})
def semantic_search(*, workspace: Workspace, query: str) -> str:
    # Fall back to grep in the harness — true semantic search requires an embedding model.
    return grep_search(
        workspace=workspace,
        query=query,
        isRegexp=False,
        maxResults=30,
    )
