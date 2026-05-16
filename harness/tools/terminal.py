"""harness/tools/terminal.py — sandboxed terminal tool backed by the workspace.

Commands are executed inside the Docker container (or the tmpdir workspace).
A hard allowlist prevents destructive or network-reaching commands.
"""
from __future__ import annotations

from harness.tools import register
from harness.provision import Workspace

# Commands that are unconditionally blocked regardless of context.
_BLOCKED_PREFIXES = (
    "rm -rf /",
    "dd if=",
    "mkfs",
    "wget ",
    "curl ",
    "nc ",
    "ncat ",
    "ssh ",
    "scp ",
    "git push",
    "git remote add",
    "pip install",   # package installs change container state unpredictably
    "npm install",
    "apt-get",
    "sudo",
)


def _is_blocked(cmd: str) -> bool:
    stripped = cmd.strip().lower()
    return any(stripped.startswith(p) for p in _BLOCKED_PREFIXES)


@register("run_in_terminal", {
    "name": "run_in_terminal",
    "description": (
        "Run a shell command in the workspace. "
        "Network access and package installation are disabled. "
        "Use for git status/log/diff, file listing, test runs, linting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run."},
            "explanation": {"type": "string", "description": "One-sentence description of purpose."},
        },
        "required": ["command", "explanation"],
    },
})
def run_in_terminal(*, workspace: Workspace, command: str,
                    explanation: str = "") -> str:
    if _is_blocked(command):
        return f"[blocked] command not permitted in bench harness: {command!r}"

    returncode, stdout, stderr = workspace.exec(command)
    parts = []
    if stdout.strip():
        parts.append(stdout.rstrip())
    if stderr.strip():
        parts.append(f"[stderr]\n{stderr.rstrip()}")
    if not parts:
        parts.append(f"(exit {returncode})")
    return "\n".join(parts)
