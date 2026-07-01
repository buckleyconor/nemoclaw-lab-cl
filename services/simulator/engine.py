"""SimulatorEngine — asset state machine for the NemoClaw demo lab.

Holds healthy/faulted state per asset and provides inject/clear control.
The engine is pack-driven: it knows which assets exist and which scenario
data to surface when a fault is active. It is intentionally free of I/O;
the surface layer (Redfish / generic) reads from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from libs.common.pack_loader import LoadedPack


@dataclass
class InjectedFault:
    """Runtime snapshot of an active fault on one asset."""

    scenario_id: str
    asset_id: str
    fault_type: str
    injected_at: datetime
    event_severity: str
    event_message_id: str
    # List of (severity, message) pairs from the scenario's emit.log_entries.
    log_entries: list[tuple[str, str]] = field(default_factory=list)


class SimulatorEngine:
    """Holds asset state and controls fault injection/clearing.

    Thread-safety: asyncio single-threaded; no locks needed for the demo.
    """

    def __init__(self, pack: LoadedPack) -> None:
        self._pack = pack
        self._scenarios = {s.id: s for s in pack.scenarios}
        # asset_id → InjectedFault | None
        self._faults: dict[str, InjectedFault | None] = {
            a.id: None for a in pack.pack.assets
        }
        # asset_id → asset type (e.g. "server") from the simulator profile
        self._asset_types: dict[str, str] = {
            a.id: a.type for a in pack.simulator_profile.assets
        }

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def asset_ids(self) -> list[str]:
        return list(self._faults)

    def asset_type(self, asset_id: str) -> str:
        return self._asset_types.get(asset_id, "unknown")

    def is_faulted(self, asset_id: str) -> bool:
        return self._faults.get(asset_id) is not None

    def get_fault(self, asset_id: str) -> InjectedFault | None:
        return self._faults.get(asset_id)

    def all_states(self) -> dict[str, str]:
        """Return {asset_id: "healthy"|"faulted"} for all assets."""
        return {
            aid: ("faulted" if fault else "healthy")
            for aid, fault in self._faults.items()
        }

    def known_scenario_ids(self) -> list[str]:
        return list(self._scenarios)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def inject(self, asset_id: str, scenario_id: str) -> InjectedFault:
        """Inject a fault onto *asset_id* using *scenario_id*'s emit data.

        Raises:
            KeyError: If the asset or scenario is not known to this pack.
        """
        if asset_id not in self._faults:
            raise KeyError(f"Unknown asset: {asset_id!r}")
        if scenario_id not in self._scenarios:
            raise KeyError(f"Unknown scenario: {scenario_id!r}")

        scenario = self._scenarios[scenario_id]
        fault = InjectedFault(
            scenario_id=scenario_id,
            asset_id=asset_id,
            fault_type=scenario.fault_type,
            injected_at=datetime.now(timezone.utc),
            event_severity=scenario.emit.event.severity,
            event_message_id=scenario.emit.event.message_id,
            log_entries=[
                (entry.severity, entry.message)
                for entry in scenario.emit.log_entries
            ],
        )
        self._faults[asset_id] = fault
        return fault

    def clear(self, asset_id: str) -> None:
        """Return *asset_id* to healthy state.

        Raises:
            KeyError: If the asset is not known to this pack.
        """
        if asset_id not in self._faults:
            raise KeyError(f"Unknown asset: {asset_id!r}")
        self._faults[asset_id] = None
