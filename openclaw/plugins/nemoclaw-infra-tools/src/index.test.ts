import { beforeEach, describe, expect, it, vi } from "vitest";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import plugin from "./index.js";
import {
  registerFaultFromLogs,
  recordKbResult,
  recordSignature,
  resetInvestigation,
  currentInvestigation,
  noRegisteredFaultError,
  alreadyProposedResult,
  markProposed,
  clearStaleInvestigation,
} from "./harness.js";
import {
  assertAllowlistExcludesExecute,
  assertServerContract,
  LLM_EXPOSED_TOOLS,
  toFunctionName,
} from "./mcp.js";

const EXPECTED_TOOLS = [
  "monitor_list_events",
  "monitor_get_asset",
  "monitor_list_assets",
  "logs_get_bundle",
  "kb_search",
  "notify_post_activity",
  "remediation_propose",
];

describe("plugin metadata", () => {
  const metadata = getToolPluginMetadata(plugin);

  it("registers exactly the seven allowlisted tools", () => {
    expect(metadata).toBeDefined();
    expect(metadata!.tools.map((t) => t.name).sort()).toEqual([...EXPECTED_TOOLS].sort());
  });

  it("never registers remediation_execute", () => {
    const names = metadata!.tools.map((t) => t.name);
    expect(names).not.toContain("remediation_execute");
    expect(names.some((n) => n.toLowerCase().includes("execute"))).toBe(false);
  });

  it("mirrors the MCP allowlist one-to-one", () => {
    const fromAllowlist = LLM_EXPOSED_TOOLS.map(toFunctionName).sort();
    expect(metadata!.tools.map((t) => t.name).sort()).toEqual(fromAllowlist);
  });

  it("activates on gateway startup", () => {
    expect(metadata!.activation.onStartup).toBe(true);
  });
});

describe("execute-tool guards", () => {
  it("rejects an allowlist containing the dotted execute name", () => {
    expect(() => assertAllowlistExcludesExecute(["remediation.execute"])).toThrow(
      /must never be exposed/,
    );
  });

  it("rejects an allowlist containing the underscored execute name", () => {
    expect(() => assertAllowlistExcludesExecute(["remediation_execute"])).toThrow(
      /must never be exposed/,
    );
  });

  it("accepts the server listing remediation.execute for the Gateway's trusted path", () => {
    // The server legitimately lists it; it must simply never be dispatchable
    // through this plugin.
    expect(() =>
      assertServerContract([...LLM_EXPOSED_TOOLS, "remediation.execute"]),
    ).not.toThrow();
  });

  it("hard-fails when the server is missing an allowlisted tool", () => {
    expect(() => assertServerContract(["monitor.list_events"])).toThrow(/missing expected tools/);
  });
});

