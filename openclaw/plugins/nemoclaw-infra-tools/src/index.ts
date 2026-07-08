/**
 * nemoclaw-infra-tools — OpenClaw plugin exposing the NemoClaw lab's seven
 * LLM-facing MCP tools as native OpenClaw tools (ADR-011).
 *
 * The plugin is an MCP client (Streamable HTTP) against the existing,
 * unmodified services/mcp_tools server, so tool behaviour stays
 * single-sourced there. remediation.execute is never registered here — see
 * src/mcp.ts for the three-layer guard. Deterministic harness side-work
 * (fault registration, narration, diagnosis persistence) is ported from
 * agent/loop.py into the tool handlers — see src/harness.ts.
 */

import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import {
  alreadyProposedResult,
  clearStaleInvestigation,
  currentInvestigation,
  markProposed,
  noRegisteredFaultError,
  postActivity,
  recordKbResult,
  recordSignature,
  registerFaultFromLogs,
} from "./harness.js";
import { callMcpTool, type ExposedMcpTool } from "./mcp.js";

const DEFAULT_MCP_URL = "http://host.openshell.internal:8004/mcp";
const DEFAULT_GATEWAY_URL = "http://host.openshell.internal:8001";

type PluginConfig = {
  mcpUrl?: string;
  gatewayUrl?: string;
};

function mcpUrl(config: PluginConfig): string {
  return config.mcpUrl || process.env.NEMOCLAW_INFRA_MCP_URL || DEFAULT_MCP_URL;
}

function gatewayUrl(config: PluginConfig): string {
  return config.gatewayUrl || process.env.NEMOCLAW_INFRA_GATEWAY_URL || DEFAULT_GATEWAY_URL;
}

