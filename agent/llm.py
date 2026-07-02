"""LLM client helpers (ADR-010: OpenAI-style tool-calling).

Production: ``OpenAILLMClient`` — thin wrapper around ``openai.AsyncOpenAI``
            pointed at the vLLM endpoint, passing ``tools=[...]`` each turn.
Tests:      ``StubLLMClient``     — deterministic rule-based policy that plays a
            competent agent (monitor → logs → notify → kb → propose) without
            any HTTP call.
            ``ScriptedLLMClient`` — replays a fixed sequence of assistant
            messages (adversarial / edge-case tests).
"""

from __future__ import annotations

import json
import re
from typing import Protocol

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str | None) -> str:
    """Drop chain-of-thought blocks Qwen may inline in content."""
    if not text:
        return ""
    return _THINK_RE.sub("", text).strip()


def make_tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    """Build an assistant message containing a single tool call (test helper)."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


# ── Protocol ─────────────────────────────────────────────────────────────────

class LLMClient(Protocol):
    """Minimal interface the agent loop needs from the LLM client."""

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """Run one reasoning turn.

        Args:
            messages: OpenAI-format conversation (system/user/assistant/tool).
            tools:    OpenAI-format function-calling tool schemas.

        Returns:
            The assistant message dict: ``{"role": "assistant", "content": str}``
            plus a ``"tool_calls"`` key when the model requested calls.
        """
        ...


# ── Production client ─────────────────────────────────────────────────────────

class OpenAILLMClient:
    """Wraps ``openai.AsyncOpenAI`` with the vLLM endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> None:
        try:
            import openai

            self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
        except ImportError as exc:
            raise ImportError("openai package required for OpenAILLMClient") from exc
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        msg = resp.choices[0].message
        out: dict = {"role": "assistant", "content": _strip_think(msg.content)}
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in msg.tool_calls
            ]
        return out


# ── Stub clients (tests) ───────────────────────────────────────────────────────

def _tool_results_by_name(messages: list[dict]) -> dict:
    """Map function name → parsed content of its latest tool result."""
    id_to_name: dict[str, str] = {}
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            id_to_name[tc.get("id", "")] = tc["function"]["name"]
    results: dict = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        name = id_to_name.get(msg.get("tool_call_id", ""))
        if name is None:
            continue
        try:
            results[name] = json.loads(msg.get("content") or "null")
        except json.JSONDecodeError:
            results[name] = None
    return results


class StubLLMClient:
    """Deterministic policy stub that drives the tool-calling loop in tests.

    Plays a competent agent — monitor → logs → notify → kb.search → propose —
    threading each tool result into the next call's arguments.

    ``default_response`` is the error signature the stub "extracts" from the
    logs (default "Xid 79"); adversarial tests pass injection payloads here to
    emulate a hijacked extraction.
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_response: str = "Xid 79",
    ) -> None:
        self._signature = default_response
        self._responses = responses or {}
        self.calls: list[dict] = []  # {"messages": [...], "tools": [...]} per turn
        self._counter = 0

    def _call(self, name: str, arguments: dict) -> dict:
        self._counter += 1
        return make_tool_call(name, arguments, call_id=f"stub-{self._counter}")

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        seen = _tool_results_by_name(messages)

        if "monitor_list_events" not in seen:
            return self._call("monitor_list_events", {})

        events = seen["monitor_list_events"] or []
        if not events:
            return {"role": "assistant", "content": "No active faults observed."}

        if "logs_get_bundle" not in seen:
            return self._call("logs_get_bundle", {"asset_id": events[0]["asset_id"]})

        bundle = seen["logs_get_bundle"] or {}
        fault_id = bundle.get("fault_event_id", "")

        if "notify_post_activity" not in seen:
            return self._call(
                "notify_post_activity",
                {
                    "fault_event_id": fault_id,
                    "step": "diagnose",
                    "message": f"Identified error signature: {self._signature}",
                },
            )

        if "kb_search" not in seen:
            return self._call("kb_search", {"signature": self._signature})

        kb = seen["kb_search"] or {}
        if "remediation_propose" not in seen:
            step_ids = kb.get("remediation_step_ids", []) if isinstance(kb, dict) else []
            return self._call(
                "remediation_propose",
                {
                    "fault_event_id": fault_id,
                    "step_ids": step_ids,
                    "summary": (
                        f"Fault on {events[0]['asset_id']}: {self._signature}. "
                        "Remediation recommended per matched KB procedure."
                    ),
                },
            )

        return {
            "role": "assistant",
            "content": "Investigation complete — awaiting operator decision.",
        }


class ScriptedLLMClient:
    """Replays a fixed sequence of assistant messages, then plain completions.

    For adversarial and edge-case tests — e.g. a hijacked model attempting to
    call tools it was never given, or one that never proposes remediation.
    """

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        if self._script:
            return self._script.pop(0)
        return {"role": "assistant", "content": "Done."}
