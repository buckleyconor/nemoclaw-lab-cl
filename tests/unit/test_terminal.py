"""Embedded operator terminal (ADR-012): daemon auth/PTY + gateway feature gate."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.terminal.main import create_app as create_daemon_app

if TYPE_CHECKING:
    from pathlib import Path

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
# Terminal daemon — TERMINAL_MODE=restricted (ADR-013)
# ─────────────────────────────────────────────────────────────────────────────


def test_daemon_restricted_mode_refuses_to_start_without_sandbox_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMINAL_MODE", "restricted")
    monkeypatch.delenv("SANDBOX_NAME", raising=False)
    with pytest.raises(RuntimeError, match="SANDBOX_NAME"):
        create_daemon_app(token="secret")


def _write_fake_bin(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_daemon_restricted_mode_console_menu_edit_and_push_over_the_wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Selecting a menu item drives download(skip)->edit->push with real argv,
    entirely through the WS/PTY wire protocol — no real nemoclaw/vim needed."""
    # argv: [nemoclaw_bin, sandbox_name, subcommand, ...] -> $1=sandbox_name, $2=subcommand
    fake_nemoclaw = tmp_path / "fake-nemoclaw"
    _write_fake_bin(
        fake_nemoclaw,
        """
        case "$2" in
          download) exit 1 ;;
          upload) echo "PUSHED-UPLOAD $3 -> $4" ;;
        esac
        """,
    )
    fake_editor = tmp_path / "fake-editor"
    _write_fake_bin(fake_editor, 'echo "edited" > "$1"')

    monkeypatch.setenv("TERMINAL_MODE", "restricted")
    monkeypatch.setenv("SANDBOX_NAME", "nemoclaw-lab")
    monkeypatch.setenv("TERMINAL_WORKSPACE_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("TERMINAL_NEMOCLAW_BIN", str(fake_nemoclaw))
    monkeypatch.setenv("TERMINAL_EDITOR_BIN", str(fake_editor))
    monkeypatch.setenv("TERMINAL_EDITOR_ARGS", "")

    client = TestClient(create_daemon_app(token="secret"))
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer secret"}) as ws:
        ws.send_bytes(b"1\n")  # select "Edit SOUL.md"

        output = b""
        exit_frame: dict | None = None
        dismissed = False
        for _ in range(200):
            message = ws.receive()
            if message.get("bytes") is not None:
                output += message["bytes"]
                if b"PUSHED-UPLOAD" in output and not dismissed:
                    dismissed = True
                    ws.send_bytes(b"\n")  # dismiss "Press Enter to return to the menu..."
                    ws.send_bytes(b"q\n")  # then quit from the redrawn menu
            elif message.get("text") is not None:
                exit_frame = json.loads(message["text"])
                break
            elif message["type"] == "websocket.disconnect":
                break

        assert b"NemoClaw operator console" in output
        assert b"PUSHED-UPLOAD" in output
        assert b"\x1b[32m\xe2\x9c\x93 Edit SOUL.md" in output  # completed item 1 rendered green
        assert exit_frame is not None and exit_frame["type"] == "exit"


# ─────────────────────────────────────────────────────────────────────────────
# Terminal daemon — POST /reset (non-interactive equivalent of console option 7)
# ─────────────────────────────────────────────────────────────────────────────


def test_daemon_reset_route_absent_in_shell_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TERMINAL_MODE", raising=False)
    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post("/reset", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
    ],
)
def test_daemon_reset_requires_auth_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    monkeypatch.setenv("TERMINAL_MODE", "restricted")
    monkeypatch.setenv("SANDBOX_NAME", "nemoclaw-lab")
    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post("/reset", headers=headers)
    assert resp.status_code == 401


def test_daemon_reset_calls_run_reset_with_loaded_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.terminal.main as terminal_main

    monkeypatch.setenv("TERMINAL_MODE", "restricted")
    monkeypatch.setenv("SANDBOX_NAME", "nemoclaw-lab")

    calls: list[object] = []

    def fake_run_reset(config: object) -> bool:
        calls.append(config)
        return True

    monkeypatch.setattr(terminal_main.console, "run_reset", fake_run_reset)

    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post("/reset", headers={"Authorization": "Bearer secret"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(calls) == 1
    assert calls[0].sandbox_name == "nemoclaw-lab"  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# Terminal daemon — POST /switch-pack (automated pack switching)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
    ],
)
def test_daemon_switch_pack_requires_auth(headers: dict[str, str]) -> None:
    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post("/switch-pack", json={"pack_id": "laptop-fleet"}, headers=headers)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "pack_id",
    ["", "not a real pack", "../../etc/passwd", "UPPER-CASE", "laptop-fleet; rm -rf /"],
)
def test_daemon_switch_pack_rejects_malformed_pack_id(pack_id: str) -> None:
    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post(
        "/switch-pack", json={"pack_id": pack_id}, headers={"Authorization": "Bearer secret"}
    )
    assert resp.status_code == 400


