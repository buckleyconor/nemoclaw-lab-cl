"""SimulatorClient — abstraction over the Simulator's control API.

The ``HttpSimulatorClient`` drives a real running Simulator service.
The ``FakeSimulatorClient`` is used in tests to record calls without
needing a live Simulator.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class SimulatorClient(Protocol):
    """Interface the Orchestrator uses to control the Simulator."""

    async def inject(self, asset_id: str, scenario_id: str) -> None: ...

    async def clear(self, asset_id: str) -> None: ...


class HttpSimulatorClient:
    """Drives the Simulator service over HTTP (production use)."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def inject(self, asset_id: str, scenario_id: str) -> None:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._base_url}/control/inject",
                json={"asset_id": asset_id, "scenario_id": scenario_id},
                timeout=10.0,
            )
            r.raise_for_status()

    async def clear(self, asset_id: str) -> None:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._base_url}/control/clear",
                json={"asset_id": asset_id},
                timeout=10.0,
            )
            r.raise_for_status()


class FakeSimulatorClient:
    """In-process simulator client for unit and integration tests.

    Records calls for assertion; never makes real HTTP requests.
    """

    def __init__(self) -> None:
        self.injected: list[tuple[str, str]] = []  # [(asset_id, scenario_id)]
        self.cleared: list[str] = []  # [asset_id]

    async def inject(self, asset_id: str, scenario_id: str) -> None:
        self.injected.append((asset_id, scenario_id))

    async def clear(self, asset_id: str) -> None:
        self.cleared.append(asset_id)

    def reset_history(self) -> None:
        self.injected.clear()
        self.cleared.clear()
