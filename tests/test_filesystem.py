"""tests/test_filesystem.py — unit tests for harness/tools/filesystem.py."""
from __future__ import annotations

import pytest
from pathlib import Path

from harness.tools.filesystem import (
    create_file,
    list_dir,
    multi_replace_string_in_file,
    read_file,
    replace_string_in_file,
)


class _StubWorkspace:
    """Minimal workspace stub: real tmpdir path, no-op Docker sync."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, relpath: str, text: str) -> None:
        pass  # Docker sync not needed in unit tests


@pytest.fixture
def ws(tmp_path: Path) -> _StubWorkspace:
    return _StubWorkspace(tmp_path)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def test_read_file_full(ws, tmp_path):
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3", encoding="utf-8")
    assert read_file(workspace=ws, filePath="hello.txt") == "line1\nline2\nline3"


def test_read_file_line_range(ws, tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\nc\nd", encoding="utf-8")
    assert read_file(workspace=ws, filePath="f.txt", startLine=2, endLine=3) == "b\nc"


def test_read_file_start_only(ws, tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\nc", encoding="utf-8")
    assert read_file(workspace=ws, filePath="f.txt", startLine=2) == "b\nc"


def test_read_file_missing(ws):
    result = read_file(workspace=ws, filePath="nope.txt")
    assert result.startswith("[error]")


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

def test_list_dir_entries(ws, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.py").write_text("")
    result = list_dir(workspace=ws, path=".")
    assert "sub/" in result
    assert "file.py" in result


def test_list_dir_empty(ws, tmp_path):
    (tmp_path / "empty").mkdir()
    assert list_dir(workspace=ws, path="empty") == "(empty)"


def test_list_dir_missing(ws):
    result = list_dir(workspace=ws, path="nodir")
    assert result.startswith("[error]")


# ---------------------------------------------------------------------------
# replace_string_in_file
# ---------------------------------------------------------------------------

def test_replace_success(ws, tmp_path):
    (tmp_path / "src.py").write_text("def foo(): pass\n", encoding="utf-8")
    msg = replace_string_in_file(workspace=ws, filePath="src.py",
                                  oldString="foo", newString="bar")
    assert "1 occurrence" in msg
    assert (tmp_path / "src.py").read_text(encoding="utf-8") == "def bar(): pass\n"


def test_replace_not_found(ws, tmp_path):
    (tmp_path / "src.py").write_text("def foo(): pass\n", encoding="utf-8")
    result = replace_string_in_file(workspace=ws, filePath="src.py",
                                     oldString="xyz", newString="abc")
    assert result.startswith("[error]")
    assert (tmp_path / "src.py").read_text(encoding="utf-8") == "def foo(): pass\n"


def test_replace_ambiguous(ws, tmp_path):
    (tmp_path / "src.py").write_text("foo foo\n", encoding="utf-8")
    result = replace_string_in_file(workspace=ws, filePath="src.py",
                                     oldString="foo", newString="bar")
    assert result.startswith("[error]")
    assert "2" in result


def test_replace_missing_file(ws):
    result = replace_string_in_file(workspace=ws, filePath="missing.py",
                                     oldString="x", newString="y")
    assert result.startswith("[error]")


# ---------------------------------------------------------------------------
# create_file
# ---------------------------------------------------------------------------

def test_create_file_success(ws, tmp_path):
    msg = create_file(workspace=ws, filePath="new.txt", content="hello")
    assert "Created" in msg
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello"


def test_create_file_already_exists(ws, tmp_path):
    (tmp_path / "existing.txt").write_text("original", encoding="utf-8")
    result = create_file(workspace=ws, filePath="existing.txt", content="new")
    assert result.startswith("[error]")
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "original"


def test_create_file_creates_parent_dirs(ws, tmp_path):
    create_file(workspace=ws, filePath="a/b/c.txt", content="deep")
    assert (tmp_path / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "deep"


# ---------------------------------------------------------------------------
# multi_replace_string_in_file
# ---------------------------------------------------------------------------

def test_multi_replace_success(ws, tmp_path):
    (tmp_path / "x.py").write_text("alpha beta\n", encoding="utf-8")
    (tmp_path / "y.py").write_text("gamma delta\n", encoding="utf-8")
    multi_replace_string_in_file(workspace=ws, replacements=[
        {"filePath": "x.py", "oldString": "alpha", "newString": "ALPHA"},
        {"filePath": "y.py", "oldString": "gamma", "newString": "GAMMA"},
    ])
    assert "ALPHA" in (tmp_path / "x.py").read_text(encoding="utf-8")
    assert "GAMMA" in (tmp_path / "y.py").read_text(encoding="utf-8")


def test_multi_replace_partial_error_does_not_abort(ws, tmp_path):
    (tmp_path / "a.py").write_text("hello world\n", encoding="utf-8")
    result = multi_replace_string_in_file(workspace=ws, replacements=[
        {"filePath": "a.py",       "oldString": "hello", "newString": "hi"},
        {"filePath": "missing.py", "oldString": "x",     "newString": "y"},
    ])
    assert "hi" in (tmp_path / "a.py").read_text(encoding="utf-8")
    assert "[error]" in result


# ---------------------------------------------------------------------------
# path traversal
# ---------------------------------------------------------------------------

def test_path_traversal_rejected(ws):
    with pytest.raises(PermissionError):
        read_file(workspace=ws, filePath="../../etc/passwd")


def test_path_traversal_create_rejected(ws):
    with pytest.raises(PermissionError):
        create_file(workspace=ws, filePath="../outside.txt", content="bad")
