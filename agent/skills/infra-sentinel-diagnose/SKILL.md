---
name: "infra-sentinel-diagnose"
description: "Diagnoses a detected hardware fault by fetching the iDRAC lifecycle log bundle, extracting the error signature using LLM reasoning, and searching the infrastructure knowledge base for a matching remediation procedure. Produces a structured fault diagnosis report ready for operator approval. Use after infra-sentinel-monitor returns a fault event."
license: "Apache-2.0"
---

# Skill: Fault Diagnosis

## Purpose

Turn a raw Redfish fault event into a structured diagnosis: canonical error signature, KB article reference, and ranked remediation steps — ready for human review and approval.

## Prerequisites

- `asset_id` from `infra-sentinel-monitor`
- A fault event has been detected but not yet registered in the gateway

## Steps

### 1. Fetch Log Bundle

Call `logs_get_bundle(asset_id)`.

Extract:
- `log_text` — the raw iDRAC lifecycle log
- `scenario_id` — the fault scenario identifier (used for KB lookup fallback)

Post activity: `"Querying iDRAC Lifecycle Log for <asset_id>…"` (step: `detect`)
Post activity: `"Log bundle retrieved — <N> entries collected"` (step: `detect`)

### 2. Register Fault Event

`POST /api/faults` with:
```json
{
  "scenario_id": "<scenario_id>",
  "asset_id": "<asset_id>",
  "log_extract": "<first 300 chars of log_text>"
}
```

Store the returned `fault_id` — all subsequent status updates and activity posts use this ID.

### 3. Extract Error Signature (LLM)

Post activity: `"Parsing log entries and isolating critical-severity events…"` (step: `diagnose`)
Post activity: `"Sending log extract to Qwen 3.6 35B for error signature analysis…"` (step: `diagnose`)

Send the first 2000 characters of `log_text` to the LLM with this system prompt:

> You are an infrastructure monitoring agent. Analyse log text and extract the primary error signature — a short, canonical phrase that identifies the fault class (e.g. 'Xid 79', 'ECC uncorrectable error', 'PSU 2 input lost'). Return ONLY the signature string, nothing else. Do not explain or rephrase.

The model uses chain-of-thought reasoning. Wait for `content` in the response; if `content` is null, fall back to `reasoning_content`.

Post activity: `"LLM identified error signature: \"<signature>\""` (step: `diagnose`)

### 4. Snap to Canonical Signature

The raw LLM output may be verbose. Map it to a known canonical signature from the `signature_index` (a list of known fault classes). Use substring matching (case-insensitive).

If no match is found, use the first `error_signature` from the scenario definition, or the raw LLM output as a last resort.

Post activity: `"Canonical signature confirmed via snap-to-known index"` (step: `diagnose`)

### 5. Search Knowledge Base

Call `kb_search(canonical_signature)`.

The tool returns:
```json
{
  "kb_id": "<article-id>",
  "score": 0.92,
  "via": "faiss | fallback | exact"
}
```

Post activity: `"Querying local KB with FAISS semantic search: \"<signature>\"…"` (step: `search_kb`)

If a match is found:
Post activity: `"✓ KB article matched: <kb_id> (confidence <score%>, via <via>)"` (step: `search_kb`)
Post activity: `"Reading remediation procedure from <kb_id>…"` (step: `search_kb`)

If no match:
Post activity: `"No KB article matched — falling back to scenario defaults"` (step: `search_kb`)

### 6. Update Fault Status to Diagnosing

`PATCH /api/faults/<fault_id>/status` with `{ "status": "diagnosing" }`

Post activity: `"Composing fault diagnosis report…"` (step: `diagnose`)

## Output

```json
{
  "fault_id": "<string>",
  "asset_id": "<string>",
  "canonical_signature": "<string>",
  "kb_id": "<string | null>",
  "kb_score": 0.92,
  "scenario_id": "<string>",
  "remediation_steps": ["step-01", "step-02", "step-03"]
}
```

Pass to `infra-sentinel-notify` to request operator approval.

## Notes

- Always register the fault event in the gateway before posting activity updates (the gateway links activity to the fault_id).
- The LLM uses chain-of-thought reasoning (max_tokens: 2048). With thinking enabled, the model may take a few seconds longer but will produce a more reliable signature extraction.
- If `log_text` is empty, abort and return `{ "status": "no_data" }`.
