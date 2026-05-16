"""Tests for harness/tools/ — registry, search, and terminal tools."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harness.tools import build_tool_list, dispatch
from harness.tools.search import grep_search, file_search, semantic_search
from harness.tools.terminal import _is_blocked, run_in_terminal


# ── Workspace mock helper ─────────────────────────────────────────────────────

def _make_ws(root: Path) -> MagicMock:
    ws = MagicMock()
    ws.path = root
    return ws


# ── build_tool_list ───────────────────────────────────────────────────────────

def test_build_tool_list_empty():
    tools = build_tool_list([])
    assert tools == []


def test_build_tool_list_terminal():
    tools = build_tool_list(["terminal"])
    names = [t["function"]["name"] for t in tools]
    assert "run_in_terminal" in names


def test_build_tool_list_filesystem():
    tools = build_tool_list(["filesystem"])
    names = {t["function"]["name"] for t in tools}
    assert "read_file" in names
    assert "create_file" in names
    # search tools NOT included
    assert "grep_search" not in names


def test_build_tool_list_search():
    tools = build_tool_list(["search"])
    names = {t["function"]["name"] for t in tools}
    assert "grep_search" in names
    assert "file_search" in names
    assert "semantic_search" in names


def test_build_tool_list_codebase():
    tools = build_tool_list(["codebase"])
    names = {t["function"]["name"] for t in tools}
    # Codebase = filesystem + search (minus semantic_search in the map)
    assert "read_file" in names
    assert "grep_search" in names


def test_build_tool_list_agent_surface():
    """'agent' surface maps to empty set — Control agent gets no tools."""
    tools = build_tool_list(["agent"])
    assert tools == []


def test_build_tool_list_unknown_surface():
    """Unknown surface silently contributes no tools."""
    tools = build_tool_list(["unknown-surface"])
    assert tools == []


def test_build_tool_list_combined():
    tools = build_tool_list(["filesystem", "terminal"])
    names = {t["function"]["name"] for t in tools}
    assert "read_file" in names
    assert "run_in_terminal" in names


def test_build_tool_list_schema_format():
    tools = build_tool_list(["terminal"])
    assert tools[0]["type"] == "function"
    assert "name" in tools[0]["function"]
    assert "parameters" in tools[0]["function"]


# ── dispatch ──────────────────────────────────────────────────────────────────

def test_dispatch_unknown_tool(tmp_path):
    ws = _make_ws(tmp_path)
    result = dispatch("totally_unknown_tool", ws, {})
    assert result == "[error] unknown tool: totally_unknown_tool"


def test_dispatch_read_file(tmp_path):
    (tmp_path / "test.py").write_text("hello world")
    ws = _make_ws(tmp_path)
    result = dispatch("read_file", ws, {"filePath": "test.py", "startLine": 1, "endLine": 1})
    assert "hello world" in result


def test_dispatch_handler_exception(tmp_path):
    ws = _make_ws(tmp_path)
    # read_file with missing file → should return error string (handled internally)
    result = dispatch("read_file", ws, {"filePath": "nonexistent.py", "startLine": 1, "endLine": 1})
    assert result.startswith("[error]")


def test_dispatch_exception_path_traversal(tmp_path):
    """Path traversal triggers PermissionError in handler → caught by dispatch except block."""
    ws = _make_ws(tmp_path)
    # '../../etc/passwd' escapes workspace root → _safe_path raises PermissionError
    result = dispatch("read_file", ws, {"filePath": "../../etc/passwd"})
    assert result.startswith("[error] read_file failed:")


# ── grep_search ───────────────────────────────────────────────────────────────

def test_grep_search_match_found(tmp_path):
    (tmp_path / "code.py").write_text("def hello():\n    pass\n")
    ws = _make_ws(tmp_path)
    result = grep_search(workspace=ws, query="hello", isRegexp=False)
    assert "hello" in result


def test_grep_search_no_match(tmp_path):
    (tmp_path / "code.py").write_text("x = 1\n")
    ws = _make_ws(tmp_path)
    result = grep_search(workspace=ws, query="nonexistent_xyz", isRegexp=False)
    assert result == "(no matches)"


def test_grep_search_no_match_rc1(tmp_path):
    """rg returns rc=1 when no matches — should return '(no matches)'."""
    ws = _make_ws(tmp_path)
    mock_result = MagicMock(returncode=1, stdout="", stderr="")
    with patch("harness.tools.search.subprocess.run", return_value=mock_result):
        result = grep_search(workspace=ws, query="abc", isRegexp=False)
    assert result == "(no matches)"


def test_grep_search_error_rc2(tmp_path):
    """rg returns rc>1 on real error — should return [error] ..."""
    ws = _make_ws(tmp_path)
    mock_result = MagicMock(returncode=2, stdout="", stderr="invalid pattern")
    with patch("harness.tools.search.subprocess.run", return_value=mock_result):
        result = grep_search(workspace=ws, query="[bad", isRegexp=True)
    assert result.startswith("[error] grep failed:")


def test_grep_search_with_glob(tmp_path):
    (tmp_path / "main.py").write_text("def run(): pass")
    (tmp_path / "main.txt").write_text("def run(): pass")
    ws = _make_ws(tmp_path)
    result = grep_search(workspace=ws, query="run", isRegexp=False, includePattern="*.py")
    assert "main.py" in result


def test_grep_search_truncation(tmp_path):
    """Results at maxResults limit should show truncated notice."""
    (tmp_path / "big.py").write_text("\n".join(f"line{i}" for i in range(100)))
    ws = _make_ws(tmp_path)
    # Mock rg with 5 result lines
    fake_output = "\n".join(f"big.py:1:match{i}" for i in range(5))
    mock_result = MagicMock(returncode=0, stdout=fake_output + "\n")
    with patch("harness.tools.search.subprocess.run", return_value=mock_result):
        result = grep_search(workspace=ws, query="match", isRegexp=False, maxResults=5)
    assert "truncated" in result


def test_grep_search_regexp(tmp_path):
    (tmp_path / "code.py").write_text("calculate_total(100)")
    ws = _make_ws(tmp_path)
    result = grep_search(workspace=ws, query=r"calculate_\w+", isRegexp=True)
    assert "calculate_total" in result


# ── file_search ───────────────────────────────────────────────────────────────

def test_file_search_finds_files(tmp_path):
    (tmp_path / "billing.py").write_text("# billing")
    (tmp_path / "invoice.py").write_text("# invoice")
    ws = _make_ws(tmp_path)
    result = file_search(workspace=ws, query="**/*.py")
    assert "billing.py" in result
    assert "invoice.py" in result


def test_file_search_no_matches(tmp_path):
    ws = _make_ws(tmp_path)
    result = file_search(workspace=ws, query="**/*.nonexistent")
    assert result == "(no matches)"


def test_file_search_truncation(tmp_path):
    for i in range(10):
        (tmp_path / f"file{i}.py").write_text("x")
    ws = _make_ws(tmp_path)
    result = file_search(workspace=ws, query="**/*.py", maxResults=3)
    assert "truncated" in result


# ── semantic_search ───────────────────────────────────────────────────────────

def test_semantic_search_delegates_to_grep(tmp_path):
    (tmp_path / "auth.py").write_text("def authenticate(): ...")
    ws = _make_ws(tmp_path)
    result = semantic_search(workspace=ws, query="authenticate")
    assert "authenticate" in result


def test_semantic_search_returns_no_matches(tmp_path):
    ws = _make_ws(tmp_path)
    result = semantic_search(workspace=ws, query="xyzzy_not_present_anywhere")
    assert result == "(no matches)"


# ── _is_blocked ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -rf /home",
    "dd if=/dev/zero",
    "mkfs.ext4 /dev/sda",
    "wget http://example.com/file",
    "curl https://example.com",
    "nc localhost 4444",
    "ncat -l 1234",
    "ssh user@host",
    "scp file.txt user@host:/",
    "git push origin main",
    "git remote add upstream ...",
    "pip install requests",
    "npm install",
    "apt-get install vim",
    "sudo rm -rf",
])
def test_is_blocked_for_dangerous_commands(cmd):
    assert _is_blocked(cmd) is True


@pytest.mark.parametrize("cmd", [
    "git status",
    "git log --oneline",
    "git diff HEAD",
    "python -m pytest tests/",
    "ls -la",
    "echo hello",
    "cat billing.py",
    "grep -r 'TODO' .",
    "rg pattern",
])
def test_is_not_blocked_for_safe_commands(cmd):
    assert _is_blocked(cmd) is False


def test_is_blocked_case_insensitive():
    assert _is_blocked("RM -RF /") is True
    assert _is_blocked("SUDO apt-get update") is True


def test_is_blocked_strips_leading_whitespace():
    assert _is_blocked("  rm -rf /") is True


# ── run_in_terminal ───────────────────────────────────────────────────────────

def test_run_in_terminal_blocked():
    ws = MagicMock()
    result = run_in_terminal(workspace=ws, command="rm -rf /", explanation="delete")
    assert result.startswith("[blocked]")
    ws.exec.assert_not_called()


def test_run_in_terminal_success(tmp_path):
    ws = MagicMock()
    ws.exec.return_value = (0, "hello world\n", "")
    result = run_in_terminal(workspace=ws, command="echo hello world", explanation="greet")
    assert "hello world" in result


def test_run_in_terminal_stderr_shown(tmp_path):
    ws = MagicMock()
    ws.exec.return_value = (1, "", "error output\n")
    result = run_in_terminal(workspace=ws, command="badcmd", explanation="test")
    assert "[stderr]" in result
    assert "error output" in result


def test_run_in_terminal_empty_output():
    ws = MagicMock()
    ws.exec.return_value = (0, "", "")
    result = run_in_terminal(workspace=ws, command="true", explanation="no output")
    assert "(exit 0)" in result


def test_run_in_terminal_both_stdout_and_stderr():
    ws = MagicMock()
    ws.exec.return_value = (1, "partial output\n", "some warning\n")
    result = run_in_terminal(workspace=ws, command="mixed", explanation="mixed output")
    assert "partial output" in result
    assert "[stderr]" in result
    assert "some warning" in result


def test_run_in_terminal_no_explanation():
    ws = MagicMock()
    ws.exec.return_value = (0, "ok\n", "")
    # explanation is optional
    result = run_in_terminal(workspace=ws, command="echo ok")
    assert "ok" in result
