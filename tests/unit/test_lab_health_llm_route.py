"""lab-health must not report a broken LLM route as healthy.

Added after the 2026-10-13 incident: the agent's LLM calls were failing for
two days (first a Bearer/x-api-key mismatch returning 401, then a mangled model
id returning 404) while `/api/lab-health` reported the inference proxy "ok",
because the check treated anything below 500 as healthy. A 401 and a 404 both
clear that bar — the proxy was indeed up, and completely useless to the agent.

These tests pin the replacement rule: only a 2xx (or a 429 that proves auth and
routing both worked) counts as ok.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from services.gateway import router


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""


class _FakeClient:
    """Stand-in for httpx.AsyncClient that answers with canned statuses.

    Everything answers 200 except the inference-proxy probe (`/v1/models`),
    which answers with `llm_status`, and the wake-hook POST, which must answer
    401 for that check to stay green (it asserts auth is wired, not open).
    """

    def __init__(self, llm_status: int | None, llm_error: Exception | None) -> None:
        self._llm_status = llm_status
        self._llm_error = llm_error

    def __call__(self, *args, **kwargs) -> _FakeClient:
        return self

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url: str, headers=None, **kwargs) -> _FakeResponse:
        if "/v1/models" in url:
            if self._llm_error is not None:
                raise self._llm_error
            return _FakeResponse(self._llm_status or 200)
        return _FakeResponse(200)

    async def post(self, url: str, headers=None, json=None, **kwargs) -> _FakeResponse:
        return _FakeResponse(401)


def _patch_client(monkeypatch, llm_status=None, llm_error=None) -> None:
    fake = _FakeClient(llm_status, llm_error)
    monkeypatch.setattr(router.httpx, "AsyncClient", fake)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROXY_PORT", "18100")
    monkeypatch.delenv("LLM_DIRECT", raising=False)
    for var in ("ORCHESTRATOR_URL", "MCP_TOOLS_URL", "SIMULATOR_URL"):
        monkeypatch.delenv(var, raising=False)


def _proxy_check(monkeypatch, llm_status=None, llm_error=None) -> dict:
    _patch_client(monkeypatch, llm_status, llm_error)
    payload = asyncio.run(router.lab_health())
    checks = {c["id"]: c for c in payload["checks"]}
    return {"healthy": payload["healthy"], **checks["inference-proxy"]}


@pytest.mark.parametrize(
    ("status", "expect_ok"),
    [
        (200, True),  # the only real proof the agent's route works
        (429, True),  # throttled, but auth and routing demonstrably worked
        (401, False),  # key rejected — the bug that hid the incident
        (403, False),
        (404, False),  # model id the endpoint does not have
        (500, False),
        (503, False),
    ],
)
def test_proxy_status_maps_to_health(monkeypatch, status: int, expect_ok: bool) -> None:
    result = _proxy_check(monkeypatch, llm_status=status)
    assert (result["status"] == "ok") is expect_ok, result
    assert result["healthy"] is expect_ok, result


def test_rejected_key_is_named_not_hidden(monkeypatch) -> None:
    """A 401 must say so — 'ok' with a 401 detail is what misled the demo."""
    result = _proxy_check(monkeypatch, llm_status=401)
    assert result["status"] == "fail"
    assert "401" in result["detail"]
    assert "key rejected" in result["detail"]


def test_missing_model_is_named(monkeypatch) -> None:
    result = _proxy_check(monkeypatch, llm_status=404)
    assert result["status"] == "fail"
    assert "no such model" in result["detail"]


def test_unreachable_proxy_still_fails(monkeypatch) -> None:
    result = _proxy_check(monkeypatch, llm_error=httpx.ConnectError("refused"))
    assert result["status"] == "fail"
    assert result["healthy"] is False


def test_direct_mode_opts_out(monkeypatch) -> None:
    """LLM_DIRECT=1 means no proxy exists — 'skip', never a failure."""
    _patch_client(monkeypatch, llm_status=404)
    monkeypatch.setenv("LLM_DIRECT", "1")  # after the helper, which clears it
    payload = asyncio.run(router.lab_health())
    check = {c["id"]: c for c in payload["checks"]}["inference-proxy"]
    assert check["status"] == "skip"
