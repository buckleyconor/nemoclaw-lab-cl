"""FaultEventRegistry — tracks active fault events and their allowed remediation steps.

The Gateway (M5) registers a fault event when the agent first reports a fault.
The remediation tool checks the registry to validate that requested step IDs
are within the scenario's allowlist (SEC-05).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FaultEventRecord:
    fault_event_id: str
    asset_id: str
    scenario_id: str
    allowed_step_ids: list[str]


class FaultEventRegistry:
    def __init__(self) -> None:
        self._records: dict[str, FaultEventRecord] = {}

    def register(
        self,
        fault_event_id: str,
        asset_id: str,
        scenario_id: str,
        allowed_step_ids: list[str],
    ) -> FaultEventRecord:
        record = FaultEventRecord(
            fault_event_id=fault_event_id,
            asset_id=asset_id,
            scenario_id=scenario_id,
            allowed_step_ids=list(allowed_step_ids),
        )
        self._records[fault_event_id] = record
        return record

    def get(self, fault_event_id: str) -> FaultEventRecord | None:
        return self._records.get(fault_event_id)

    def clear(self) -> None:
        self._records.clear()
