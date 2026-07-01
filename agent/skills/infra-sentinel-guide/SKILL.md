---
name: "infra-sentinel-guide"
description: "Orientation skill for the NemoClaw AI Infrastructure Sentinel. Lists all available skills and maps them to the detect→diagnose→approve→remediate workflow. Use when starting a monitoring session or to understand which skill to invoke for a given situation."
license: "Apache-2.0"
---

# AI Infrastructure Sentinel — Skills Guide

The NemoClaw AI Infrastructure Sentinel runs a continuous detect→diagnose→approve→remediate cycle over a DELL PowerEdge XE9780L GPU cluster.

Load this guide first. Then load the specific skill for the phase you are in.

## Workflow Overview

```
monitor  →  diagnose  →  [human approval]  →  remediate  →  notify
```

Each phase is a separate skill. Skills are cumulative: remediation depends on a diagnosis, which depends on monitoring.

## Skill Catalog

| Skill | Phase | When to use |
|-------|-------|-------------|
| `infra-sentinel-monitor` | Detect | Poll Redfish for active hardware fault events |
| `infra-sentinel-diagnose` | Diagnose | Fetch logs, extract error signature, search KB |
| `infra-sentinel-remediate` | Remediate | Execute operator-approved remediation steps |
| `infra-sentinel-notify` | Notify | Post structured activity updates to the operator dashboard |

## MCP Tools Available

Connect to the MCP tools server at `http://mcp-tools:8004/mcp` (within the Docker network) or configure as an MCP server in `openclaw.json`.

| Tool | Description |
|------|-------------|
| `monitor_list_events` | Returns active fault events from the Redfish simulator |
| `logs_get_bundle` | Returns log bundle (iDRAC lifecycle log text + scenario_id) for an asset |
| `kb_search` | FAISS semantic search over the infrastructure KB; returns kb_id, score, via |
| `remediation_execute` | Executes approved steps with a single-use approval token |

## Gateway API

The gateway runs at `http://gateway:8001` within the Docker network.

| Endpoint | Purpose |
|----------|---------|
| `POST /api/faults` | Register a new fault event |
| `PATCH /api/faults/{id}/status` | Update fault status (diagnosing, awaiting_approval, resolved, denied) |
| `GET /api/faults/{id}/token` | Poll for human approval token |
| `POST /api/agent/activity` | Post a structured activity update to the dashboard feed |

## Getting Started

1. Load `infra-sentinel-monitor` and poll for events.
2. If an event is found, load `infra-sentinel-diagnose`.
3. After diagnosis, post the approval request via `infra-sentinel-notify`.
4. Poll for the approval token.
5. Once approved, load `infra-sentinel-remediate`.
6. Confirm resolution and post final status via `infra-sentinel-notify`.
