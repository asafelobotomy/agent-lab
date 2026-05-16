"""harness/tools/filesystem.py — file system tools backed by the workspace.

All paths are relative to workspace root. Absolute paths outside the workspace
are rejected to prevent the agent from touching the host system.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from harness.tools import register
from harness.provision import Workspace


def _safe_path(workspace: Workspace, relpath: str) -> Path:
    """Resolve relpath within the workspace, rejecting path traversal."""
    root = workspace.path if isinstance(workspace.path, Path) else Path(workspace.path)
    # Normalise the relative path without allowing traversal outside root
    try:
        resolved = (root / relpath).resolve()
        resolved.relative_to(root.resolve())  # raises ValueError if outside
        return resolved
    except ValueError:
        raise PermissionError(f"Path '{relpath}' escapes workspace root") from None


@register("read_file", {
    "name": "read_file",
    "description": "Read the contents of a file in the workspace.",
    "parameters": {
        "type": "object",
        "properties": {
            "filePath": {"type": "string", "description": "Workspace-relative file path."},
            "startLine": {"type": "integer", "description": "1-based start line (optional)."},
            "endLine": {"type": "integer", "description": "1-based end line inclusive (optional)."},
        },
        "required": ["filePath"],
    },
})
def read_file(*, workspace: Workspace, filePath: str,
              startLine: int | None = None, endLine: int | None = None) -> str:
    path = _safe_path(workspace, filePath)
    if not path.exists():
        return f"[error] file not found: {filePath}"
    text = path.read_text(encoding="utf-8", errors="replace")
    if startLine is not None or endLine is not None:
        lines = text.splitlines()
        s = (startLine or 1) - 1
        e = endLine or len(lines)
        text = "\n".join(lines[s:e])
    return text


@register("list_dir", {
    "name": "list_dir",
    "description": "List the contents of a directory in the workspace.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative directory path."},
        },
        "required": ["path"],
    },
})
def list_dir(*, workspace: Workspace, path: str) -> str:
    dir_path = _safe_path(workspace, path)
    if not dir_path.is_dir():
        return f"[error] not a directory: {path}"
    entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name))
    lines = [f"{e.name}{'/' if e.is_dir() else ''}" for e in entries]
    return "\n".join(lines) if lines else "(empty)"


@register("replace_string_in_file", {
    "name": "replace_string_in_file",
    "description": "Replace an exact string occurrence in a file.",
    "parameters": {
        "type": "object",
        "properties": {
            "filePath": {"type": "string"},
            "oldString": {"type": "string"},
            "newString": {"type": "string"},
        },
        "required": ["filePath", "oldString", "newString"],
    },
})
def replace_string_in_file(*, workspace: Workspace, filePath: str,
                            oldString: str, newString: str) -> str:
    path = _safe_path(workspace, filePath)
    if not path.exists():
        return f"[error] file not found: {filePath}"
    text = path.read_text(encoding="utf-8")
    count = text.count(oldString)
    if count == 0:
        return f"[error] oldString not found in {filePath}"
    if count > 1:
        return f"[error] oldString matches {count} locations in {filePath} — be more specific"
    new_text = text.replace(oldString, newString, 1)
    path.write_text(new_text, encoding="utf-8")
    # Sync to Docker container via workspace.write
    workspace.write(filePath, new_text)
    return f"Replaced 1 occurrence in {filePath}"


@register("create_file", {
    "name": "create_file",
    "description": "Create a new file in the workspace with the given content.",
    "parameters": {
        "type": "object",
        "properties": {
            "filePath": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["filePath", "content"],
    },
})
def create_file(*, workspace: Workspace, filePath: str, content: str) -> str:
    path = _safe_path(workspace, filePath)
    if path.exists():
        return f"[error] file already exists: {filePath} — use replace_string_in_file to edit"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    workspace.write(filePath, content)
    return f"Created {filePath}"


@register("multi_replace_string_in_file", {
    "name": "multi_replace_string_in_file",
    "description": "Apply multiple replace_string_in_file operations in a single call.",
    "parameters": {
        "type": "object",
        "properties": {
            "replacements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "filePath": {"type": "string"},
                        "oldString": {"type": "string"},
                        "newString": {"type": "string"},
                    },
                    "required": ["filePath", "oldString", "newString"],
                },
            },
        },
        "required": ["replacements"],
    },
})
def multi_replace_string_in_file(*, workspace: Workspace,
                                  replacements: list[dict]) -> str:
    results = []
    for op in replacements:
        results.append(replace_string_in_file(
            workspace=workspace,
            filePath=op["filePath"],
            oldString=op["oldString"],
            newString=op["newString"],
        ))
    return "\n".join(results)
