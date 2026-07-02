"""AgentTools — abstraction over the MCP tool calls.

Two implementations:
  MCPAgentTools    — production; calls the MCP Tools service over Streamable HTTP.
  DirectAgentTools — tests; calls business-logic functions in-process (no HTTP).

ADR-010: besides the named methods the harness uses directly, both
implementations expose the LLM-facing tool surface:
  list_llm_tool_schemas() — OpenAI-format function schemas for the allowlisted
                            tools (dotted MCP names become underscored)
  call_llm_tool()         — dispatch a model-issued call by function name

``remediation.execute`` is deliberately absent from LLM_EXPOSED_TOOLS: the LLM
never sees its schema and call_llm_tool() refuses to dispatch it. Only the
harness calls remediation_execute(), with a token retrieved out-of-band.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from services.mcp_tools.adapters.base import MonitoringAdapter
from services.mcp_tools.fault_registry import FaultEventRegistry
from services.mcp_tools.kb_index import KBIndex
from services.mcp_tools.token_store import ApprovalTokenStore
from services.mcp_tools.tools.kb import kb_search
from services.mcp_tools.tools.logs import logs_get_bundle
from services.mcp_tools.tools.monitor import (
    monitor_get_asset,
    monitor_list_assets,
    monitor_list_events,
)
from services.mcp_tools.tools.notify import notify_post_activity
from services.mcp_tools.tools.remediation import (
    RemediationError,
    remediation_execute,
    remediation_propose,
)
from services.orchestrator.simulator_client import SimulatorClient


# ── LLM tool surface (ADR-010) ────────────────────────────────────────────────

# Dotted MCP tool names the LLM may see and call. remediation.execute is
# deliberately absent — it is harness-only.
LLM_EXPOSED_TOOLS = frozenset(
    {
        "monitor.list_events",
        "monitor.get_asset",
        "monitor.list_assets",
        "logs.get_bundle",
        "kb.search",
        "notify.post_activity",
        "remediation.propose",
    }
)


def to_function_name(mcp_name: str) -> str:
    """MCP dotted name → OpenAI function name ([a-zA-Z0-9_-] only)."""
    return mcp_name.replace(".", "_")


_FN_TO_MCP = {to_function_name(name): name for name in LLM_EXPOSED_TOOLS}

# Hand-built schemas mirroring what FastMCP generates from the server's typed
# signatures. Used by DirectAgentTools (no MCP session to introspect in tests).
FALLBACK_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "monitor_list_events",
            "description": "List active fault events from the monitoring surface.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {
                        "type": "string",
                        "description": "Optional. Filter to a specific asset. Empty string means all assets.",
                        "default": "",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monitor_get_asset",
            "description": "Inspect health state for a single asset.",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monitor_list_assets",
            "description": "List all assets in the pack with their current health state.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "logs_get_bundle",
            "description": "Fetch the scenario log bundle for an asset. Returns log text plus the fault_event_id issued for this investigation.",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "Search the knowledge base for an error signature. Returns the best-matching article (kb_id, title, body_md, remediation_step_ids, score, via).",
            "parameters": {
                "type": "object",
                "properties": {
                    "signature": {"type": "string"},
                    "fallback_kb_id": {"type": "string", "default": ""},
                },
                "required": ["signature"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_post_activity",
            "description": "Post a plain-language progress update to the operator dashboard activity feed. Display-only narration; cannot change fault status or infrastructure state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fault_event_id": {"type": "string"},
                    "step": {
                        "type": "string",
                        "description": "Feed category: detect | diagnose | search_kb | present.",
                    },
                    "message": {"type": "string"},
                },
                "required": ["fault_event_id", "step", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remediation_propose",
            "description": "Record your recommended remediation steps and request operator approval. A proposal, not an action: changes no infrastructure state and returns no token. Call once, then stop calling tools and wait.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fault_event_id": {"type": "string"},
                    "step_ids": {"type": "array", "items": {"type": "string"}},
                    "summary": {
                        "type": "string",
                        "description": "2-3 sentence plain-language diagnosis summary for the operator: what is wrong, the likely cause, and the operational risk.",
                    },
                },
                "required": ["fault_event_id", "step_ids"],
            },
        },
    },
]


def _unknown_tool_result(name: str) -> str:
    return json.dumps(
        {
            "status": "error",
            "error": f"unknown_tool: {name}",
            "available_tools": sorted(_FN_TO_MCP),
        }
    )


# ── Protocol ──────────────────────────────────────────────────────────────────

class AgentTools(Protocol):
    """The tool surface the agent loop needs.  Each method mirrors an MCP tool."""

    async def monitor_list_events(self, asset_id: str | None = None) -> list[dict]: ...

    async def logs_get_bundle(self, asset_id: str) -> dict: ...

    async def kb_search(
        self,
        signature: str,
        fallback_kb_id: str | None = None,
    ) -> dict | None: ...

    async def remediation_propose(
        self,
        fault_event_id: str,
        step_ids: list[str],
    ) -> dict: ...

    async def remediation_execute(
        self,
        fault_event_id: str,
        approval_token: str,
        step_ids: list[str],
    ) -> dict: ...

    async def list_llm_tool_schemas(self) -> list[dict]: ...

    async def call_llm_tool(self, name: str, arguments: dict) -> str: ...


# ── MCP implementation (production) ──────────────────────────────────────────

class MCPAgentTools:
    """Calls the MCP Tools service via the MCP Streamable HTTP protocol.

    Usage:
        async with MCPAgentTools.connect(mcp_tools_url) as tools:
            events = await tools.monitor_list_events()
    """

    def __init__(self, session) -> None:
        self._session = session

    @staticmethod
    def connect(mcp_tools_url: str):
        """Return an async context manager that yields a ready ``MCPAgentTools``."""
        from contextlib import asynccontextmanager
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        @asynccontextmanager
        async def _ctx():
            async with streamablehttp_client(f"{mcp_tools_url}/mcp") as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    yield MCPAgentTools(session)

        return _ctx()

    async def _call(self, name: str, arguments: dict) -> str:
        result = await self._session.call_tool(name, arguments)
        for content in result.content:
            if hasattr(content, "text"):
                return content.text
        return "null"

    async def monitor_list_events(self, asset_id: str | None = None) -> list[dict]:
        raw = await self._call("monitor.list_events", {"asset_id": asset_id or ""})
        return json.loads(raw)

    async def logs_get_bundle(self, asset_id: str) -> dict:
        raw = await self._call("logs.get_bundle", {"asset_id": asset_id})
        return json.loads(raw)

    async def kb_search(
        self,
        signature: str,
        fallback_kb_id: str | None = None,
    ) -> dict | None:
        raw = await self._call(
            "kb.search",
            {"signature": signature, "fallback_kb_id": fallback_kb_id or ""},
        )
        return json.loads(raw)

    async def remediation_propose(
        self,
        fault_event_id: str,
        step_ids: list[str],
        summary: str = "",
    ) -> dict:
        raw = await self._call(
            "remediation.propose",
            {"fault_event_id": fault_event_id, "step_ids": step_ids, "summary": summary},
        )
        return json.loads(raw)

    async def remediation_execute(
        self,
        fault_event_id: str,
        approval_token: str,
        step_ids: list[str],
    ) -> dict:
        raw = await self._call(
            "remediation.execute",
            {
                "fault_event_id": fault_event_id,
                "approval_token": approval_token,
                "step_ids": json.dumps(step_ids),
            },
        )
        return json.loads(raw)

    async def list_llm_tool_schemas(self) -> list[dict]:
        """Build the LLM tool catalogue from the MCP server's own listing."""
        resp = await self._session.list_tools()
        schemas: list[dict] = []
        for tool in resp.tools:
            if tool.name not in LLM_EXPOSED_TOOLS:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": to_function_name(tool.name),
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    },
                }
            )
        return schemas

    async def call_llm_tool(self, name: str, arguments: dict) -> str:
        mcp_name = _FN_TO_MCP.get(name)
        if mcp_name is None:
            return _unknown_tool_result(name)
        return await self._call(mcp_name, arguments)