async function call(
  config: PluginConfig,
  name: ExposedMcpTool,
  args: Record<string, unknown>,
): Promise<unknown> {
  const raw = await callMcpTool(mcpUrl(config), name, args);
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export default defineToolPlugin({
  id: "nemoclaw-infra-tools",
  name: "NemoClaw Infra Tools",
  description:
    "Infrastructure sentinel tools for the NemoClaw lab: Redfish monitoring, " +
    "iDRAC log retrieval, KB search, operator narration, and HITL-gated " +
    "remediation proposals.",
  activation: { onStartup: true },
  configSchema: Type.Object({
    mcpUrl: Type.Optional(
      Type.String({ description: "Streamable HTTP URL of the mcp-tools service." }),
    ),
    gatewayUrl: Type.Optional(
      Type.String({ description: "Base URL of the lab Gateway API." }),
    ),
  }),
  tools: (tool) => [
    tool({
      name: "monitor_list_events",
      label: "List Fault Events",
      description:
        "List active fault events from the monitoring surface. " +
        "Optionally filter to one asset.",
      parameters: Type.Object({
        asset_id: Type.Optional(
          Type.String({ description: "Filter to a specific asset. Omit for all assets." }),
        ),
      }),
      async execute({ asset_id }, config) {
        // Every wake-up starts here (AGENTS.md) — clear any investigation the
        // Gateway already resolved/denied before the model can narrate more
        // about it or trigger a fresh diagnosis pass.
        await clearStaleInvestigation(gatewayUrl(config));
        return call(config, "monitor.list_events", { asset_id: asset_id ?? "" });
      },
    }),
    tool({
      name: "monitor_get_asset",
      label: "Get Asset Health",
      description: "Inspect health state for a single asset.",
      parameters: Type.Object({
        asset_id: Type.String({ description: "The asset id (e.g. 'gpu-server-01')." }),
      }),
      async execute({ asset_id }, config) {
        return call(config, "monitor.get_asset", { asset_id });
      },
    }),
    tool({
      name: "monitor_list_assets",
      label: "List Assets",
      description: "List all assets in the pack with their current health state.",
      parameters: Type.Object({}),
      async execute(_params, config) {
        return call(config, "monitor.list_assets", {});
      },
    }),
    tool({
      name: "logs_get_bundle",
      label: "Get Log Bundle",
      description:
        "Fetch the scenario log bundle for an asset. Returns log text plus " +
        "the fault_event_id issued for this investigation.",
      parameters: Type.Object({
        asset_id: Type.String({ description: "The asset whose log bundle to retrieve." }),
      }),
      async execute({ asset_id }, config) {
        const result = await call(config, "logs.get_bundle", { asset_id });
        if (result && typeof result === "object" && !Array.isArray(result)) {
          return registerFaultFromLogs(gatewayUrl(config), result as Record<string, unknown>);
        }
        return result;
      },
    }),
    tool({
      name: "kb_search",
      label: "Search Knowledge Base",
      description:
        "Search the knowledge base for an error signature. Returns the " +
        "best-matching article (kb_id, title, body_md, remediation_step_ids, " +
        "score, via).",
      parameters: Type.Object({
        signature: Type.String({
          description: "Error signature or log extract from the fault logs.",
        }),
        fallback_kb_id: Type.Optional(
          Type.String({ description: "KB article id to use if search finds nothing." }),
        ),
      }),
      async execute({ signature, fallback_kb_id }, config) {
        const gw = gatewayUrl(config);
        const inv = currentInvestigation();
        // Persist the extracted signature *before* the search runs — the
        // Operator Dashboard's ERROR SIGNATURE card populates first, while
        // KNOWLEDGE BASE still shows "Searching…" (staged population order).
        await recordSignature(gw, signature);
        // Only narrate the search itself the first time — a repeat wake-up
        // that re-runs kb_search for an already-diagnosed fault shouldn't
        // spam the same "performing search" / "matched KBxxx" lines.
        if (inv && !inv.diagnosed) {
          await postActivity(gw, inv.faultId, "search_kb",
            `Performing local KB semantic search (FAISS): "${signature}"…`);
        }
        const result = await call(config, "kb.search", {
          signature,
          fallback_kb_id: fallback_kb_id ?? "",
        });
        const kb =
          result && typeof result === "object" && !Array.isArray(result)
            ? (result as Record<string, unknown>)
            : null;
        await recordKbResult(gw, signature, kb);
        return result;
      },
    }),
    tool({
      name: "notify_post_activity",
      label: "Post Operator Update",
      description:
        "Post a plain-language progress update to the operator dashboard " +
        "activity feed. Display-only narration; cannot change fault status " +
        "or infrastructure state.",
      parameters: Type.Object({
        fault_event_id: Type.String({
          description: "The FaultEvent id this update relates to.",
        }),
        step: Type.String({
          description: "Feed category: detect | diagnose | search_kb | present.",
        }),
        message: Type.String({
          description: "Plain-language description (keep it under 120 characters).",
        }),
      }),
      async execute({ step, message }, config) {
        const inv = currentInvestigation();
        if (inv === null) return noRegisteredFaultError();
        // The LLM cannot address a different fault than the one under investigation.
        return call(config, "notify.post_activity", {
          fault_event_id: inv.faultId,
          step,
          message,
        });
      },
    }),
    tool({
      name: "remediation_propose",
      label: "Propose Remediation",
      description:
        "Record your recommended remediation steps and request operator " +
        "approval. A proposal, not an action: changes no infrastructure " +
        "state and returns no token. Call once, then stop calling tools " +
        "and wait.",
      parameters: Type.Object({
        fault_event_id: Type.String({
          description: "The FaultEvent id being diagnosed.",
        }),
        step_ids: Type.Array(Type.String(), {
          description: "Ordered remediation step ids from the matched KB article.",
        }),
        summary: Type.Optional(
          Type.String({
            description:
              "2-3 sentence plain-language diagnosis summary for the operator: " +
              "what is wrong, the likely cause, and the operational risk.",
          }),
        ),
      }),
      async execute({ step_ids, summary }, config) {
        const inv = currentInvestigation();
        if (inv === null) return noRegisteredFaultError();
        // A repeat wake-up must not re-propose (and re-PATCH analysis, and
        // re-post "presenting…") for a fault already awaiting a decision.
        if (inv.proposed) return alreadyProposedResult();
        await postActivity(gatewayUrl(config), inv.faultId, "present",
          "Presenting problem details, impact assessment and KB remediation steps to the operator…");
        const result = await call(config, "remediation.propose", {
          fault_event_id: inv.faultId,
          step_ids,
          summary: summary ?? "",
        });
        const ok =
          result === null ||
          typeof result !== "object" ||
          (result as { status?: unknown }).status !== "error";
        if (ok) markProposed();
        return result;
      },
    }),
  ],
});
