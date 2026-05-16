"""Gap tests for harness/fetch.py — covers remaining uncovered paths."""
from __future__ import annotations

import shutil
from unittest.mock import patch, MagicMock


from harness.fetch import (
    _git_clone,
    _copy_agents,
    _copy_context,
    _extract_frontmatter_name,
    fetch_repo,
)


# ── _git_clone — with and without token ──────────────────────────────────────

def test_git_clone_with_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "mytoken123")
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("harness.fetch.subprocess.run", return_value=mock_result) as mock_run:
        _git_clone("org/repo", tmp_path / "clone")
    args = mock_run.call_args[0][0]
    url_arg = next(a for a in args if "github.com" in a)
    assert "mytoken123" in url_arg


def test_git_clone_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("harness.fetch.subprocess.run", return_value=mock_result) as mock_run:
        _git_clone("org/repo", tmp_path / "clone")
    args = mock_run.call_args[0][0]
    url_arg = next(a for a in args if "github.com" in a)
    # Should NOT contain a token in the URL
    assert "@" not in url_arg


# ── _copy_agents — multiple agents ───────────────────────────────────────────

def test_copy_agents_multiple_agents(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in ("triage", "review"):
        agent_file = src / f"{name}.agent.md"
        agent_file.write_text(f"---\nname: {name.capitalize()}\n---\nBody.\n")
    dest = tmp_path / "dest"
    dest.mkdir()
    agents = _copy_agents(src, dest)
    assert len(agents) == 2
    names = {a["name"] for a in agents}
    assert "Triage" in names
    assert "Review" in names


def test_copy_agents_no_agents(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    agents = _copy_agents(src, dest)
    assert agents == []


def test_copy_agents_falls_back_to_stem_name(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    # No frontmatter → falls back to stem
    agent_file = src / "my-agent.agent.md"
    agent_file.write_text("Just a description, no frontmatter.\n")
    dest = tmp_path / "dest"
    dest.mkdir()
    agents = _copy_agents(src, dest)
    assert len(agents) == 1
    assert agents[0]["name"] == "my-agent"


# ── _copy_context — README and docs ──────────────────────────────────────────

def test_copy_context_with_readme(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("# My Repo")
    dest = tmp_path / "dest"
    dest.mkdir()
    captured = _copy_context(src, dest)
    assert "context/README.md" in captured
    assert (dest / "context" / "README.md").exists()


def test_copy_context_with_docs_dir(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    docs = src / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide")
    dest = tmp_path / "dest"
    dest.mkdir()
    captured = _copy_context(src, dest)
    assert "context/docs/" in captured
    assert (dest / "context" / "docs" / "guide.md").exists()


def test_copy_context_no_readme_no_docs(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    captured = _copy_context(src, dest)
    assert captured == []


def test_copy_context_rst_readme(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.rst").write_text("RST content")
    dest = tmp_path / "dest"
    dest.mkdir()
    captured = _copy_context(src, dest)
    assert "context/README.rst" in captured


# ── _extract_frontmatter_name ─────────────────────────────────────────────────

def test_extract_frontmatter_name_found(tmp_path):
    f = tmp_path / "agent.agent.md"
    f.write_text('---\nname: "MyAgent"\n---\nBody.\n')
    assert _extract_frontmatter_name(f) == "MyAgent"


def test_extract_frontmatter_name_not_found(tmp_path):
    f = tmp_path / "agent.agent.md"
    f.write_text("No frontmatter here.\n")
    assert _extract_frontmatter_name(f) is None


def test_extract_frontmatter_name_unclosed(tmp_path):
    f = tmp_path / "agent.agent.md"
    f.write_text("---\nname: X\nno closing delimiter")
    assert _extract_frontmatter_name(f) is None


# ── fetch_repo — various paths ────────────────────────────────────────────────

def test_fetch_repo_already_exists(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "org" / "repo"
    dest.mkdir(parents=True)
    monkeypatch.setattr("harness.fetch.REGISTRY_DIR", tmp_path)
    rc = fetch_repo("org/repo", force=False)
    assert rc == 1
    out = capsys.readouterr().out
    assert "already exists" in out


def test_fetch_repo_clone_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("harness.fetch.REGISTRY_DIR", tmp_path)
    clone_result = MagicMock(returncode=1, stdout="", stderr="Repository not found")
    with patch("harness.fetch.subprocess.run", return_value=clone_result), \
         patch("harness.fetch.tempfile.TemporaryDirectory") as mock_tmpdir:
        # Set up TemporaryDirectory context manager
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=str(tmp_path / "tmp"))
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_tmpdir.return_value = mock_ctx
        (tmp_path / "tmp").mkdir(exist_ok=True)
        rc = fetch_repo("org/badrepo", force=False)
    assert rc == 2
    assert "failed" in capsys.readouterr().out.lower()


def test_fetch_repo_with_force_overwrites(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "org" / "myrepo"
    dest.mkdir(parents=True)
    old_file = dest / "old.txt"
    old_file.write_text("old")

    monkeypatch.setattr("harness.fetch.REGISTRY_DIR", tmp_path)

    # Set up a real temp clone dir
    clone_src = tmp_path / "fakeclone"
    clone_src.mkdir()
    agent_file = clone_src / "test.agent.md"
    agent_file.write_text("---\nname: Test\n---\nBody\n")

    clone_result = MagicMock(returncode=0, stdout="abc123\n", stderr="")
    sha_result = MagicMock(returncode=0, stdout="deadbeef123\n", stderr="")

    call_count = [0]
    def _fake_run(cmd, *args, **kwargs):
        call_count[0] += 1
        cmd_str = " ".join(str(c) for c in cmd)
        if "clone" in cmd_str:
            # Actually copy the fake clone dir to the tempdir destination
            # Copy our fake clone content to where the code expects it
            return clone_result
        if "rev-parse" in cmd_str:
            return sha_result
        return MagicMock(returncode=0, stdout="", stderr="")


    class _FakeTmpDir:
        def __enter__(self):
            self.path = tmp_path / "tmp-fetch"
            self.path.mkdir(exist_ok=True)
            clone_dest = self.path / "clone"
            shutil.copytree(clone_src, clone_dest)
            return str(self.path)
        def __exit__(self, *_):
            pass

    with patch("harness.fetch.subprocess.run", side_effect=_fake_run), \
         patch("harness.fetch.tempfile.TemporaryDirectory", return_value=_FakeTmpDir()):
        rc = fetch_repo("org/myrepo", force=True)

    # Old file should be gone (dest was overwritten)
    assert rc == 0
    assert not old_file.exists()


def test_fetch_repo_no_agents_warning(tmp_path, monkeypatch, capsys):
    """When no .agent.md files found, a warning is printed."""
    monkeypatch.setattr("harness.fetch.REGISTRY_DIR", tmp_path)

    # Fake clone with no agents
    clone_src = tmp_path / "fakeclone-noagents"
    clone_src.mkdir()
    (clone_src / "README.md").write_text("# Repo with no agents")

    class _FakeTmpDir:
        def __enter__(self):
            self.path = tmp_path / "tmp-no-agents"
            self.path.mkdir(exist_ok=True)
            shutil.copytree(clone_src, self.path / "clone")
            return str(self.path)
        def __exit__(self, *_):
            pass

    clone_result = MagicMock(returncode=0, stdout="", stderr="")
    sha_result = MagicMock(returncode=0, stdout="abc123\n", stderr="")

    def _fake_run(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "clone" in cmd_str:
            return clone_result
        if "rev-parse" in cmd_str:
            return sha_result
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.fetch.subprocess.run", side_effect=_fake_run), \
         patch("harness.fetch.tempfile.TemporaryDirectory", return_value=_FakeTmpDir()):
        fetch_repo("org/noagents", force=False)

    out = capsys.readouterr().out
    assert "warning" in out.lower()


def test_fetch_repo_with_context_files(tmp_path, monkeypatch, capsys):
    """fetch_repo should print 'context captured' when README is found."""
    monkeypatch.setattr("harness.fetch.REGISTRY_DIR", tmp_path)

    clone_src = tmp_path / "fakeclone-ctx"
    clone_src.mkdir()
    (clone_src / "agent.agent.md").write_text("---\nname: Ctx\n---\nBody\n")
    (clone_src / "README.md").write_text("# Context")

    class _FakeTmpDir:
        def __enter__(self):
            self.path = tmp_path / "tmp-ctx"
            self.path.mkdir(exist_ok=True)
            shutil.copytree(clone_src, self.path / "clone")
            return str(self.path)
        def __exit__(self, *_):
            pass

    def _fake_run(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "clone" in cmd_str:
            return MagicMock(returncode=0, stdout="", stderr="")
        if "rev-parse" in cmd_str:
            return MagicMock(returncode=0, stdout="abc\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.fetch.subprocess.run", side_effect=_fake_run), \
         patch("harness.fetch.tempfile.TemporaryDirectory", return_value=_FakeTmpDir()):
        fetch_repo("org/ctxrepo", force=False)

    out = capsys.readouterr().out
    assert "context captured" in out


# ── _extract_frontmatter_name — OSError path (lines 107-109) ─────────────────

def test_extract_frontmatter_name_os_error(tmp_path):
    """OSError when reading file → _extract_frontmatter_name returns None (lines 107-109)."""
    f = tmp_path / "agent.agent.md"
    f.write_text("---\nname: Test\n---\nBody\n")
    with patch("harness.fetch.Path.read_text", side_effect=OSError("permission denied")):
        result = _extract_frontmatter_name(f)
    assert result is None


# ── fetch_repo with hook dir (lines 64-65, 142, 160) ─────────────────────────

def test_fetch_repo_with_hook_dir(tmp_path, monkeypatch, capsys):
    """When clone contains a hook/skill/tool dir, _copy_dir_if_exists covers lines 64-65."""
    monkeypatch.setattr("harness.fetch.REGISTRY_DIR", tmp_path)

    from harness.fetch import _HOOK_DIRS, _SKILL_DIRS, _TOOL_DIRS
    # Pick one directory name from the extra dirs
    extra_dir_name = (_HOOK_DIRS or _SKILL_DIRS or _TOOL_DIRS)[0] if (
        _HOOK_DIRS or _SKILL_DIRS or _TOOL_DIRS
    ) else None

    clone_src = tmp_path / "fakeclone-extras"
    clone_src.mkdir()
    (clone_src / "agent.agent.md").write_text("---\nname: Extras\n---\nBody\n")
    if extra_dir_name:
        extra = clone_src / extra_dir_name
        extra.mkdir()
        (extra / "tool.json").write_text("{}")

    class _FakeTmpDir:
        def __enter__(self):
            self.path = tmp_path / "tmp-extras"
            self.path.mkdir(exist_ok=True)
            shutil.copytree(clone_src, self.path / "clone")
            return str(self.path)
        def __exit__(self, *_):
            pass

    def _fake_run(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "clone" in cmd_str:
            return MagicMock(returncode=0, stdout="", stderr="")
        if "rev-parse" in cmd_str:
            return MagicMock(returncode=0, stdout="abc\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("harness.fetch.subprocess.run", side_effect=_fake_run), \
         patch("harness.fetch.tempfile.TemporaryDirectory", return_value=_FakeTmpDir()):
        rc = fetch_repo("org/extras-repo", force=False)

    assert rc == 0
    out = capsys.readouterr().out
    if extra_dir_name:
        assert "extras copied" in out