describe("harness side-work", () => {
  const gatewayUrl = "http://gateway.test";
  let calls: Array<{ method: string; url: string; body: unknown }>;

  beforeEach(() => {
    resetInvestigation();
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(init.body as string) : undefined;
        calls.push({ method: init?.method ?? "GET", url: String(url), body });
        if (String(url).endsWith("/api/faults") && init?.method === "POST") {
          return new Response(JSON.stringify({ id: "fault-123" }), { status: 201 });
        }
        return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
      }),
    );
  });

  it("registers the fault once and stamps fault_event_id into the bundle", async () => {
    const bundle = { log_text: "line1\nline2", scenario_id: "scn-1", asset_id: "gpu-01" };
    const first = await registerFaultFromLogs(gatewayUrl, bundle);
    expect(first.fault_event_id).toBe("fault-123");
    expect(currentInvestigation()?.faultId).toBe("fault-123");

    const posts = calls.filter((c) => c.url.endsWith("/api/faults") && c.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0].body.log_extract).toBe("line1\nline2");

    const statusPatch = calls.find((c) => c.url.includes("/status"));
    expect(statusPatch?.body).toEqual({ status: "diagnosing" });
  });

  it("uses the Orchestrator's log_highlight as log_extract when present (Operator Dashboard evidence)", async () => {
    const bundle = {
      log_text: "# comment\nlots of routine INFO chatter that isn't the fault\nline2",
      log_highlight: "2026-07-07T09:23:40Z CRIT kernel: Xid 79 fault on gpu-01",
      scenario_id: "scn-1",
      asset_id: "gpu-01",
    };
    await registerFaultFromLogs(gatewayUrl, bundle);

    const posts = calls.filter((c) => c.url.endsWith("/api/faults") && c.method === "POST");
    expect(posts[0].body.log_extract).toBe(
      "2026-07-07T09:23:40Z CRIT kernel: Xid 79 fault on gpu-01",
    );
  });

  it("falls back to slicing log_text when log_highlight is absent", async () => {
    const bundle = {
      log_text: "line1\nline2\nline3",
      scenario_id: "scn-1",
      asset_id: "gpu-01",
    };
    await registerFaultFromLogs(gatewayUrl, bundle);

    const posts = calls.filter((c) => c.url.endsWith("/api/faults") && c.method === "POST");
    expect(posts[0].body.log_extract).toBe("line1\nline2\nline3");
  });

  it("passes error payloads through without registering", async () => {
    const result = await registerFaultFromLogs(gatewayUrl, { status: "error", error: "http_404" });
    expect(result.fault_event_id).toBeUndefined();
    expect(currentInvestigation()).toBeNull();
  });

  it("persists the diagnosis after a KB match", async () => {
    await registerFaultFromLogs(gatewayUrl, {
      log_text: "x", scenario_id: "scn-1", asset_id: "gpu-01",
    });
    await recordKbResult(gatewayUrl, "Xid 79", {
      kb_id: "KB000123", title: "GPU Xid 79", score: 0.92, via: "faiss",
    });
    const diag = calls.find((c) => c.url.includes("/diagnosis"));
    expect(diag?.body).toMatchObject({
      error_signature: "Xid 79",
      kb_article_id: "KB000123",
      kb_score: 0.92,
    });
  });

  it("persists the signature before the KB match as its own PATCH (staged dashboard order)", async () => {
    await registerFaultFromLogs(gatewayUrl, {
      log_text: "x", scenario_id: "scn-1", asset_id: "gpu-01",
    });
    await recordSignature(gatewayUrl, "Xid 79");
    expect(currentInvestigation()?.signatureRecorded).toBe(true);

    let diagPatches = calls.filter((c) => c.url.includes("/diagnosis"));
    expect(diagPatches).toHaveLength(1);
    expect(diagPatches[0].body).toEqual({ error_signature: "Xid 79" });

    await recordKbResult(gatewayUrl, "Xid 79", {
      kb_id: "KB000123", title: "GPU Xid 79", score: 0.92, via: "faiss",
    });
    diagPatches = calls.filter((c) => c.url.includes("/diagnosis"));
    expect(diagPatches).toHaveLength(2);
    // The KB PATCH must not re-send the signature — it landed in PATCH #1.
    expect(diagPatches[1].body).toEqual({
      kb_article_id: "KB000123",
      kb_title: "GPU Xid 79",
      kb_score: 0.92,
    });
  });

  it("recordSignature is idempotent and a no-op before evidence exists", async () => {
    await recordSignature(gatewayUrl, "Xid 79"); // no investigation yet
    expect(calls.filter((c) => c.url.includes("/diagnosis"))).toHaveLength(0);

    await registerFaultFromLogs(gatewayUrl, {
      log_text: "x", scenario_id: "scn-1", asset_id: "gpu-01",
    });
    await recordSignature(gatewayUrl, "Xid 79");
    await recordSignature(gatewayUrl, "Xid 79"); // repeat wake-up
    expect(calls.filter((c) => c.url.includes("/diagnosis"))).toHaveLength(1);
  });

  it("refuses narration before evidence exists", () => {
    expect(noRegisteredFaultError()).toMatchObject({ error: "no_registered_fault" });
  });

  it("does not re-register or re-narrate detect phase on a repeat wake-up for the same open investigation", async () => {
    const bundle = { log_text: "line1\nline2", scenario_id: "scn-1", asset_id: "gpu-01" };
    const first = await registerFaultFromLogs(gatewayUrl, bundle);
    expect(first.investigation_stage).toBe("new");

    calls = []; // only inspect what the second call does
    const second = await registerFaultFromLogs(gatewayUrl, bundle);
    expect(second.fault_event_id).toBe("fault-123");
    expect(second.investigation_stage).toBe("new"); // still undiagnosed, still unproposed

    const posts = calls.filter((c) => c.url.endsWith("/api/faults") && c.method === "POST");
    expect(posts).toHaveLength(0); // no duplicate fault registration
    const detectActivity = calls.filter((c) => c.url.endsWith("/api/agent/activity"));
    expect(detectActivity).toHaveLength(0); // no repeat detect/diagnose narration
  });

  it("does not re-patch diagnosis or re-narrate the KB match on a repeat recordKbResult call", async () => {
    await registerFaultFromLogs(gatewayUrl, {
      log_text: "x", scenario_id: "scn-1", asset_id: "gpu-01",
    });
    const kb = { kb_id: "KB000123", title: "GPU Xid 79", score: 0.92, via: "faiss" };
    await recordKbResult(gatewayUrl, "Xid 79", kb);
    expect(currentInvestigation()?.diagnosed).toBe(true);

    calls = [];
    await recordKbResult(gatewayUrl, "Xid 79", kb);
    const diagPatches = calls.filter((c) => c.url.includes("/diagnosis"));
    const searchNarration = calls.filter((c) => c.url.endsWith("/api/agent/activity"));
    expect(diagPatches).toHaveLength(0);
    expect(searchNarration).toHaveLength(0);
  });

  it("posts a waiting status and reports awaiting_operator_decision on repeat wake-up after proposal", async () => {
    const bundle = { log_text: "x", scenario_id: "scn-1", asset_id: "gpu-01" };
    await registerFaultFromLogs(gatewayUrl, bundle);
    await recordKbResult(gatewayUrl, "Xid 79", { kb_id: "KB000123", title: "t", score: 0.9, via: "faiss" });
    markProposed();
    expect(currentInvestigation()?.proposed).toBe(true);

    calls = [];
    const woken = await registerFaultFromLogs(gatewayUrl, bundle);
    expect(woken.investigation_stage).toBe("awaiting_operator_decision");

    const waitingPosts = calls.filter(
      (c) => c.url.endsWith("/api/agent/activity") && c.body?.step === "waiting",
    );
    expect(waitingPosts).toHaveLength(1);
    const posts = calls.filter((c) => c.url.endsWith("/api/faults") && c.method === "POST");
    expect(posts).toHaveLength(0); // still the same fault, not re-registered
  });

  it("alreadyProposedResult tells the model to stop instead of proposing again", () => {
    expect(alreadyProposedResult()).toMatchObject({ status: "already_proposed" });
  });

  it("clearStaleInvestigation drops an investigation the Gateway already resolved", async () => {
    await registerFaultFromLogs(gatewayUrl, {
      log_text: "x", scenario_id: "scn-1", asset_id: "gpu-01",
    });
    expect(currentInvestigation()?.faultId).toBe("fault-123");

    // Override the mock: this fault now reports resolved.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ method: init?.method ?? "GET", url: String(url), body: undefined });
        if (String(url).endsWith("/api/faults/fault-123") && (!init?.method || init.method === "GET")) {
          return new Response(JSON.stringify({ status: "resolved" }), { status: 200 });
        }
        return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
      }),
    );

    await clearStaleInvestigation(gatewayUrl);
    expect(currentInvestigation()).toBeNull();
  });

  it("clearStaleInvestigation leaves an active investigation alone", async () => {
    await registerFaultFromLogs(gatewayUrl, {
      log_text: "x", scenario_id: "scn-1", asset_id: "gpu-01",
    });
    await clearStaleInvestigation(gatewayUrl); // mock still reports {status:"ok"} — not terminal
    expect(currentInvestigation()?.faultId).toBe("fault-123");
  });
});
