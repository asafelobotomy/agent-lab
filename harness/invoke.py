"""harness/invoke.py — agentic LLM loop with real tool calls.

Drives a conversation with the LLM until it stops issuing tool calls.
All tool calls are dispatched to the workspace-backed tool handlers.
Every turn is logged via TaskLogger.

The loop:
  1. Send messages + tool list to LLM
  2. If response contains tool calls → execute each → append results → goto 1
  3. If response is text-only (stop_reason = "end_turn") → done

Max turns is capped to prevent runaway loops.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from harness.log import TaskLogger
from harness.provision import Workspace
import harness.tools as tools_registry

_ENDPOINT = "https://models.github.ai/inference/chat/completions"
_API_VERSION = "2026-03-10"
_MAX_TURNS = 20
_MIN_CALL_INTERVAL = 5.0   # seconds — stay under rate limits

_last_call: float = 0.0


def _throttle() -> None:
    global _last_call
    elapsed = time.monotonic() - _last_call
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)
    _last_call = time.monotonic()


def _llm_call(messages: list[dict], model: str,
              tool_list: list[dict]) -> dict[str, Any]:
    """Make a single call to the GitHub Models API. Returns raw response dict."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")

    _throttle()

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
    }
    if tool_list:
        payload["tools"] = tool_list
        payload["tool_choice"] = "auto"

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        _ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-ms-model-mesh-model-name": model,
        },
        method="POST",
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    data["_latency_ms"] = latency_ms
    return data


def _parse_tool_calls(choice: dict) -> list[dict]:
    """Extract tool_calls from a choice dict (OpenAI format)."""
    return choice.get("message", {}).get("tool_calls") or []


def run_task(
    *,
    agent_system_prompt: str,
    task: dict,
    workspace: Workspace,
    model: str,
    agent_tool_surfaces: list[str],
    logger: TaskLogger,
) -> dict:
    """
    Run a single task against the agent in the agentic loop.

    Returns a result dict with:
      final_response: str       — last text response from the agent
      tool_calls_made: list     — summary of all tool calls issued
      turn_count: int
      prompt_tokens: int
      completion_tokens: int
      total_latency_ms: float
    """
    tool_list = tools_registry.build_tool_list(agent_tool_surfaces)

    messages: list[dict] = [
        {"role": "system", "content": agent_system_prompt},
        {"role": "user",   "content": task["prompt"]},
    ]

    # Capture workspace state before the task
    pre_diff = workspace.diff()
    logger.workspace_snapshot("pre-task", pre_diff)

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_latency_ms = 0.0
    all_tool_calls: list[dict] = []
    final_response = ""

    for turn in range(1, _MAX_TURNS + 1):
        logger.turn_start(turn)
        logger.llm_request(messages, model, tool_list)

        try:
            data = _llm_call(messages, model, tool_list)
        except Exception as exc:
            logger.error("LLM call failed", exc)
            break

        latency_ms = data.get("_latency_ms", 0.0)
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_prompt_tokens += prompt_tokens
        total_completion_tokens += completion_tokens
        total_latency_ms += latency_ms

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        tool_calls = _parse_tool_calls(choice)
        stop_reason = choice.get("finish_reason", "stop")

        logger.llm_response(
            content=content or None,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

        # Append assistant message to history
        messages.append({"role": "assistant", "content": content,
                         "tool_calls": tool_calls} if tool_calls
                        else {"role": "assistant", "content": content})

        if not tool_calls:
            # Agent finished — no more tool calls
            final_response = content
            break

        # Execute every tool call and collect results
        tool_results: list[dict] = []
        for tc in tool_calls:
            call_id = tc.get("id", "")
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            logger.tool_call(call_id, name, args)
            result_text = tools_registry.dispatch(name, workspace, args)
            logger.tool_result(call_id, name, result_text,
                               error=result_text.startswith("[error]"))

            all_tool_calls.append({"name": name, "args": args, "result": result_text})
            tool_results.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result_text,
            })

        messages.extend(tool_results)

    # Capture workspace state after the task
    post_diff = workspace.diff()
    logger.workspace_snapshot("post-task", post_diff)

    return {
        "final_response": final_response,
        "tool_calls_made": all_tool_calls,
        "turn_count": turn,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_latency_ms": total_latency_ms,
        "workspace_diff": post_diff,
    }
