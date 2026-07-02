"""Post-approval remediation execution — deterministic, LLM-excluded (ADR-011).

Ported from agent/loop.py::_await_approval_and_execute (ADR-010), minus the
polling: the Gateway triggers execution the instant a human approves, from
post_decision(). The approval token is minted and consumed entirely on
trusted server-to-server paths — it never enters any LLM context, and the
OpenClaw agent has no execution tool to call (the plugin never registers
remediation.execute).

The MCP call itself is injectable so tests can wire in-process business
logic instead of a live Streamable HTTP session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from libs.common.models import AssetState, FaultEventStatus
from services.gateway.store import AssetRecord

log = logging.getLogger(__name__)

# Matches MCPAgentTools.remediation_execute / DirectAgentTools.remediation_execute:
# (fault_event_id, approval_token, step_ids) -> result dict.
RemediationExecuteFn = Callable[[str, str, list[str]], Awaitable[dict]]


class _Store(Protocol):
    async def update_fault_status(self, fault_id: str, status: FaultEventStatus): ...
    async def has_asset(self, asset_id: str) -> bool: ...
    async def get_asset(self, asset_id: str): ...
    async def set_asset(self, asset) -> None: ...
    async def create_activity(self, **kwargs): ...
    @property
    def sse(self): ...


def make_mcp_remediation_execute(mcp_tools_url: str) -> RemediationExecuteFn:
    """Production executor: call remediation.execute on the MCP Tools service
    over Streamable HTTP — the same wire the retired agent harness used."""

    async def _execute(fault_event_id: str, approval_token: str, step_ids: list[str]) -> dict:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with (
            streamablehttp_client(f"{mcp_tools_url}/mcp") as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "remediation.execute",
                {
                    "fault_event_id": fault_event_id,
                    "approval_token": approval_token,
                    "step_ids": json.dumps(step_ids),
                },
            )
            for content in result.content:
                if hasattr(content, "text"):
                    return json.loads(content.text)
        return {"status": "error", "error": "empty_mcp_response"}

    return _execute


@dataclass
class RemediationExecutor:
    """Runs the approved remediation sequence and narrates it to the operator.

    ``delay_scale`` scales the demo-pacing sleeps between narration lines;
    tests set it to 0.
    """

    execute_fn: RemediationExecuteFn
    delay_scale: float = 1.0
    _tasks: set[asyncio.Task] = field(default_factory=set)

    def start(
        self,
        store: _Store,
        *,
        fault_id: str,
        asset_id: str,
        scenario_id: str,
        step_ids: list[str],
        step_labels: dict[str, str],
        token: str,
    ) -> asyncio.Task:
        """Fire-and-track: execution runs as a background task so the
        approval POST returns immediately; the dashboard follows along
        over SSE."""
        task = asyncio.create_task(
            self._run(
                store,
                fault_id=fault_id,
                asset_id=asset_id,
                scenario_id=scenario_id,
                step_ids=step_ids,
                step_labels=step_labels,
                token=token,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _narrate(
        self, store: _Store, fault_id: str, step: str, message: str, delay: float = 0.0
    ) -> None:
        if delay and self.delay_scale:
            await asyncio.sleep(delay * self.delay_scale)
        try:
            evt = await store.create_activity(
                fault_event_id=fault_id, step=step, message=message
            )
            await store.sse.publish("activity", evt.model_dump(mode="json"))
        except Exception:
            log.warning("Failed to post activity %r for fault %s", step, fault_id)

    async def _set_status(
        self, store: _Store, fault_id: str, asset_id: str, status: FaultEventStatus
    ) -> None:
        updated = await store.update_fault_status(fault_id, status)
        if updated is None:
            return
        if status == FaultEventStatus.resolved and asset_id and await store.has_asset(asset_id):
            asset = await store.get_asset(asset_id)
            await store.set_asset(AssetRecord(
                id=asset.id,
                type=asset.type,
                state=AssetState.healthy,
                active_fault_event_id=None,
            ))
            await store.sse.publish("asset", {
                "id": asset_id,
                "state": "healthy",
                "active_fault_event_id": None,
            })
        await store.sse.publish("fault", updated.model_dump(mode="json"))

    async def _run(
        self,
        store: _Store,
        *,
        fault_id: str,
        asset_id: str,
        scenario_id: str,
        step_ids: list[str],
        step_labels: dict[str, str],
        token: str,
    ) -> str:
        try:
            await self._set_status(store, fault_id, asset_id, FaultEventStatus.remediating)
            await self._narrate(store, fault_id, "remediate",
                                "✅ Approval received — single-use token validated", delay=0.5)
            await self._narrate(store, fault_id, "remediate",
                                f"Beginning remediation: {len(step_ids)} approved steps",
                                delay=0.7)

            for step_id in step_ids:
                label = step_labels.get(step_id, step_id)
                await self._narrate(store, fault_id, "remediate", f"  ▶ {label}…", delay=1.2)
                await self._narrate(store, fault_id, "remediate",
                                    f"  ✓ {label} — complete", delay=1.5)

            result = await self.execute_fn(fault_id, token, step_ids)
            log.info("Remediation result for fault %s: %s", fault_id, result)

            if result.get("status") == "resolved":
                await self._narrate(store, fault_id, "remediate",
                                    f"Remediation complete — verifying {asset_id} health…",
                                    delay=1.0)
                await self._set_status(store, fault_id, asset_id, FaultEventStatus.resolved)
                await self._narrate(store, fault_id, "resolved",
                                    f"✅ {asset_id} returned to healthy state — fault cleared",
                                    delay=0.8)
                return "resolved"

            error = str(result.get("error", "unknown"))
            log.error("Remediation failed for fault %s: %s", fault_id, error)
            await self._narrate(store, fault_id, "remediate", f"⚠ Remediation error: {error}")
            return f"error:{error}"
        except Exception:
            log.exception("Remediation execution crashed for fault %s", fault_id)
            await self._narrate(store, fault_id, "remediate",
                                "⚠ Remediation error: internal execution failure")
            return "error:internal"
