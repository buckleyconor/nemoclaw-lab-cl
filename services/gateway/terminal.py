"""Embedded-terminal WS proxy (ADR-012).

Bridges the dashboard to the host-side terminal daemon so the operator's
single-port workflow (ssh -L 8001) keeps working: the browser speaks WS to
the Gateway, the Gateway opens a client WS to TERMINAL_WS_URL and injects
the bearer token — the token never reaches the browser.

Endpoints:
  GET  /api/terminal/enabled        feature flag for the dashboard panel
  WS   /api/terminal/ws             transparent byte/control-frame proxy
  POST /api/terminal/reset          forwards to the daemon's /reset
                                     (restricted mode only — 404s in shell
                                     mode)
  POST /api/terminal/switch-pack    forwards {"pack_id": str} to the
                                     daemon's /switch-pack (restarts the
                                     docker-compose stack on a different
                                     pack — always registered, any
                                     TERMINAL_MODE)
  GET  /api/terminal/config-status  forwards to the daemon's /config-status
                                     (restricted mode only) — lab-guide.html's
                                     fault-injection gate calls this before
                                     POSTing /api/presenter/inject

Fail-safe: enabled only when TERMINAL_WS_URL and TERMINAL_TOKEN are both set
and TERMINAL_ENABLED != "0". The M9 shared deployment MUST keep this off.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

import httpx
from fastapi import APIRouter, Request, WebSocket
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


def _daemon_http_url(ws_url: str, path: str) -> str:
    """ws://host:port/ws -> http://host:port/<path> (wss -> https)."""
    http_url = ws_url.replace("wss://", "https://").replace("ws://", "http://")
    if http_url.endswith("/ws"):
        http_url = http_url[: -len("/ws")]
    return f"{http_url}/{path.lstrip('/')}"


@terminal_router.post("/api/terminal/reset")
async def terminal_reset() -> dict:
    """Best-effort proxy to the daemon's /reset — see docs/lab-guide.html's
    "Ready for a different scenario?" button. Not a WS proxy: this is a
    single request/response, so a plain httpx POST (same client used
    elsewhere in the Gateway) is simpler than opening a client WS for it."""
    url, token, enabled = _config()
    if not enabled:
        return {"ok": False, "error": "terminal disabled"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _daemon_http_url(url, "reset"), headers={"Authorization": f"Bearer {token}"}
            )
        return resp.json()
    except httpx.HTTPError:
        return {"ok": False, "error": "daemon unreachable"}


@terminal_router.get("/api/terminal/config-status")
async def terminal_config_status() -> dict:
    """Best-effort proxy to the daemon's /config-status. `ok: false` (feature
    disabled, daemon unreachable, or shell-mode 404) means the caller should
    fail open and let injection proceed rather than block the demo on an
    unrelated outage — the gate only fires on a definite `configured: false`."""
    url, token, enabled = _config()
    if not enabled:
        return {"ok": False, "error": "terminal disabled"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                _daemon_http_url(url, "config-status"), headers={"Authorization": f"Bearer {token}"}
            )
        if resp.status_code != 200:
            return {"ok": False, "error": f"daemon returned {resp.status_code}"}
        return {"ok": True, **resp.json()}
    except httpx.HTTPError:
        return {"ok": False, "error": "daemon unreachable"}


@terminal_router.post("/api/terminal/switch-pack")
async def terminal_switch_pack(request: Request) -> dict:
    """Best-effort proxy to the daemon's /switch-pack — see docs/lab-guide.html's
    automated pack-switch flow. Short timeout: the daemon acks almost
    instantly (it spawns the restart detached rather than waiting on it)."""
    url, token, enabled = _config()
    if not enabled:
        return {"ok": False, "error": "terminal disabled"}
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _daemon_http_url(url, "switch-pack"),
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        return resp.json()
    except httpx.HTTPError:
        return {"ok": False, "error": "daemon unreachable"}


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
