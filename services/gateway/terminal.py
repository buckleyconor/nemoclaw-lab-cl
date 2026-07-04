"""Embedded-terminal WS proxy (ADR-012).

Bridges the dashboard to the host-side terminal daemon so the operator's
single-port workflow (ssh -L 8001) keeps working: the browser speaks WS to
the Gateway, the Gateway opens a client WS to TERMINAL_WS_URL and injects
the bearer token — the token never reaches the browser.

Endpoints:
  GET /api/terminal/enabled   feature flag for the dashboard panel
  WS  /api/terminal/ws        transparent byte/control-frame proxy

Fail-safe: enabled only when TERMINAL_WS_URL and TERMINAL_TOKEN are both set
and TERMINAL_ENABLED != "0". The M9 shared deployment MUST keep this off.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

from fastapi import APIRouter, WebSocket
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

terminal_router = APIRouter()


def _config() -> tuple[str, str, bool]:
    url = os.environ.get("TERMINAL_WS_URL", "")
    token = os.environ.get("TERMINAL_TOKEN", "")
    enabled = bool(url and token) and os.environ.get("TERMINAL_ENABLED") != "0"
    return url, token, enabled


@terminal_router.get("/api/terminal/enabled")
async def terminal_enabled() -> dict:
    """Drives panel visibility — the dashboard hides the terminal when false."""
    _, _, enabled = _config()
    return {"enabled": enabled}


async def _browser_to_daemon(ws: WebSocket, daemon: ClientConnection) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            break
        if message.get("bytes") is not None:
            await daemon.send(message["bytes"])
        elif message.get("text") is not None:
            await daemon.send(message["text"])


async def _daemon_to_browser(daemon: ClientConnection, ws: WebSocket) -> None:
    async for message in daemon:
        if isinstance(message, bytes):
            await ws.send_bytes(message)
        else:
            await ws.send_text(message)


@terminal_router.websocket("/api/terminal/ws")
async def terminal_proxy(ws: WebSocket) -> None:
    url, token, enabled = _config()
    if not enabled:
        await ws.close(code=1008)
        return
    await ws.accept()

    try:
        async with connect(url, additional_headers={"Authorization": f"Bearer {token}"}) as daemon:
            pumps = {
                asyncio.ensure_future(_browser_to_daemon(ws, daemon)),
                asyncio.ensure_future(_daemon_to_browser(daemon, ws)),
            }
            _, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
    except (OSError, WebSocketException):
        pass  # daemon down/unreachable — fall through to closing the browser WS
    finally:
        with contextlib.suppress(RuntimeError):
            await ws.close()