# ── Direct implementation (tests) ─────────────────────────────────────────────

@dataclass
class DirectAgentTools:
    """Calls business-logic functions directly — no MCP server needed in tests.

    Dependencies are injected so tests can use in-process service instances.
    """

    adapter: MonitoringAdapter
    orchestrator_client: httpx.AsyncClient
    kb_index: KBIndex
    token_store: ApprovalTokenStore
    fault_registry: FaultEventRegistry
    sim_client: SimulatorClient
    gateway_client: httpx.AsyncClient | None = None  # notify/propose tools

    async def monitor_list_events(self, asset_id: str | None = None) -> list[dict]:
        events = await monitor_list_events(self.adapter, asset_id=asset_id)
        return events  # already list[dict]

    async def logs_get_bundle(self, asset_id: str) -> dict:
        return await logs_get_bundle(self.orchestrator_client, asset_id=asset_id)

    async def kb_search(
        self,
        signature: str,
        fallback_kb_id: str | None = None,
    ) -> dict | None:
        return await kb_search(self.kb_index, signature=signature, fallback_kb_id=fallback_kb_id)

    def _gateway(self) -> httpx.AsyncClient:
        if self.gateway_client is None:
            raise RuntimeError("DirectAgentTools.gateway_client is required for notify/propose")
        return self.gateway_client

    async def notify_post_activity(
        self,
        fault_event_id: str,
        step: str,
        message: str,
    ) -> dict:
        return await notify_post_activity(
            self._gateway(), fault_event_id=fault_event_id, step=step, message=message
        )

    async def remediation_propose(
        self,
        fault_event_id: str,
        step_ids: list[str],
        summary: str = "",
    ) -> dict:
        return await remediation_propose(
            self._gateway(), fault_event_id=fault_event_id, step_ids=step_ids,
            summary=summary,
        )

    async def remediation_execute(
        self,
        fault_event_id: str,
        approval_token: str,
        step_ids: list[str],
    ) -> dict:
        async def _clear(asset_id: str) -> None:
            await self.sim_client.clear(asset_id)

        try:
            return await remediation_execute(
                fault_event_id=fault_event_id,
                approval_token=approval_token,
                step_ids=step_ids,
                token_store=self.token_store,
                fault_registry=self.fault_registry,
                clear_fn=_clear,
            )
        except RemediationError as exc:
            return exc.to_dict()

    async def list_llm_tool_schemas(self) -> list[dict]:
        return [json.loads(json.dumps(s)) for s in FALLBACK_TOOL_SCHEMAS]

    async def call_llm_tool(self, name: str, arguments: dict) -> str:
        try:
            if name == "monitor_list_events":
                result: object = await self.monitor_list_events(
                    arguments.get("asset_id") or None
                )
            elif name == "monitor_get_asset":
                result = await monitor_get_asset(self.adapter, asset_id=arguments["asset_id"])
            elif name == "monitor_list_assets":
                result = await monitor_list_assets(self.adapter)
            elif name == "logs_get_bundle":
                result = await self.logs_get_bundle(arguments["asset_id"])
            elif name == "kb_search":
                result = await self.kb_search(
                    arguments["signature"], arguments.get("fallback_kb_id") or None
                )
            elif name == "notify_post_activity":
                result = await self.notify_post_activity(
                    arguments["fault_event_id"],
                    arguments.get("step", "diagnose"),
                    arguments.get("message", ""),
                )
            elif name == "remediation_propose":
                result = await self.remediation_propose(
                    arguments["fault_event_id"],
                    list(arguments.get("step_ids") or []),
                    str(arguments.get("summary") or ""),
                )
            else:
                return _unknown_tool_result(name)
        except httpx.HTTPStatusError as exc:
            result = {"status": "error", "error": f"http_{exc.response.status_code}"}
        except KeyError as exc:
            result = {"status": "error", "error": f"missing_argument: {exc}"}
        return json.dumps(result)
