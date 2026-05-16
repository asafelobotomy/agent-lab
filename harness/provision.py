"""harness/provision.py — build an isolated workspace for a benchmark task.

Two modes:
  Docker (default): spawns a container from the workspace image, copies the
                    template into /workspace, returns a DockerWorkspace handle.
  tmpdir  (--no-docker): copies the template to a temp directory on the host,
                          returns a TmpdirWorkspace handle.

Both handle classes expose the same interface so invoke.py is mode-agnostic:
  workspace.path          — absolute path to the workspace root (host-side for tmpdir,
                            container-side path for Docker)
  workspace.exec(cmd)     — run a shell command inside the workspace
  workspace.read(relpath) — read a file as text
  workspace.write(relpath, text) — write a file
  workspace.diff()        — return git diff --stat + full patch
  workspace.destroy()     — stop container / remove tmpdir
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

BATTERIES_DIR = Path(__file__).resolve().parents[1] / "batteries"
DOCKER_IMAGE = os.getenv("LAB_DOCKER_IMG", "agent-lab-workspace")
_WORKSPACE_MOUNT = "/workspace"


# ── Shared interface ──────────────────────────────────────────────────────────

class Workspace(Protocol):
    path: Path

    def exec(self, cmd: str) -> tuple[int, str, str]: ...
    def read(self, relpath: str) -> str: ...
    def write(self, relpath: str, text: str) -> None: ...
    def diff(self) -> str: ...
    def destroy(self) -> None: ...


# ── Docker workspace ──────────────────────────────────────────────────────────

class DockerWorkspace:
    """Isolated workspace inside a Docker container."""

    def __init__(self, container_id: str, host_copy: Path) -> None:
        self._id = container_id
        self._host = host_copy          # mirrored copy on host for direct read/write
        self.path = Path(_WORKSPACE_MOUNT)

    def exec(self, cmd: str) -> tuple[int, str, str]:
        r = subprocess.run(
            ["docker", "exec", "--workdir", _WORKSPACE_MOUNT, self._id,
             "bash", "-c", cmd],
            capture_output=True, text=True, check=False,
        )
        return r.returncode, r.stdout, r.stderr

    def read(self, relpath: str) -> str:
        return (self._host / relpath).read_text(encoding="utf-8")

    def write(self, relpath: str, text: str) -> None:
        target = self._host / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        # Sync to container
        subprocess.run(
            ["docker", "cp", str(target),
             f"{self._id}:{_WORKSPACE_MOUNT}/{relpath}"],
            capture_output=True, check=False,
        )

    def diff(self) -> str:
        _, out, _ = self.exec("git diff --stat HEAD && git diff HEAD")
        return out

    def destroy(self) -> None:
        subprocess.run(["docker", "rm", "-f", self._id],
                       capture_output=True, check=False)
        shutil.rmtree(self._host, ignore_errors=True)


# ── Tmpdir workspace ──────────────────────────────────────────────────────────

class TmpdirWorkspace:
    """Workspace as a plain temp directory on the host (no Docker)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.path = root

    def exec(self, cmd: str) -> tuple[int, str, str]:
        r = subprocess.run(
            cmd, shell=True, cwd=self._root,
            capture_output=True, text=True, check=False,
        )
        return r.returncode, r.stdout, r.stderr

    def read(self, relpath: str) -> str:
        return (self._root / relpath).read_text(encoding="utf-8")

    def write(self, relpath: str, text: str) -> None:
        target = self._root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def diff(self) -> str:
        r = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=self._root, capture_output=True, text=True, check=False,
        )
        r2 = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=self._root, capture_output=True, text=True, check=False,
        )
        return r.stdout + r2.stdout

    def destroy(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)


# ── Factory ───────────────────────────────────────────────────────────────────

def _find_template(battery_name: str, workspace_name: str) -> Path:
    """Locate a workspace template from batteries/ directory."""
    candidate = BATTERIES_DIR / battery_name / "workspaces" / workspace_name
    if candidate.is_dir():
        return candidate
    # Fallback: search all batteries
    for ws in BATTERIES_DIR.glob(f"*/workspaces/{workspace_name}"):
        if ws.is_dir():
            return ws
    raise FileNotFoundError(
        f"No workspace template '{workspace_name}' found in batteries/"
    )


def _copy_template(template: Path, dest: Path) -> None:
    """Copy template to dest, initialise git if not already a repo."""
    shutil.copytree(template, dest, symlinks=False)
    git_dir = dest / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "-C", str(dest), "init", "-q"], check=False)
        subprocess.run(
            ["git", "-C", str(dest), "config", "user.email", "bench@agent-lab.local"],
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(dest), "config", "user.name", "Agent Lab"],
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(dest), "add", "-A"], check=False
        )
        subprocess.run(
            ["git", "-C", str(dest), "commit", "-m", "chore: initial workspace snapshot",
             "--quiet", "--allow-empty"],
            check=False,
        )


def provision(
    battery_name: str,
    workspace_name: str,
    *,
    use_docker: bool = True,
) -> Workspace:
    """Provision a fresh isolated workspace and return a handle."""
    template = _find_template(battery_name, workspace_name)
    tmp_root = Path(tempfile.mkdtemp(prefix="agent-lab-ws-"))
    host_copy = tmp_root / "workspace"
    _copy_template(template, host_copy)

    if not use_docker:
        return TmpdirWorkspace(host_copy)

    # Start a long-running container and copy the workspace into it
    r = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", f"agent-lab-{tmp_root.name}",
         "--memory", "512m", "--cpus", "1",
         DOCKER_IMAGE, "sleep", "infinity"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(f"Docker run failed: {r.stderr.strip()}")

    container_id = r.stdout.strip()

    # Copy workspace contents into container
    cp = subprocess.run(
        ["docker", "cp", f"{host_copy}/.", f"{container_id}:{_WORKSPACE_MOUNT}"],
        capture_output=True, check=False,
    )
    if cp.returncode != 0:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(f"Docker cp failed: {cp.stderr.decode()}")

    return DockerWorkspace(container_id, host_copy)
