"""Shared Pydantic models for the NemoClaw lab framework.

Three categories:
- Authored models  (Pack, Scenario, KBArticle, SimulatorProfile) — loaded from YAML at startup.
- Runtime entities (Asset, FaultEvent, Notification, ActivityEvent, ApprovalToken) — held in SQLite/Redis.
- Enums            (AssetState, FaultEventStatus, ApprovalDecision, MonitoringAdapterType).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ──────────────────────────────────────────────────────────────────────────────
# Authored models  (loaded from YAML, schema-validated at pack load time)
# ──────────────────────────────────────────────────────────────────────────────

class MonitoringAdapterType(str, Enum):
    redfish = "redfish"
    generic = "generic"
    opcua = "opcua"  # future


class AssetNoun(BaseModel):
    singular: str
    plural: str


class PackAsset(BaseModel):
    id: str


class Pack(BaseModel):
    id: str
    name: str
    asset_noun: AssetNoun
    fleet_label: str
    monitoring_adapter: MonitoringAdapterType
    theme: str = "default"
    assets: list[PackAsset] = Field(min_length=1)


class EmitEvent(BaseModel):
    severity: str
    message_id: str


class EmitLogEntry(BaseModel):
    severity: str
    message: str


class ScenarioEmit(BaseModel):
    event: EmitEvent
    log_entries: list[EmitLogEntry] = Field(default_factory=list)


class RemediationStep(BaseModel):
    id: str
    label: str


class Scenario(BaseModel):
    id: str
    pack_id: str
    target_asset: str
    fault_type: str
    emit: ScenarioEmit
    log_bundle_ref: str
    error_signatures: list[str] = Field(min_length=1)
    kb_article_ref: str
    remediation_steps: list[RemediationStep] = Field(min_length=1)
    # Optional explicit log extract; derived from emit.log_entries[0] when absent.
    log_extract: str | None = None
    # "scaffold" marks packs that aren't yet demo-ready (placeholder content).
    status: Literal["active", "scaffold"] = "active"


class SimulatorAssetHealthyState(BaseModel):
    """Baseline telemetry the simulator surfaces when this asset is healthy."""

    system_status: str = "OK"
    power_status: str = "OK"
    gpu_count: int = 0


class SimulatorAsset(BaseModel):
    id: str
    type: str
    healthy_state: SimulatorAssetHealthyState = Field(
        default_factory=SimulatorAssetHealthyState
    )


class SimulatorProfile(BaseModel):
    pack_id: str
    assets: list[SimulatorAsset] = Field(min_length=1)


class KBArticle(BaseModel):
    id: str
    pack_id: str
    title: str
    body_md: str
    remediation_step_ids: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Runtime entities  (persisted in SQLite dev / Redis prod)
# ──────────────────────────────────────────────────────────────────────────────

class AssetState(str, Enum):
    healthy = "healthy"
    faulted = "faulted"


class Asset(BaseModel):
    id: str
    pack_id: str
    type: str
    state: AssetState = AssetState.healthy
    active_scenario_id: str | None = None


class FaultEventStatus(str, Enum):
    detected = "detected"
    diagnosing = "diagnosing"
    awaiting_approval = "awaiting_approval"
    remediating = "remediating"
    resolved = "resolved"
    denied = "denied"


class FaultEvent(BaseModel):
    id: str = Field(default_factory=_uuid)
    scenario_id: str
    asset_id: str
    detected_at: datetime = Field(default_factory=_now)
    status: FaultEventStatus = FaultEventStatus.detected
    kb_article_id: str | None = None
    log_extract: str | None = None


class Notification(BaseModel):
    id: str = Field(default_factory=_uuid)
    fault_event_id: str
    title: str
    body: str
    read: bool = False
    ts: datetime = Field(default_factory=_now)


class ActivityEvent(BaseModel):
    id: str = Field(default_factory=_uuid)
    fault_event_id: str
    step: str
    message: str
    ts: datetime = Field(default_factory=_now)


class ApprovalDecision(str, Enum):
    approved = "approved"
    denied = "denied"


class ApprovalToken(BaseModel):
    token: str
    fault_event_id: str
    decision: ApprovalDecision
    decided_by: str
    decided_at: datetime = Field(default_factory=_now)
    # Single-use: consumed=True after remediation.execute reads it.
    consumed: bool = False
