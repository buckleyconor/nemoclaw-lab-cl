"""Telling the agent what happened, not just that something happened.

`/hooks/wake mode=now` means "run the heartbeat immediately" in OpenClaw's own
wake-reason matrix — it carries no prompt, so a fault injected from the
dashboard never reached the agent as a message, and the agent had to rediscover
it via monitor_list_events. These tests pin the replacement: the terminal
daemon runs `nemoclaw <sandbox> agent -m …` on the host, and the Gateway only
falls back to the wake hook when that genuinely cannot happen.

The most important assertion here is the one that would otherwise rot quietly:
when the daemon confirms the turn started, the wake hook must NOT also fire.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from services.gateway import router as gateway_router
from services.gateway import terminal as gateway_terminal
from services.terminal.main import create_app as create_daemon_app

SANDBOX = "infra-sentinel"
SCENARIO = "scn-nvlink-down"
ASSET = "gpu-server-02"
# The sentence the agent is supposed to see — identical on both channels, so
# switching transport does not silently change the prompt the persona was
# tuned against.
FAULT_TEXT = (
    f"fault-event: scenario {SCENARIO} injected on {ASSET} — "
    "run the Infrastructure Fault Response program now"
)


# ─────────────────────────────────────────────────────────────────────────────
# Terminal daemon — /wake-agent spawns the real thing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def daemon_restricted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Restricted-mode daemon (the mode that owns a sandbox) with Popen captured
    and the log redirected into tmp_path, so tests never touch the real home."""
    monkeypatch.setenv("TERMINAL_MODE", "restricted")
    monkeypatch.setenv("SANDBOX_NAME", SANDBOX)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    spawned: list[dict] = []

    class _FakePopen:
        pid = 4242

        def __init__(self, argv: list[str], **kwargs: object) -> None:
            spawned.append({"argv": argv, **kwargs})

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    from fastapi.testclient import TestClient

    with TestClient(create_daemon_app(token="secret")) as client:
        yield client, spawned


def test_daemon_wake_agent_requires_auth(daemon_restricted) -> None:
    client, spawned = daemon_restricted
    resp = client.post("/wake-agent", json={"scenario_id": SCENARIO, "asset_id": ASSET})
    assert resp.status_code == 401
    assert spawned == []


@pytest.mark.parametrize(
    "scenario_id, asset_id",
    [
        ("scn-nvlink-down; rm -rf /", ASSET),
        ("scn nvlink", ASSET),
        ("", ASSET),
        (SCENARIO, ""),
        (SCENARIO, "gpu server 02"),
        (SCENARIO, "../../etc/passwd"),
    ],
)
def test_daemon_wake_agent_refuses_ids_that_are_not_ids(
    daemon_restricted, scenario_id: str, asset_id: str
) -> None:
    """The whole reason the body is structured and not free text: a caller must
    not be able to dictate what the agent is told."""
    client, spawned = daemon_restricted
    resp = client.post(
        "/wake-agent",
        headers={"Authorization": "Bearer secret"},
        json={"scenario_id": scenario_id, "asset_id": asset_id},
    )
    assert resp.status_code == 400
    assert spawned == []


