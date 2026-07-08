"""Redfish surface for the datacenter-xe9680 pack.

Exposes a minimal Redfish-style API seeded with real XE9780L response shapes.
The monitor MCP tool's Redfish adapter (M4) reads these endpoints.

Implemented endpoints:
  GET /redfish/v1/                                          Service root
  GET /redfish/v1/Systems                                   System collection
  GET /redfish/v1/Systems/{asset_id}                        Single system + health
  GET /redfish/v1/Systems/{asset_id}/LogServices            Log service collection
  GET /redfish/v1/Systems/{asset_id}/LogServices/DCGM       DCGM log service
  GET /redfish/v1/Systems/{asset_id}/LogServices/DCGM/Entries  Fault log entries
  GET /redfish/v1/Chassis                                   Chassis collection
  GET /redfish/v1/Chassis/{asset_id}                        Single chassis
  GET /redfish/v1/Chassis/{asset_id}/Power                  PSU status
  GET /redfish/v1/EventService                              Event service
  GET /redfish/v1/EventService/Events                       Active events (all assets)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from services.simulator.engine import InjectedFault, SimulatorEngine

router = APIRouter(tags=["redfish"])


def _engine(request: Request) -> SimulatorEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


def _require_asset(asset_id: str, engine: SimulatorEngine) -> None:
    if asset_id not in engine.asset_ids:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id!r}")


# ──────────────────────────────────────────────────────────────────────────────
# Service root
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/redfish/v1/")
async def service_root() -> dict:
    return {
        "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
        "@odata.id": "/redfish/v1/",
        "Id": "RootService",
        "Name": "Root Service",
        "RedfishVersion": "1.13.0",
        "Systems": {"@odata.id": "/redfish/v1/Systems"},
        "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
        "EventService": {"@odata.id": "/redfish/v1/EventService"},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Systems  (each pack asset is one Redfish System)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/redfish/v1/Systems")
async def systems_collection(engine: SimulatorEngine = Depends(_engine)) -> dict:
    members = [{"@odata.id": f"/redfish/v1/Systems/{aid}"} for aid in engine.asset_ids]
    return {
        "@odata.type": "#ComputerSystemCollection.ComputerSystemCollection",
        "@odata.id": "/redfish/v1/Systems",
        "Name": "Computer System Collection",
        "Members": members,
        "Members@odata.count": len(members),
    }


@router.get("/redfish/v1/Systems/{asset_id}")
async def get_system(asset_id: str, engine: SimulatorEngine = Depends(_engine)) -> dict:
    _require_asset(asset_id, engine)
    fault = engine.get_fault(asset_id)
    health = "Critical" if fault else "OK"
    return {
        "@odata.type": "#ComputerSystem.v1_13_0.ComputerSystem",
        "@odata.id": f"/redfish/v1/Systems/{asset_id}",
        "Id": asset_id,
        "Name": asset_id,
        "SystemType": engine.asset_type(asset_id),
        "Status": {
            "State": "Enabled",
            "Health": health,
            "HealthRollup": health,
        },
        "LogServices": {"@odata.id": f"/redfish/v1/Systems/{asset_id}/LogServices"},
    }


# ──────────────────────────────────────────────────────────────────────────────
# LogServices  (DCGM log service surfaces GPU/system faults)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/redfish/v1/Systems/{asset_id}/LogServices")
async def log_services_collection(asset_id: str, engine: SimulatorEngine = Depends(_engine)) -> dict:
    _require_asset(asset_id, engine)
    return {
        "@odata.type": "#LogServiceCollection.LogServiceCollection",
        "@odata.id": f"/redfish/v1/Systems/{asset_id}/LogServices",
        "Name": "Log Service Collection",
        "Members": [
            {"@odata.id": f"/redfish/v1/Systems/{asset_id}/LogServices/DCGM"}
        ],
        "Members@odata.count": 1,
    }


@router.get("/redfish/v1/Systems/{asset_id}/LogServices/DCGM")
async def dcgm_log_service(asset_id: str, engine: SimulatorEngine = Depends(_engine)) -> dict:
    _require_asset(asset_id, engine)
    return {
        "@odata.type": "#LogService.v1_2_0.LogService",
        "@odata.id": f"/redfish/v1/Systems/{asset_id}/LogServices/DCGM",
        "Id": "DCGM",
        "Name": "DCGM Log Service",
        "Description": "NVIDIA Data Center GPU Manager event log",
        "Entries": {"@odata.id": f"/redfish/v1/Systems/{asset_id}/LogServices/DCGM/Entries"},
    }


@router.get("/redfish/v1/Systems/{asset_id}/LogServices/DCGM/Entries")
async def dcgm_log_entries(asset_id: str, engine: SimulatorEngine = Depends(_engine)) -> dict:
    _require_asset(asset_id, engine)
    fault = engine.get_fault(asset_id)
    members = _build_log_entry_members(asset_id, fault)
    return {
        "@odata.type": "#LogEntryCollection.LogEntryCollection",
        "@odata.id": f"/redfish/v1/Systems/{asset_id}/LogServices/DCGM/Entries",
        "Name": "Log Entry Collection",
        "Members": members,
        "Members@odata.count": len(members),
    }


def _build_log_entry_members(asset_id: str, fault: InjectedFault | None) -> list[dict]:
    if fault is None:
        return []
    ts = fault.injected_at.isoformat()
    entries = []
    for idx, (severity, message) in enumerate(fault.log_entries):
        entries.append({
            "@odata.type": "#LogEntry.v1_9_0.LogEntry",
            "@odata.id": f"/redfish/v1/Systems/{asset_id}/LogServices/DCGM/Entries/{idx + 1}",
            "Id": str(idx + 1),
            "Name": "Log Entry",
            "EntryType": "Event",
            "Severity": severity,
            "Message": message,
            "MessageId": fault.event_message_id,
            "Created": ts,
        })
    return entries


# ──────────────────────────────────────────────────────────────────────────────
# Chassis  (PSU status — relevant for psu_failure fault type)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/redfish/v1/Chassis")
async def chassis_collection(engine: SimulatorEngine = Depends(_engine)) -> dict:
    members = [{"@odata.id": f"/redfish/v1/Chassis/{aid}"} for aid in engine.asset_ids]
    return {
        "@odata.type": "#ChassisCollection.ChassisCollection",
        "@odata.id": "/redfish/v1/Chassis",
        "Name": "Chassis Collection",
        "Members": members,
        "Members@odata.count": len(members),
    }


@router.get("/redfish/v1/Chassis/{asset_id}")
async def get_chassis(asset_id: str, engine: SimulatorEngine = Depends(_engine)) -> dict:
    _require_asset(asset_id, engine)
    fault = engine.get_fault(asset_id)
    health = "Critical" if fault and fault.fault_type == "psu_failure" else "OK"
    return {
        "@odata.type": "#Chassis.v1_16_0.Chassis",
        "@odata.id": f"/redfish/v1/Chassis/{asset_id}",
        "Id": asset_id,
        "Name": asset_id,
        "ChassisType": "RackMount",
        "Status": {"State": "Enabled", "Health": health},
        "Power": {"@odata.id": f"/redfish/v1/Chassis/{asset_id}/Power"},
    }


@router.get("/redfish/v1/Chassis/{asset_id}/Power")
async def get_power(asset_id: str, engine: SimulatorEngine = Depends(_engine)) -> dict:
    _require_asset(asset_id, engine)
    fault = engine.get_fault(asset_id)
    psus = _build_psu_members(asset_id, fault)
    return {
        "@odata.type": "#Power.v1_7_0.Power",
        "@odata.id": f"/redfish/v1/Chassis/{asset_id}/Power",
        "Id": "Power",
        "Name": "Power",
        "PowerSupplies": psus,
    }


def _build_psu_members(asset_id: str, fault: InjectedFault | None) -> list[dict]:
    psu_names = ["PSU 1", "PSU 2", "PSU 3", "PSU 4"]
    psu_list = []
    for idx, name in enumerate(psu_names):
        # PSU 2 (index 1) fails on a psu_failure fault
        failed = fault is not None and fault.fault_type == "psu_failure" and idx == 1
        psu_list.append({
            "@odata.id": f"/redfish/v1/Chassis/{asset_id}/Power#/PowerSupplies/{idx}",
            "MemberId": str(idx),
            "Name": name,
            "PowerSupplyType": "AC",
            "Status": {
                "State": "Absent" if failed else "Enabled",
                "Health": "Critical" if failed else "OK",
            },
        })
    return psu_list


# ──────────────────────────────────────────────────────────────────────────────
# EventService  (all active fault events across all assets)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/redfish/v1/EventService")
async def event_service() -> dict:
    return {
        "@odata.type": "#EventService.v1_7_0.EventService",
        "@odata.id": "/redfish/v1/EventService",
        "Id": "EventService",
        "Name": "Event Service",
        "Status": {"State": "Enabled", "Health": "OK"},
        "Events": {"@odata.id": "/redfish/v1/EventService/Events"},
    }


@router.get("/redfish/v1/EventService/Events")
async def list_events(engine: SimulatorEngine = Depends(_engine)) -> dict:
    events = []
    for asset_id in engine.asset_ids:
        fault = engine.get_fault(asset_id)
        if fault:
            events.append(_build_event(asset_id, fault))
    return {
        "@odata.type": "#EventCollection.EventCollection",
        "@odata.id": "/redfish/v1/EventService/Events",
        "Name": "Event Collection",
        "Members": events,
        "Members@odata.count": len(events),
    }


def _build_event(asset_id: str, fault: InjectedFault) -> dict:
    return {
        "@odata.type": "#Event.v1_7_0.Event",
        "@odata.id": f"/redfish/v1/EventService/Events/{asset_id}",
        "Id": asset_id,
        "Name": "Alert Event",
        "Events": [
            {
                "EventType": "Alert",
                "EventTimestamp": fault.injected_at.isoformat(),
                "Severity": fault.event_severity,
                "MessageId": fault.event_message_id,
                "Message": fault.log_entries[0][1] if fault.log_entries else "",
                "MemberId": asset_id,
                "OriginOfCondition": {"@odata.id": f"/redfish/v1/Systems/{asset_id}"},
            }
        ],
    }