def test_daemon_switch_pack_route_absent_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M9 per-tenant daemons (run-terminal-tenant.sh) set SWITCH_PACK_ENABLED=0:
    the stack is Helm-managed there, and docker compose down/up against the
    agent host's checkout would be destructive, not a pack switch."""
    monkeypatch.setenv("SWITCH_PACK_ENABLED", "0")
    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post(
        "/switch-pack",
        json={"pack_id": "laptop-fleet"},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 404


def test_daemon_switch_pack_rejects_unknown_but_validly_shaped_pack_id() -> None:
    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post(
        "/switch-pack",
        json={"pack_id": "no-such-pack"},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 404


def test_daemon_switch_pack_spawns_detached_and_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.terminal.main as terminal_main

    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            calls.append((argv, kwargs))

    monkeypatch.setattr(terminal_main.subprocess, "Popen", FakePopen)

    client = TestClient(create_daemon_app(token="secret"))
    resp = client.post(
        "/switch-pack",
        json={"pack_id": "laptop-fleet"},
        headers={"Authorization": "Bearer secret"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "switch-started", "pack_id": "laptop-fleet"}
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[-1] == "laptop-fleet"
    assert argv[0].endswith("switch-pack.sh")
    assert kwargs.get("start_new_session") is True


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


# ─────────────────────────────────────────────────────────────────────────────
# Gateway — /api/terminal/reset proxy
# ─────────────────────────────────────────────────────────────────────────────


def test_gateway_terminal_reset_disabled_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TERMINAL_WS_URL", raising=False)
    monkeypatch.delenv("TERMINAL_TOKEN", raising=False)
    with _gateway_client() as client:
        resp = client.post("/api/terminal/reset")
        assert resp.json() == {"ok": False, "error": "terminal disabled"}


def test_gateway_terminal_reset_forwards_to_daemon_http_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    monkeypatch.setenv("TERMINAL_WS_URL", "ws://host.docker.internal:8005/ws")
    monkeypatch.setenv("TERMINAL_TOKEN", "secret")

    calls: list[tuple[str, dict[str, str] | None]] = []

    class FakeResponse:
        def json(self) -> dict:
            return {"ok": True}

    async def fake_post(
        self: object, url: str, headers: dict | None = None, **_: object
    ) -> FakeResponse:
        calls.append((url, headers))
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with _gateway_client() as client:
        resp = client.post("/api/terminal/reset")

    assert resp.json() == {"ok": True}
    assert calls == [("http://host.docker.internal:8005/reset", {"Authorization": "Bearer secret"})]


def test_gateway_terminal_switch_pack_disabled_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TERMINAL_WS_URL", raising=False)
    monkeypatch.delenv("TERMINAL_TOKEN", raising=False)
    with _gateway_client() as client:
        resp = client.post("/api/terminal/switch-pack", json={"pack_id": "laptop-fleet"})
        assert resp.json() == {"ok": False, "error": "terminal disabled"}


def test_gateway_terminal_switch_pack_forwards_body_to_daemon_http_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    monkeypatch.setenv("TERMINAL_WS_URL", "ws://host.docker.internal:8005/ws")
    monkeypatch.setenv("TERMINAL_TOKEN", "secret")

    calls: list[tuple[str, dict | None, object]] = []

    class FakeResponse:
        def json(self) -> dict:
            return {"ok": True, "status": "switch-started", "pack_id": "laptop-fleet"}

    async def fake_post(
        self: object,
        url: str,
        headers: dict | None = None,
        json: object = None,
        **_: object,
    ) -> FakeResponse:
        calls.append((url, headers, json))
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with _gateway_client() as client:
        resp = client.post("/api/terminal/switch-pack", json={"pack_id": "laptop-fleet"})

    assert resp.json() == {"ok": True, "status": "switch-started", "pack_id": "laptop-fleet"}
    assert calls == [
        (
            "http://host.docker.internal:8005/switch-pack",
            {"Authorization": "Bearer secret"},
            {"pack_id": "laptop-fleet"},
        )
    ]
