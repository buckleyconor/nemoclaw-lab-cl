"""FastMCP server — registers all four NemoClaw MCP tools.

Tool names exposed over Streamable HTTP:
  monitor.list_events   — list events from the active monitoring surface
  monitor.get_asset     — inspect a single asset's health state
  logs.get_bundle       — fetch scenario log bundle from Orchestrator
  kb.search             — semantic + deterministic KB article search
  remediation.execute   — HITL-gated fault remediation (token required)

The server reads its runtime state from ``app.state`` (populated by
``create_app()`` in main.py).  Tools receive injected dependencies so
the business-logic functions remain independently testable.
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from services.mcp_tools.adapters.redfish import RedfishAdapter
from services.mcp_tools.tools.kb import kb_search
from services.mcp_tools.tools.logs import logs_get_bundle
from services.mcp_tools.tools.monitor import (
    monitor_get_asset,
    monitor_list_assets,
    monitor_list_events,
)
from services.mcp_tools.tools.remediation import RemediationError, remediation_execute

# Module-level state — initialised by create_app() in main.py before the
# uvicorn worker accepts requests.
_state: dict = {}


mcp = FastMCP(
    "NemoClaw MCP Tools",
    # Disable DNS rebinding protection — the server runs inside Docker where
    # the agent connects via the service hostname (mcp-tools:8004), not localhost.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ──────────────────────────────────────────────────────────────────────────────
# monitor.*
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool(name="monitor.list_events")
async def _monitor_list_events(asset_id: str = "") -> str:
    """List active fault events from the monitoring surface.

    Args:
        asset_id: Optional. Filter to a specific asset. Empty string means all assets.
    """
    adapter = _state["adapter"]
    aid = asset_id if asset_id else None
    result = await monitor_list_events(adapter, asset_id=aid)
    return json.dumps(result)


@mcp.tool(name="monitor.get_asset")
async def _monitor_get_asset(asset_id: str) -> str:
    """Inspect health state for a single asset.

    Args:
        asset_id: The asset id (e.g. 'gpu-server-01').
    """
    adapter = _state["adapter"]
    result = await monitor_get_asset(adapter, asset_id=asset_id)
    return json.dumps(result)


@mcp.tool(name="monitor.list_assets")
async def _monitor_list_assets() -> str:
    """List all assets in the pack with their current health state."""
    adapter = _state["adapter"]
    result = await monitor_list_assets(adapter)
    return json.dumps(result)


# ──────────────────────────────────────────────────────────────────────────────
# logs.*
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool(name="logs.get_bundle")
async def _logs_get_bundle(asset_id: str) -> str:
    """Fetch the scenario log bundle for an asset.

    Returns scenario_id, asset_id, and the full log text. Fails with 404
    if no scenario is active for that asset.

    Args:
        asset_id: The asset whose log bundle to retrieve.
    """
    orchestrator_url = _state["orchestrator_url"]
    async with httpx.AsyncClient(base_url=orchestrator_url) as client:
        result = await logs_get_bundle(client, asset_id=asset_id)
    return json.dumps(result)


# ──────────────────────────────────────────────────────────────────────────────
# kb.*
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool(name="kb.search")
async def _kb_search(signature: str, fallback_kb_id: str = "") -> str:
    """Search the KB for an error signature.

    Returns the best-matching article (id, title, body_md, remediation_step_ids,
    score, via). Returns null JSON if no match.

    Args:
        signature:      Error signature or log extract from the fault logs.
        fallback_kb_id: Optional KB article id to use if semantic+deterministic
                        search finds nothing.
    """
    kb_index = _state["kb_index"]
    fid = fallback_kb_id if fallback_kb_id else None
    result = await kb_search(kb_index, signature=signature, fallback_kb_id=fid)
    return json.dumps(result)


# ──────────────────────────────────────────────────────────────────────────────
# remediation.*
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool(name="remediation.execute")
async def _remediation_execute(
    fault_event_id: str,
    approval_token: str,
    step_ids: str,
) -> str:
    """Execute approved remediation steps for a fault event.

    Requires a valid, unconsumed, approved ApprovalToken bound to fault_event_id.
    All step_ids must be within the scenario's remediation allowlist.

    Args:
        fault_event_id:  The FaultEvent id being remediated.
        approval_token:  Token string minted by the Gateway (from human approval).
        step_ids:        JSON array of step ids to execute
                         (e.g. '["drain_node","gpu_reset","verify_health"]').
    """
    token_store = _state["token_store"]
    fault_registry = _state["fault_registry"]
    simulator_url = _state["simulator_url"]

    try:
        parsed_steps: list[str] = json.loads(step_ids)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"status": "error", "error": "invalid_step_ids_json"})

    async def _clear(asset_id: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{simulator_url}/control/clear",
                json={"asset_id": asset_id},
            )

    try:
        result = await remediation_execute(
            fault_event_id=fault_event_id,
            approval_token=approval_token,
            step_ids=parsed_steps,
            token_store=token_store,
            fault_registry=fault_registry,
            clear_fn=_clear,
        )
    except RemediationError as exc:
        result = exc.to_dict()

    return json.dumps(result)
