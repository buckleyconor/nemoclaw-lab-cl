"""AgentTools — abstraction over the MCP tool calls.

Two implementations:
  MCPAgentTools    — production; calls the MCP Tools service over Streamable HTTP.
  DirectAgentTools — tests; calls business-logic functions in-process (no HTTP).
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
from services.mcp_tools.tools.monitor import monitor_list_events
from services.mcp_tools.tools.remediation import RemediationError, remediation_execute
from services.orchestrator.simulator_client import SimulatorClient


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

    async def remediation_execute(
        self,
        fault_event_id: str,
        approval_token: str,
        step_ids: list[str],
    ) -> dict: ...


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

    async def monitor_list_events(self, asset_id: str | None = None) -> list[dict]:
        from dataclasses import asdict
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
