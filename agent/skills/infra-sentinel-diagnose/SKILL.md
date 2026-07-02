---
name: "infra-sentinel-diagnose"
description: "Turns a raw fault event into a diagnosis: retrieve the log bundle, identify the error signature through your own reasoning, and search the knowledge base for a matching remediation reference. Use once infra-sentinel-monitor has surfaced a fault you're investigating."
license: "Apache-2.0"
---

# Skill: Fault Diagnosis

## Purpose

Turn a raw fault event into something an operator can act on: a clear statement of what's wrong, evidence for it, and a knowledge-base reference for how it's normally fixed.

## Using `logs_get_bundle`

Call with the `asset_id` from the fault event. It returns the raw log text and a `scenario_id` (used internally for fallback matching — you don't need to do anything with it directly).

Read the logs before concluding anything. If the bundle is empty or clearly unrelated to a hardware fault, say so and don't force a diagnosis.

## Identifying the error signature

This is your own reasoning, not a separate tool call. Read the log text and identify the primary error signature — a short, canonical phrase that names the fault class (e.g. "Xid 79", "ECC uncorrectable error", "PSU 2 input lost"). State it plainly before moving on; don't bury it in a paragraph.

## Using `kb_search`

Call with the error signature you identified. It searches the knowledge base and returns a matched article id, a confidence score, and how the match was found (semantic search or fallback). You don't need to canonicalize the signature yourself first — the tool handles fuzzy matching against known fault classes internally.

- High-confidence match: use its remediation steps as your basis.
- No match or low confidence: say so, and fall back to the scenario's default remediation steps if you have them; don't fabricate a procedure that isn't grounded in either source.

## What a good diagnosis includes

By the time you're ready to hand this to an operator (via `infra-sentinel-remediate`), you should be able to state, in plain language: what you observed and from which source, the error signature, which KB article (if any) backs your recommendation and how confident you are in it, and what you intend to propose.

## Judgment notes

- Don't call `kb_search` before you have a signature — a search without one wastes a turn and won't return anything meaningful.
- If your own reasoning here feels uncertain, say so explicitly rather than presenting a guess as settled fact — see the Reasoning Style section of `soul.md`.
