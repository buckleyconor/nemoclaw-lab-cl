"""notify MCP tool — post agent narration to the Gateway activity feed.

Display-only by construction (ADR-010): writes a text line to the operator
dashboard's activity feed. It cannot change fault status, mint tokens, or
touch infrastructure state, so it is safe to expose to the LLM directly.
"""

from __future__ import annotations

import httpx

_ALLOWED_STEPS = {"detect", "diagnose", "search_kb", "present", "remediate", "resolved", "denied"}
_MAX_MESSAGE_LEN = 500


async def notify_post_activity(
    gateway_client: httpx.AsyncClient,
    fault_event_id: str,
    step: str,
    message: str,
) -> dict:
    """Post a plain-language activity update to the operator dashboard.

    Args:
        gateway_client:  Configured httpx.AsyncClient pointed at the Gateway.
        fault_event_id:  FaultEvent id the update relates to.
        step:            Feed category (detect|diagnose|search_kb|present|...).
                         Unknown values are coerced to "diagnose".
        message:         Free-text narration; truncated to 500 characters.

    Returns:
        dict with status "posted", or an error dict on Gateway failure.
    """
    if step not in _ALLOWED_STEPS:
        step = "diagnose"
    r = await gateway_client.post(
        "/api/agent/activity",
        json={
            "fault_event_id": fault_event_id,
            "step": step,
            "message": message[:_MAX_MESSAGE_LEN],
        },
    )
    if r.status_code >= 400:
        return {"status": "error", "error": f"gateway_{r.status_code}"}
    return {"status": "posted", "fault_event_id": fault_event_id}
