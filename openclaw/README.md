# OpenClaw Agent Runtime (ADR-011)

This directory is the real NemoClaw/OpenClaw replacement for the retired
bespoke Python agent (`agent/`, ADR-010). It holds everything that gets
deployed into the OpenClaw sandbox by `deploy/scripts/onboard-openclaw.sh`:

```
openclaw/
├── SOUL.md                      # agent identity (workspace bootstrap file)
├── AGENTS.md                    # orientation + Standing Order (auto-injected every session)
├── skills/                      # OpenClaw skills → <workspace>/skills
│   ├── infra-sentinel-monitor/
│   ├── infra-sentinel-diagnose/
│   ├── infra-sentinel-notify/
│   └── infra-sentinel-remediate/
└── plugins/
    └── nemoclaw-infra-tools/    # OpenClaw tool plugin (MCP client → mcp-tools)
```

## How the pieces map to ADR-010's runtime

| ADR-010 (deleted)                          | ADR-011 (this)                                   |
|--------------------------------------------|--------------------------------------------------|
| `agent/soul.md` + prompt concatenation     | `SOUL.md` + `AGENTS.md` workspace bootstrap      |
| `agent/skills/infra-sentinel-guide`        | folded into `AGENTS.md`                          |
| `agent/tools.py` LLM tool allowlist        | plugin `contracts.tools` (7 tools, no execute)   |
| `agent/loop.py` dispatch side-work         | plugin handlers (`src/harness.ts`)               |
| `agent/loop.py` approval poll + execute    | Gateway `post_decision()` → `services/gateway/executor.py` |
| `agent/main.py` 5-second poll              | Gateway webhook on inject + cron safety net      |
| `agent/llm.py` OpenAI client               | OpenClaw's own provider (OpenAI-compatible endpoint) |

## HITL invariant (ADR-004, restated)

`remediation.execute` is never callable by the model:

1. Not in the plugin manifest's `contracts.tools`, not in its dispatch table
   (`src/mcp.ts` `LLM_EXPOSED_TOOLS`); a module-load assert refuses any
   edit that re-introduces it.
2. OpenClaw tool policy denies it and every built-in tool
   (`tools.profile: full` narrowed by an explicit `tools.allow`, plus
   `tools.deny`; deny wins over allow) — set by the onboard script.
   `profile: minimal` looks safer but is not usable here: the profile sets a
   BASE allowlist that `tools.allow` can only narrow, and `minimal` caps it
   at `session_status`, which silently drops the plugin's seven tools.
3. The token is minted only on operator approval and consumed by the
   Gateway's server-side executor; it never enters any LLM context.
   `remediation.execute` itself still validates token binding, single-use,
   and the step allowlist (SEC-01..05), unchanged.

Note: the MCP server legitimately *lists* `remediation.execute` — the
Gateway calls it post-approval over the same MCP wire. The plugin hard-fails
if that name could ever become dispatchable through the LLM surface (see
`assertServerContract`), which is the enforceable reading of ADR-011's
load-time guard.

## Platform note

OpenShell publishes no macOS x86_64 gateway assets, so `nemoclaw onboard`
hard-fails on Intel Macs (NVIDIA platform-support matrix). Onboard on a
Linux x86_64/aarch64 host or Apple Silicon Mac that can reach the lab's
published ports (8001, 8004).

## Plugin development

```bash
cd plugins/nemoclaw-infra-tools
npm install
npm run build          # tsc → dist/
npx openclaw plugins build     # regenerate openclaw.plugin.json
npx openclaw plugins validate
npm test
```
