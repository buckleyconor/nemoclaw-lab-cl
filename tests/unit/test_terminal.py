"""Embedded operator terminal (ADR-012): daemon auth/PTY + gateway feature gate."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.terminal.main import create_app as create_daemon_app

# ─────────────────────────────────────────────────────────────────────────────
# Terminal daemon — token gate
# ─────────────────────────────────────────────────────────────────────────────


def test_daemon_refuses_to_start_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERMINAL_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TERMINAL_TOKEN"):
        create_daemon_app()


@pytest.mark.parametrize(
    "headers",
    [
        {},  # no Authorization at all
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Basic secret"},  # wrong scheme, right token
    ],
)
def test_daemon_rejects_bad_auth_before_spawning(headers: dict[str, str]) -> None:
    client = TestClient(create_daemon_app(token="secret"))
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws", headers=headers),
    ):
        pass
    assert exc.value.code == 1008  # policy violation


def test_daemon_healthz_is_open() -> None:
    client = TestClient(create_daemon_app(token="secret"))
    assert client.get("/healthz").json() == {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Terminal daemon — PTY session over the wire protocol
# ─────────────────────────────────────────────────────────────────────────────


def test_daemon_pty_roundtrip_and_exit() -> None:
    """Authorized WS gets a live shell: echo round-trips, exit sends control frame."""
    client = TestClient(create_daemon_app(token="secret"))
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer secret"}) as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 30}))
        ws.send_bytes(b"echo terminal-$((20+22))\n")

        output = b""
        exit_frame: dict | None = None
        for _ in range(200):
            message = ws.receive()
            if message.get("bytes") is not None:
                output += message["bytes"]
                # Marker is computed shell-side so the echoed input can't match.
                if b"terminal-42" in output and exit_frame is None:
                    ws.send_bytes(b"exit\n")
            elif message.get("text") is not None:
                exit_frame = json.loads(message["text"])
                break
            elif message["type"] == "websocket.disconnect":
                break

        assert b"terminal-42" in output
        assert exit_frame is not None and exit_frame["type"] == "exit"


# ─────────────────────────────────────────────────────────────────────────────
# Gateway — /api/terminal/enabled fail-safe gate
# ─────────────────────────────────────────────────────────────────────────────


def _gateway_client() -> TestClient:
    from services.gateway.main import create_app

    return TestClient(create_app())


def test_gateway_terminal_disabled_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TERMINAL_WS_URL", raising=False)
    monkeypatch.delenv("TERMINAL_TOKEN", raising=False)
    monkeypatch.delenv("TERMINAL_ENABLED", raising=False)
    with _gateway_client() as client:
        assert client.get("/api/terminal/enabled").json() == {"enabled": False}


def test_gateway_terminal_enabled_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMINAL_WS_URL", "ws://host.docker.internal:8005/ws")
    monkeypatch.setenv("TERMINAL_TOKEN", "secret")
    monkeypatch.delenv("TERMINAL_ENABLED", raising=False)
    with _gateway_client() as client:
        assert client.get("/api/terminal/enabled").json() == {"enabled": True}


def test_gateway_terminal_kill_switch_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMINAL_WS_URL", "ws://host.docker.internal:8005/ws")
    monkeypatch.setenv("TERMINAL_TOKEN", "secret")
    monkeypatch.setenv("TERMINAL_ENABLED", "0")
    with _gateway_client() as client:
        assert client.get("/api/terminal/enabled").json() == {"enabled": False}


def test_gateway_terminal_ws_closed_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy must not dial anything when the feature is off."""
    monkeypatch.delenv("TERMINAL_WS_URL", raising=False)
    monkeypatch.delenv("TERMINAL_TOKEN", raising=False)
    with _gateway_client() as client:
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/api/terminal/ws"),
        ):
            pass
        assert exc.value.code == 1008
