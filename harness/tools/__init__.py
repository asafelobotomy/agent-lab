"""harness/tools/__init__.py — tool registry.

Maps VS Code-style tool names to handler functions and produces the
tool-call schema list that gets sent to the LLM.

Tool surface is determined per-agent from the agent's frontmatter `tools:` field.
Supported surface names: filesystem, search, terminal, codebase, agent.
"""
from __future__ import annotations

from typing import Callable
from harness.provision import Workspace

# Each tool handler receives (workspace, **arguments) and returns a str result.
ToolHandler = Callable[..., str]

_REGISTRY: dict[str, tuple[ToolHandler, dict]] = {}  # name → (handler, schema)


def register(name: str, schema: dict) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator to register a tool handler with its JSON schema."""
    def decorator(fn: ToolHandler) -> ToolHandler:
        _REGISTRY[name] = (fn, schema)
        return fn
    return decorator


# Import handlers so they self-register via @register
from harness.tools import filesystem, search, terminal  # noqa: E402, F401


def build_tool_list(agent_tools: list[str]) -> list[dict]:
    """Return the OpenAI-compatible tool list for the given tool surface names.

    agent_tools: values from the agent's frontmatter `tools:` list.
    Surface aliases: 'codebase' → filesystem + search; 'agent' → no extra tools.
    """
    surface_map: dict[str, set[str]] = {
        "filesystem": {
            "read_file", "list_dir", "replace_string_in_file",
            "create_file", "multi_replace_string_in_file",
        },
        "search": {"grep_search", "file_search", "semantic_search"},
        "terminal": {"run_in_terminal"},
        "codebase": {
            "read_file", "list_dir", "replace_string_in_file",
            "create_file", "multi_replace_string_in_file",
            "grep_search", "file_search",
        },
        "agent": set(),  # agent-to-agent delegation — not simulated in harness
    }

    wanted: set[str] = set()
    for surface in agent_tools:
        wanted |= surface_map.get(surface.lower(), set())

    tools = []
    for name, (_, schema) in _REGISTRY.items():
        if name in wanted:
            tools.append({
                "type": "function",
                "function": schema,
            })
    return tools


def dispatch(name: str, workspace: Workspace, arguments: dict) -> str:
    """Call the registered handler for `name` with workspace + arguments."""
    entry = _REGISTRY.get(name)
    if entry is None:
        return f"[error] unknown tool: {name}"
    handler, _ = entry
    try:
        return handler(workspace=workspace, **arguments)
    except Exception as exc:  # noqa: BLE001
        return f"[error] {name} failed: {exc}"
