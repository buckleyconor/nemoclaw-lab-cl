"""logs MCP tool — fetch scenario log bundle from the Orchestrator."""

from __future__ import annotations

import httpx


async def logs_get_bundle(
    orchestrator_client: httpx.AsyncClient,
    asset_id: str,
) -> dict:
    """Fetch the active scenario's log bundle for ``asset_id``.

    Args:
        orchestrator_client: Configured httpx.AsyncClient pointed at the Orchestrator.
        asset_id:            The asset whose active scenario's logs to retrieve.

    Returns:
        dict with keys: asset_id, scenario_id, log_text.

    Raises:
        httpx.HTTPStatusError: When the Orchestrator returns 4xx/5xx
            (e.g. no active scenario → 404).
    """
    r = await orchestrator_client.get(f"/api/assets/{asset_id}/logs")
    r.raise_for_status()
    return r.json()
