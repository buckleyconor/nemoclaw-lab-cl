import { useEffect, useRef } from "react";
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

interface Props {
  events: ActivityEvent[];
}

export function ActivityFeed({ events }: Props) {
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
        {events.length === 0 ? (
          <div style={{ padding: "16px 14px", color: "var(--text-dim)", fontSize: 12, fontFamily: "inherit" }}>
            No activity yet
          </div>
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
