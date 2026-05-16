"""Tests for harness/provision.py — TmpdirWorkspace, DockerWorkspace, factory."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from harness.provision import (
    TmpdirWorkspace,
    DockerWorkspace,
    _find_template,
    _copy_template,
    provision,
)


# ── TmpdirWorkspace ───────────────────────────────────────────────────────────

def test_tmpdir_exec(tmp_path):
    ws = TmpdirWorkspace(tmp_path)
    rc, out, err = ws.exec("echo hello")
    assert rc == 0
    assert "hello" in out
    assert err == ""


def test_tmpdir_exec_nonzero(tmp_path):
    ws = TmpdirWorkspace(tmp_path)
    rc, out, err = ws.exec("exit 42")
    assert rc == 42


def test_tmpdir_read_write(tmp_path):
    ws = TmpdirWorkspace(tmp_path)
    ws.write("subdir/file.txt", "hello world")
    assert (tmp_path / "subdir" / "file.txt").exists()
    assert ws.read("subdir/file.txt") == "hello world"


def test_tmpdir_write_creates_parents(tmp_path):
    ws = TmpdirWorkspace(tmp_path)
    ws.write("a/b/c/deep.py", "# deep")
    assert (tmp_path / "a" / "b" / "c" / "deep.py").read_text() == "# deep"


def test_tmpdir_diff(tmp_path):
    ws = TmpdirWorkspace(tmp_path)
    # git init so diff works
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "x@x.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "X"], cwd=tmp_path, check=True)
    (tmp_path / "file.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet", "--allow-empty"], cwd=tmp_path, check=True)
    (tmp_path / "file.py").write_text("x = 2\n")
    diff = ws.diff()
    # Should contain the modification or at least not crash
    assert isinstance(diff, str)


def test_tmpdir_diff_no_git(tmp_path):
    ws = TmpdirWorkspace(tmp_path)
    # No git repo — diff should still return a string (empty or error)
    diff = ws.diff()
    assert isinstance(diff, str)


def test_tmpdir_destroy(tmp_path):
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "file.txt").write_text("data")
    ws = TmpdirWorkspace(ws_root)
    ws.destroy()
    assert not ws_root.exists()


def test_tmpdir_path_attribute(tmp_path):
    ws = TmpdirWorkspace(tmp_path)
    assert ws.path == tmp_path


# ── DockerWorkspace ───────────────────────────────────────────────────────────

def _make_docker_ws(tmp_path: Path) -> DockerWorkspace:
    host_copy = tmp_path / "workspace"
    host_copy.mkdir()
    (host_copy / "main.py").write_text("print('hi')\n")
    return DockerWorkspace("container-abc123", host_copy)


def test_docker_exec(tmp_path):
    ws = _make_docker_ws(tmp_path)
    mock_result = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("harness.provision.subprocess.run", return_value=mock_result) as mock_run:
        rc, out, err = ws.exec("ls")
    assert rc == 0
    assert out == "ok\n"
    assert err == ""
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "exec" in args
    assert "container-abc123" in args


def test_docker_read(tmp_path):
    ws = _make_docker_ws(tmp_path)
    content = ws.read("main.py")
    assert "print('hi')" in content


def test_docker_write(tmp_path):
    ws = _make_docker_ws(tmp_path)
    mock_result = MagicMock(returncode=0)
    with patch("harness.provision.subprocess.run", return_value=mock_result):
        ws.write("new_file.py", "# new")
    assert (tmp_path / "workspace" / "new_file.py").read_text() == "# new"


def test_docker_write_creates_subdirs(tmp_path):
    ws = _make_docker_ws(tmp_path)
    mock_result = MagicMock(returncode=0)
    with patch("harness.provision.subprocess.run", return_value=mock_result):
        ws.write("sub/dir/file.py", "x = 1")
    assert (tmp_path / "workspace" / "sub" / "dir" / "file.py").exists()


def test_docker_diff(tmp_path):
    ws = _make_docker_ws(tmp_path)
    mock_result = MagicMock(returncode=0, stdout="diff output\n", stderr="")
    with patch("harness.provision.subprocess.run", return_value=mock_result):
        diff = ws.diff()
    assert "diff output" in diff


def test_docker_destroy(tmp_path):
    ws = _make_docker_ws(tmp_path)
    mock_result = MagicMock(returncode=0)
    with patch("harness.provision.subprocess.run", return_value=mock_result) as mock_run:
        ws.destroy()
    # rm -f called
    called_cmds = [mock_run.call_args_list[i][0][0] for i in range(mock_run.call_count)]
    assert any("rm" in c for c in called_cmds)


def test_docker_path_attribute(tmp_path):
    ws = _make_docker_ws(tmp_path)
    assert str(ws.path) == "/workspace"


# ── _find_template ────────────────────────────────────────────────────────────

def test_find_template_direct(tmp_path, monkeypatch):
    """Direct match: batteries/<battery>/workspaces/<name>/"""
    ws_dir = tmp_path / "mybattery" / "workspaces" / "myws"
    ws_dir.mkdir(parents=True)
    monkeypatch.setattr("harness.provision.BATTERIES_DIR", tmp_path)
    result = _find_template("mybattery", "myws")
    assert result == ws_dir


def test_find_template_fallback(tmp_path, monkeypatch):
    """Fallback search when battery doesn't directly own the workspace."""
    ws_dir = tmp_path / "other" / "workspaces" / "shared-ws"
    ws_dir.mkdir(parents=True)
    monkeypatch.setattr("harness.provision.BATTERIES_DIR", tmp_path)
    result = _find_template("nonexistent-battery", "shared-ws")
    assert result == ws_dir


