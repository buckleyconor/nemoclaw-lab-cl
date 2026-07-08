/**
 * MCP client bridge — Streamable HTTP to the lab's mcp-tools service.
 *
 * The plugin registers exactly LLM_EXPOSED_TOOLS and nothing else.
 * remediation.execute is deliberately absent: it is not in the allowlist,
 * not in the dispatch table index.ts builds from the allowlist, and
 * assertServerContract() hard-fails the first call if the dispatchable
 * surface could ever include it (ADR-011, mirroring ADR-010's three-layer
 * defense in agent/tools.py + agent/loop.py).
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

/** Dotted MCP tool names the LLM may see and call. */
export const LLM_EXPOSED_TOOLS = [
  "monitor.list_events",
  "monitor.get_asset",
  "monitor.list_assets",
  "logs.get_bundle",
  "kb.search",
  "notify.post_activity",
  "remediation.propose",
] as const;

export type ExposedMcpTool = (typeof LLM_EXPOSED_TOOLS)[number];

export const FORBIDDEN_TOOL = "remediation.execute";

/** MCP dotted name → OpenClaw tool name ([a-zA-Z0-9_-] only). */
export function toFunctionName(mcpName: string): string {
  return mcpName.replaceAll(".", "_");
}

/**
 * Layer-1 guard, evaluated at module load: the allowlist itself must never
 * contain the execute tool. Protects against a future edit to
 * LLM_EXPOSED_TOOLS re-introducing it.
 */
export function assertAllowlistExcludesExecute(allowlist: readonly string[]): void {
  for (const name of allowlist) {
    if (name === FORBIDDEN_TOOL || toFunctionName(name) === toFunctionName(FORBIDDEN_TOOL)) {
      throw new Error(
        `nemoclaw-infra-tools: ${FORBIDDEN_TOOL} must never be exposed to the LLM ` +
          "(ADR-004/ADR-011 HITL invariant)",
      );
    }
  }
}

assertAllowlistExcludesExecute(LLM_EXPOSED_TOOLS);

/**
 * Layer-3 guard, evaluated on first connect: every allowlisted tool must
 * exist on the server, and the dispatchable surface (server listing ∩
 * allowlist) must not contain the execute tool. The server legitimately
 * lists remediation.execute for the Gateway's trusted post-approval path —
 * what must never happen is that name becoming dispatchable through us.
 */
export function assertServerContract(serverToolNames: string[]): void {
  const listed = new Set(serverToolNames);
  const missing = LLM_EXPOSED_TOOLS.filter((name) => !listed.has(name));
  if (missing.length > 0) {
    throw new Error(
      `nemoclaw-infra-tools: MCP server is missing expected tools: ${missing.join(", ")}`,
    );
  }
  const dispatchable = serverToolNames.filter((name) =>
    (LLM_EXPOSED_TOOLS as readonly string[]).includes(name),
  );
  assertAllowlistExcludesExecute(dispatchable);
}

let contractVerified = false;

/** Open a session, run one call, close. Sessions are cheap at demo cadence
 * and per-call connections avoid stale-session handling entirely. */
export async function callMcpTool(
  mcpUrl: string,
  name: ExposedMcpTool,
  args: Record<string, unknown>,
): Promise<string> {
  const transport = new StreamableHTTPClientTransport(new URL(mcpUrl));
  const client = new Client({ name: "nemoclaw-infra-tools", version: "0.1.0" });
  await client.connect(transport);
  try {
    if (!contractVerified) {
      const { tools } = await client.listTools();
      assertServerContract(tools.map((t) => t.name));
      contractVerified = true;
    }
    const result = await client.callTool({ name, arguments: args });
    const content = Array.isArray(result.content) ? result.content : [];
    for (const item of content) {
      if (item.type === "text") return item.text;
    }
    return "null";
  } finally {
    await client.close();
  }
}

/** Test hook — force the next call to re-verify the server contract. */
export function resetContractVerification(): void {
  contractVerified = false;
}
