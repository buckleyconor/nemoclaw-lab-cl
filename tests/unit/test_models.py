"""U-01, U-02 — Pydantic model validation for Scenario."""

import pytest
from pydantic import ValidationError

from libs.common.models import Scenario

VALID_SCENARIO_DICT = {
    "id": "scn-gpu-xid-79",
    "pack_id": "datacenter-xe9680",
    "target_asset": "gpu-server-02",
    "fault_type": "gpu_xid",
    "emit": {
        "event": {"severity": "Critical", "message_id": "OEM.1.0.GPUFault"},
        "log_entries": [
            {"severity": "Critical", "message": "GPU0: Xid 79: GPU has fallen off the bus"}
        ],
    },
    "log_bundle_ref": "bundles/scn-gpu-xid-79.log",
    "error_signatures": ["Xid 79", "GPU has fallen off the bus"],
    "kb_article_ref": "kb/KB000123.md",
    "remediation_steps": [
        {"id": "drain_node", "label": "Cordon and drain affected node"},
        {"id": "gpu_reset", "label": "Issue GPU reset via iDRAC"},
        {"id": "verify_health", "label": "Re-run DCGM health check"},
    ],
}


# U-01 — valid scenario YAML loads into Scenario model without errors
def test_valid_scenario_loads(tmp_path) -> None:
    scenario = Scenario.model_validate(VALID_SCENARIO_DICT)
    assert scenario.id == "scn-gpu-xid-79"
    assert scenario.pack_id == "datacenter-xe9680"
    assert scenario.fault_type == "gpu_xid"
    assert "Xid 79" in scenario.error_signatures
    assert len(scenario.remediation_steps) == 3
    assert scenario.remediation_steps[0].id == "drain_node"


# U-01 — emit log_entries are parsed correctly
def test_scenario_emit_parsed() -> None:
    scenario = Scenario.model_validate(VALID_SCENARIO_DICT)
    assert scenario.emit.event.severity == "Critical"
    assert scenario.emit.event.message_id == "OEM.1.0.GPUFault"
    assert len(scenario.emit.log_entries) == 1
    assert "Xid 79" in scenario.emit.log_entries[0].message


# U-01 — scenario_status defaults to "active"
def test_scenario_default_status() -> None:
    scenario = Scenario.model_validate(VALID_SCENARIO_DICT)
    assert scenario.status == "active"


# U-01 — scaffold status is accepted
def test_scenario_scaffold_status() -> None:
    d = {**VALID_SCENARIO_DICT, "status": "scaffold"}
    scenario = Scenario.model_validate(d)
    assert scenario.status == "scaffold"


# U-02 — scenario missing kb_article_ref raises ValidationError naming the field
def test_scenario_missing_kb_article_ref() -> None:
    d = {k: v for k, v in VALID_SCENARIO_DICT.items() if k != "kb_article_ref"}
    with pytest.raises(ValidationError) as exc_info:
        Scenario.model_validate(d)
    errors = exc_info.value.errors()
    field_names = [e["loc"][0] for e in errors]
    assert "kb_article_ref" in field_names


# U-02 (edge) — scenario missing error_signatures raises ValidationError
def test_scenario_missing_signatures() -> None:
    d = {k: v for k, v in VALID_SCENARIO_DICT.items() if k != "error_signatures"}
    with pytest.raises(ValidationError):
        Scenario.model_validate(d)


# U-02 (edge) — empty error_signatures list raises ValidationError (min_length=1)
def test_scenario_empty_signatures() -> None:
    d = {**VALID_SCENARIO_DICT, "error_signatures": []}
    with pytest.raises(ValidationError):
        Scenario.model_validate(d)


# U-02 (edge) — invalid scenario_status value raises ValidationError
def test_scenario_invalid_status() -> None:
    d = {**VALID_SCENARIO_DICT, "status": "unknown"}
    with pytest.raises(ValidationError):
        Scenario.model_validate(d)
