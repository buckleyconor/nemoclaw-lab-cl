import { useEffect, useRef, useState } from "react";
import type { ActivityEvent } from "../types";

const STEP_META: Record<string, { icon: string; label: string; color: string }> = {
  detect:    { icon: "🔍", label: "DETECT",    color: "#60a5fa" },
  diagnose:  { icon: "📋", label: "DIAGNOSE",  color: "#a78bfa" },
  search_kb: { icon: "📚", label: "SEARCH KB", color: "#f59e0b" },
  present:   { icon: "🖥️", label: "PRESENT",   color: "#38bdf8" },
  waiting:   { icon: "⏳", label: "WAITING",   color: "#94a3b8" },
  remediate: { icon: "🔧", label: "REMEDIATE", color: "#fb923c" },
  resolved:  { icon: "✅", label: "RESOLVED",  color: "#4ade80" },
  denied:    { icon: "❌", label: "DENIED",    color: "#f87171" },
};

// Real wake-up cadence from AGENTS.md ("polls the MCP monitor tool every 5
// seconds") — the ticker below mirrors that cadence so "Nth scan" stays
// truthful to how often the agent actually checks in, not an arbitrary number.
const POLL_INTERVAL_S = 5;

// Purely a liveness cue — never written to the activity log or exported in
// the audit report. While idle (no active fault) there is genuinely nothing
// to narrate turn over turn, so without this the panel looks the same
// whether the agent is polling every 5s or has silently died; this makes
// the difference visible.
function IdleScanTicker() {
  const [secondsAgo, setSecondsAgo] = useState(0);
  useEffect(() => {
    const id = setInterval(
      () => setSecondsAgo((s) => (s + 1) % POLL_INTERVAL_S),
      1000,
    );
    return () => clearInterval(id);
  }, []);
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "10px 14px", fontFamily: "var(--mono)",
    }}>
      <span style={{
        display: "block", width: 6, height: 6, borderRadius: "50%",
        background: "var(--healthy)", flexShrink: 0,
        animation: "idle-scan-dot 1.6s ease-in-out infinite",
      }} />
      <span style={{ color: "var(--text-dim)", fontSize: 12 }}>
        NemoClaw agent scanning fleet for events…
      </span>
      <span style={{ color: "var(--text-dim)", fontSize: 10, opacity: .6, marginLeft: "auto" }}>
        next check in {POLL_INTERVAL_S - secondsAgo}s
      </span>
    </div>
  );
}

interface Props {
  events: ActivityEvent[];
  idle: boolean;
  /** null = still loading, true = onboarding complete (Agent+Soul+Skills),
      false = agent not configured / not monitoring. */
  agentConfigured: boolean | null;
  agentStatusDetail?: string;
}

// Orange "not configured" strip (GET /api/agent/status). Shown whenever the
// agent's onboarding state is known-to-be-absent — regardless of idle —
// because it is the headline truth: nothing is watching the fleet.
function UnconfiguredWarning({ detail }: { detail?: string }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 3,
      padding: "10px 14px", fontFamily: "var(--mono)",
      background: "rgba(245, 158, 11, 0.06)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          display: "block", width: 6, height: 6, borderRadius: "50%",
          background: "var(--warning)", flexShrink: 0,
          animation: "dot-blink 1.2s ease-in-out infinite",
        }} />
        <span style={{ color: "var(--warning)", fontSize: 12, fontWeight: 600 }}>
          ⚠ Agent not configured — not monitoring infrastructure
        </span>
      </div>
      <div style={{
        fontSize: 10, color: "var(--text-dim)", paddingLeft: 14, lineHeight: 1.5,
      }}>
        Agent, Soul and Skills are not live in the sandbox.
        {detail ? ` (${detail})` : ""} Run <code>make bootstrap</code> on the lab
        host, or check <code>make doctor</code> / the Lab health chip.
      </div>
    </div>
  );
}

export function ActivityFeed({ events, idle, agentConfigured, agentStatusDetail }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  // Build grouped sections: each time the step changes, start a new group
  type Group = { step: string; entries: ActivityEvent[] };
  const groups: Group[] = [];
  for (const e of events) {
    const last = groups[groups.length - 1];
    if (last && last.step === e.step) {
      last.entries.push(e);
    } else {
      groups.push({ step: e.step, entries: [e] });
    }
  }

  return (
    <section style={{
      background: "var(--bg-card)",
      border: "1px solid var(--border)",
      borderRadius: 10,
      overflow: "hidden",
      display: "flex",
      flexDirection: "column",
      maxHeight: 420,
    }}>
      {/* Header */}
      <div style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--border)",
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: ".12em",
        textTransform: "uppercase",
        color: "var(--text-dim)",
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexShrink: 0,
      }}>
        <span style={{ display: "block", width: 3, height: 12, background: "var(--nv-green)", borderRadius: 2 }} />
        Agent Activity
      </div>

      {/* Log body */}
      <div style={{
        overflowY: "auto",
        flex: 1,
        fontFamily: "var(--mono)",
        fontSize: 11,
      }}>
        {/* Unconfigured strip — the agent cannot be "scanning" if onboarding
            (Agent + Soul + Skills) never completed or the wake hook is dead. */}
        {agentConfigured === false && (
          <div style={{ borderBottom: "1px solid var(--border)" }}>
            <UnconfiguredWarning detail={agentStatusDetail} />
          </div>
        )}
        {/* Idle liveness strip — shown above whatever history exists, so a
            fleet with only old/resolved entries still reads as "watched". */}
        {idle && agentConfigured === true && (
          <div style={{ borderBottom: events.length > 0 ? "1px solid var(--border)" : undefined }}>
            <IdleScanTicker />
          </div>
        )}
        {events.length === 0 ? (
          !idle && (
            <div style={{ padding: "16px 14px", color: "var(--text-dim)", fontSize: 12, fontFamily: "inherit" }}>
              No activity yet
            </div>
          )
        ) : (
          <>
            {groups.map((group, gi) => {
              const meta = STEP_META[group.step] ?? { icon: "▸", label: group.step.toUpperCase(), color: "var(--text-dim)" };
              return (
                <div key={gi}>
                  {/* Phase heading */}
                  <div style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "7px 14px 4px",
                    borderTop: gi > 0 ? "1px solid var(--border)" : undefined,
                  }}>
                    <span style={{ fontSize: 13 }}>{meta.icon}</span>
                    <span style={{
                      fontSize: 9,
                      fontWeight: 700,
                      letterSpacing: ".14em",
                      color: meta.color,
                      fontFamily: "var(--sans)",
                    }}>
                      {meta.label}
                    </span>
                    <span style={{ flex: 1, height: 1, background: meta.color, opacity: 0.2, borderRadius: 1 }} />
                  </div>

                  {/* Log lines */}
                  {group.entries.map((e) => (
                    <div key={e.id} style={{
                      display: "flex",
                      gap: 10,
                      padding: "2px 14px 2px 34px",
                      lineHeight: 1.55,
                    }}>
                      <span style={{ color: "var(--text-dim)", flexShrink: 0, userSelect: "none" }}>
                        {new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                      </span>
                      <span style={{ color: "var(--text)", wordBreak: "break-word" }}>
                        {e.message}
                      </span>
                    </div>
                  ))}
                </div>
              );
            })}
            <div ref={bottomRef} style={{ height: 6 }} />
          </>
        )}
      </div>
    </section>
  );
}