def test_daemon_wake_agent_spawns_the_supported_text_channel(daemon_restricted) -> None:
    client, spawned = daemon_restricted
    resp = client.post(
        "/wake-agent",
        headers={"Authorization": "Bearer secret"},
        json={"scenario_id": SCENARIO, "asset_id": ASSET},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    (call,) = spawned
    assert call["argv"] == [
        "nemoclaw",
        SANDBOX,
        "agent",
        "-m",
        FAULT_TEXT,
        "--session-key",
        "agent:main:main",
    ]
    # Detached and acked early, like /switch-pack: an agent turn outlives the
    # request that started it and must not hold the injection open.
    assert call["start_new_session"] is True


def test_daemon_wake_agent_absent_in_shell_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raw login-shell host has no persona to brief — no endpoint to brief it with."""
    monkeypatch.delenv("TERMINAL_MODE", raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(create_daemon_app(token="secret"))
    assert (
        client.post(
            "/wake-agent",
            headers={"Authorization": "Bearer secret"},
            json={"scenario_id": SCENARIO, "asset_id": ASSET},
        ).status_code
        == 404
    )


# ─────────────────────────────────────────────────────────────────────────────
# Gateway — channel choice
# ─────────────────────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = str(body)

    def json(self) -> dict:
        return self._body


def _patch_posts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    daemon: _Resp | Exception | None,
    hook: _Resp | None = None,
) -> list[str]:
    """Record every outbound POST by URL, answering the daemon and the hook
    independently. `daemon` may be an exception to simulate an unreachable host."""
    import httpx

    hits: list[str] = []

    async def fake_post(self: object, url: str, **_: object) -> _Resp:
        hits.append(url)
        if url.endswith("/wake-agent"):
            if isinstance(daemon, Exception):
                raise daemon
            return daemon or _Resp(404, {"detail": "Not Found"})
        return hook or _Resp(200, {"ok": True, "mode": "now"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return hits


def _gateway_hook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMINAL_WS_URL", "ws://host.docker.internal:8005/ws")
    monkeypatch.setenv("TERMINAL_TOKEN", "secret")
    monkeypatch.delenv("TERMINAL_ENABLED", raising=False)
    monkeypatch.setenv("OPENCLAW_HOOK_URL", "http://host.docker.internal:18789")
    monkeypatch.setenv("OPENCLAW_HOOK_TOKEN", "hooksecret")


def test_daemon_confirmation_is_not_also_woken_by_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """The anti-regression guard. Both channels firing means two agent turns per
    injection — and it would look like success from the dashboard."""
    _gateway_hook_env(monkeypatch)
    hits = _patch_posts(
        monkeypatch, daemon=_Resp(200, {"ok": True, "status": "agent-turn-started"})
    )
    asyncio.run(gateway_router._fire_agent_webhook(SCENARIO, ASSET))
    assert hits == ["http://host.docker.internal:8005/wake-agent"]


def test_daemon_message_carries_the_fault_and_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    _gateway_hook_env(monkeypatch)
    sent: dict = {}

    async def fake_post(self: object, url: str, **kw: object) -> _Resp:
        sent.update(url=url, **kw)
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    asyncio.run(gateway_router._fire_agent_webhook(SCENARIO, ASSET))
    assert sent["json"] == {"scenario_id": SCENARIO, "asset_id": ASSET}
    assert sent["headers"] == {"Authorization": "Bearer secret"}


@pytest.mark.parametrize(
    "daemon",
    [
        pytest.param(_Resp(404, {"detail": "Not Found"}), id="daemon-too-old"),
        pytest.param(_Resp(200, {"ok": False}), id="daemon-declined"),
        pytest.param(_Resp(401), id="token-mismatch"),
    ],
)
def test_daemon_cannot_deliver_falls_back_to_the_wake_hook(
    monkeypatch: pytest.MonkeyPatch, daemon: _Resp
) -> None:
    _gateway_hook_env(monkeypatch)
    hits = _patch_posts(monkeypatch, daemon=daemon)
    asyncio.run(gateway_router._fire_agent_webhook(SCENARIO, ASSET))
    assert hits == [
        "http://host.docker.internal:8005/wake-agent",
        "http://host.docker.internal:18789/hooks/wake",
    ]


def test_unreachable_daemon_falls_back_to_the_wake_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    _gateway_hook_env(monkeypatch)
    hits = _patch_posts(monkeypatch, daemon=httpx.ConnectError("connection refused"))
    asyncio.run(gateway_router._fire_agent_webhook(SCENARIO, ASSET))
    assert hits[-1] == "http://host.docker.internal:18789/hooks/wake"


def test_terminal_disabled_uses_the_wake_hook_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell mode / TERMINAL_ENABLED=0 / the M9 layout: the old path must keep
    working on its own, not become a hard dependency on the daemon."""
    _gateway_hook_env(monkeypatch)
    monkeypatch.setenv("TERMINAL_ENABLED", "0")
    hits = _patch_posts(monkeypatch, daemon=None)
    asyncio.run(gateway_router._fire_agent_webhook(SCENARIO, ASSET))
    assert hits == ["http://host.docker.internal:18789/hooks/wake"]


def test_unconfigured_hook_still_uses_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse direction: a host with a daemon but no hook config still
    briefs the agent — previously that combination did nothing at all."""
    monkeypatch.setenv("TERMINAL_WS_URL", "ws://host.docker.internal:8005/ws")
    monkeypatch.setenv("TERMINAL_TOKEN", "secret")
    monkeypatch.delenv("TERMINAL_ENABLED", raising=False)
    monkeypatch.delenv("OPENCLAW_HOOK_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_HOOK_TOKEN", raising=False)
    hits = _patch_posts(monkeypatch, daemon=_Resp(200, {"ok": True}))
    asyncio.run(gateway_router._fire_agent_webhook(SCENARIO, ASSET))
    assert hits == ["http://host.docker.internal:8005/wake-agent"]


def test_wake_agent_sends_nothing_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control for the proxy itself: with the feature off it must not
    touch the network at all."""
    import httpx

    calls: list[str] = []

    async def no_post(self: object, url: str, **_: object) -> None:
        calls.append(url)
        raise AssertionError("must not send anything while disabled")

    monkeypatch.delenv("TERMINAL_WS_URL", raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "post", no_post)
    assert asyncio.run(gateway_terminal.wake_agent(SCENARIO, ASSET)) is False
    assert calls == []