def test_find_template_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.provision.BATTERIES_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="No workspace template"):
        _find_template("battery", "does-not-exist")


# ── _copy_template ────────────────────────────────────────────────────────────

def test_copy_template_with_git_init(tmp_path):
    """Template without .git → should git init and commit."""
    template = tmp_path / "template"
    template.mkdir()
    (template / "main.py").write_text("print('hello')")
    dest = tmp_path / "dest"
    _copy_template(template, dest)
    assert (dest / "main.py").exists()
    assert (dest / ".git").exists()


def test_copy_template_existing_git_not_reinitialised(tmp_path):
    """Template already has .git → should copytree only."""
    template = tmp_path / "tmpl"
    template.mkdir()
    (template / ".git").mkdir()
    (template / "code.py").write_text("x = 1")
    dest = tmp_path / "dest"
    with patch("harness.provision.subprocess.run") as mock_run:
        _copy_template(template, dest)
    # subprocess.run should NOT be called (no git init needed)
    mock_run.assert_not_called()


# ── provision() ──────────────────────────────────────────────────────────────

def test_provision_tmpdir(tmp_path, monkeypatch):
    """provision() with use_docker=False returns TmpdirWorkspace."""
    template = tmp_path / "bat" / "workspaces" / "ws1"
    template.mkdir(parents=True)
    (template / "README.md").write_text("# workspace")
    monkeypatch.setattr("harness.provision.BATTERIES_DIR", tmp_path)

    with patch("harness.provision.tempfile.mkdtemp", return_value=str(tmp_path / "run")):
        (tmp_path / "run").mkdir(exist_ok=True)
        ws = provision("bat", "ws1", use_docker=False)

    assert isinstance(ws, TmpdirWorkspace)
    ws.destroy()


def test_provision_docker_success(tmp_path, monkeypatch):
    """provision() with use_docker=True returns DockerWorkspace on success."""
    template = tmp_path / "bat" / "workspaces" / "ws2"
    template.mkdir(parents=True)
    (template / "app.py").write_text("# app")
    monkeypatch.setattr("harness.provision.BATTERIES_DIR", tmp_path)

    run_dir = tmp_path / "run-docker"
    run_dir.mkdir()

    success = MagicMock(returncode=0, stdout="container-xyz\n", stderr="")
    cp_success = MagicMock(returncode=0, stdout=b"", stderr=b"")

    call_count = [0]
    def fake_run(cmd, *args, **kwargs):
        call_count[0] += 1
        # First call = docker run, second = docker cp, git init calls come from _copy_template
        cmd_str = " ".join(str(c) for c in cmd)
        if "docker" in cmd_str and "run" in cmd_str:
            return success
        if "docker" in cmd_str and "cp" in cmd_str:
            return cp_success
        # git calls
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.provision.tempfile.mkdtemp", return_value=str(run_dir)), \
         patch("harness.provision.subprocess.run", side_effect=fake_run):
        ws = provision("bat", "ws2", use_docker=True)

    assert isinstance(ws, DockerWorkspace)


def test_provision_docker_run_fails(tmp_path, monkeypatch):
    """provision() raises RuntimeError if docker run fails."""
    template = tmp_path / "bat" / "workspaces" / "ws3"
    template.mkdir(parents=True)
    (template / "f.py").write_text("x")
    monkeypatch.setattr("harness.provision.BATTERIES_DIR", tmp_path)

    run_dir = tmp_path / "run-fail"
    run_dir.mkdir()

    def fake_run(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "run" in cmd_str and "docker" in cmd_str:
            return MagicMock(returncode=1, stdout="", stderr="image not found")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.provision.tempfile.mkdtemp", return_value=str(run_dir)), \
         patch("harness.provision.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="Docker run failed"):
            provision("bat", "ws3", use_docker=True)


def test_provision_docker_cp_fails(tmp_path, monkeypatch):
    """provision() raises RuntimeError if docker cp fails."""
    template = tmp_path / "bat" / "workspaces" / "ws4"
    template.mkdir(parents=True)
    (template / "f.py").write_text("x")
    monkeypatch.setattr("harness.provision.BATTERIES_DIR", tmp_path)

    run_dir = tmp_path / "run-cpfail"
    run_dir.mkdir()

    def fake_run(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "docker" in cmd_str and "run" in cmd_str and "-d" in cmd_str:
            return MagicMock(returncode=0, stdout="cid-abc\n", stderr="")
        if "docker" in cmd_str and "cp" in cmd_str:
            return MagicMock(returncode=1, stdout=b"", stderr=b"cp error")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.provision.tempfile.mkdtemp", return_value=str(run_dir)), \
         patch("harness.provision.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="Docker cp failed"):
            provision("bat", "ws4", use_docker=True)
