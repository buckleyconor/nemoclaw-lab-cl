import { gateway } from "../api/gateway";
import type { ActivityEvent, FaultEvent } from "../types";

// ── helpers to mine parsed data from activity events ──────────────────────────

function getSignature(activity: ActivityEvent[], faultId: string): string | null {
  for (const e of activity) {
    if (e.fault_event_id !== faultId) continue;
    const m = e.message.match(/LLM identified error signature: "(.+?)"/);
    if (m) return m[1];
  }
  return null;
}

function getKBMatch(activity: ActivityEvent[], faultId: string): { id: string; confidence: string } | null {
  for (const e of activity) {
    if (e.fault_event_id !== faultId) continue;
    const m = e.message.match(/✓ KB article matched: (\S+) \(confidence (\d+%)/);
    if (m) return { id: m[1], confidence: m[2] };
  }
  return null;
}

function getLLMAnalysis(activity: ActivityEvent[], faultId: string): string | null {
  const templateStarts = [
    "Parsing log", "Sending log", "LLM identified", "Canonical signature", "Composing fault",
  ];
  for (const e of activity) {
    if (e.fault_event_id !== faultId || e.step !== "diagnose") continue;
    if (templateStarts.some((p) => e.message.startsWith(p))) continue;
    if (e.message.length > 80) return e.message;
  }
  return null;
}

// ── status metadata ───────────────────────────────────────────────────────────

const STATUS_META: Record<string, { label: string; color: string; pulse: boolean }> = {
  detected:          { label: "DETECTING",           color: "#f59e0b", pulse: true  },
  diagnosing:        { label: "DIAGNOSING",           color: "#3b82f6", pulse: true  },
  awaiting_approval: { label: "AWAITING APPROVAL",    color: "#8b5cf6", pulse: true  },
  remediating:       { label: "REMEDIATING",          color: "#f97316", pulse: true  },
  resolved:          { label: "RESOLVED",             color: "#10b981", pulse: false },
  denied:            { label: "DENIED",               color: "#ef4444", pulse: false },
};

// ── component ─────────────────────────────────────────────────────────────────

interface Props {
  fault: FaultEvent;
  activity: ActivityEvent[];
  onDecision: (faultId: string, decision: "approved" | "denied") => void;
}

export function OperatorDashboard({ fault, activity, onDecision }: Props) {
  const meta = STATUS_META[fault.status] ?? { label: fault.status.toUpperCase(), color: "#6b7280", pulse: false };
  const signature = getSignature(activity, fault.id);
  const kb = getKBMatch(activity, fault.id);
  const analysis = getLLMAnalysis(activity, fault.id);
  const canDecide = fault.status === "awaiting_approval";
  const isActive = fault.status !== "resolved" && fault.status !== "denied";

  async function decide(decision: "approved" | "denied") {
    await gateway.decide(fault.id, decision);
    onDecision(fault.id, decision);
  }

  return (
    <div style={{
      background: "var(--bg-card)",
      border: `1px solid ${meta.color}44`,
      borderTop: `3px solid ${meta.color}`,
      borderRadius: 10,
      overflow: "hidden",
    }}>
      {/* Header bar */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 18px",
        borderBottom: `1px solid ${meta.color}22`,
        background: `${meta.color}08`,
      }}>
        {meta.pulse && isActive && (
          <span style={{
            display: "block", width: 8, height: 8,
            borderRadius: "50%", background: meta.color,
            boxShadow: `0 0 6px ${meta.color}`,
            animation: "pulse 1.4s ease-in-out infinite",
          }} />
        )}
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: ".14em",
          color: meta.color, fontFamily: "var(--mono)",
        }}>
          OPERATOR DASHBOARD
        </span>
        <span style={{
          padding: "2px 8px", borderRadius: 4,
          background: meta.color + "22", color: meta.color,
          fontSize: 9, fontWeight: 700, letterSpacing: ".08em",
          fontFamily: "var(--mono)",
        }}>
          {meta.label}
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--mono)" }}>
          {fault.asset_id}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-dim)", fontFamily: "var(--mono)" }}>
          {new Date(fault.detected_at).toLocaleTimeString()}
        </span>
      </div>

      {/* Body */}
      <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 14 }}>

        {/* Top row: signature + KB side by side */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {/* Error signature */}
          <div style={{
            background: "#060a14",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "12px 14px",
          }}>
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: ".12em",
              color: "var(--text-dim)", marginBottom: 6, fontFamily: "var(--mono)",
            }}>
              ERROR SIGNATURE
            </div>
            {signature ? (
              <div style={{
                fontSize: 15, fontWeight: 700, color: "#f59e0b",
                fontFamily: "var(--mono)", letterSpacing: ".02em",
              }}>
                {signature}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Extracting…</div>
            )}
          </div>

          {/* KB article */}
          <div style={{
            background: "#060a14",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "12px 14px",
          }}>
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: ".12em",
              color: "var(--text-dim)", marginBottom: 6, fontFamily: "var(--mono)",
            }}>
              KNOWLEDGE BASE
            </div>
            {kb ? (
              <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#60a5fa", fontFamily: "var(--mono)" }}>
                  {kb.id}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 3 }}>
                  Confidence: <span style={{ color: "var(--text)" }}>{kb.confidence}</span>
                </div>
              </div>
            ) : fault.kb_article_id ? (
              <div style={{ fontSize: 13, fontWeight: 700, color: "#60a5fa", fontFamily: "var(--mono)" }}>
                {fault.kb_article_id}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Searching…</div>
            )}
          </div>
        </div>

        {/* LLM analysis */}
        {analysis && (
          <div style={{
            background: "#0d1117",
            border: "1px solid #8b5cf633",
            borderLeft: "3px solid #8b5cf6",
            borderRadius: "0 8px 8px 0",
            padding: "10px 14px",
          }}>
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: ".12em",
              color: "#8b5cf6", marginBottom: 6, fontFamily: "var(--mono)",
            }}>
              AGENT ANALYSIS
            </div>
            <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
              {analysis}
            </div>
          </div>
        )}

        {/* Remediation steps — only shown once KB article is confirmed */}
        {kb && fault.remediation_step_labels.length > 0 && (
          <div>
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: ".12em",
              color: "var(--text-dim)", marginBottom: 8, fontFamily: "var(--mono)",
            }}>
              PROPOSED REMEDIATION STEPS
            </div>
            <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
              {fault.remediation_step_labels.map((label, i) => (
                <li key={i} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  fontSize: 12, color: "var(--text)",
                }}>
                  <span style={{
                    width: 20, height: 20, borderRadius: "50%",
                    background: "var(--border)", color: "var(--text-dim)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 10, fontWeight: 700, flexShrink: 0,
                    fontFamily: "var(--mono)",
                  }}>
                    {i + 1}
                  </span>
                  {label}
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* Approve / Deny */}
        {canDecide && (
          <div style={{ display: "flex", gap: 10, marginTop: 2 }}>
            <button
              onClick={() => decide("approved")}
              style={{
                flex: 1, padding: "10px 16px",
                background: "rgba(16,185,129,.12)",
                border: "1px solid rgba(16,185,129,.4)",
                borderRadius: 7, color: "#10b981",
                fontWeight: 700, fontSize: 13, cursor: "pointer",
                fontFamily: "var(--sans)", letterSpacing: ".04em",
                transition: "background .15s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(16,185,129,.22)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(16,185,129,.12)")}
            >
              ✓ Approve Self-Heal
            </button>
            <button
              onClick={() => decide("denied")}
              style={{
                flex: 1, padding: "10px 16px",
                background: "rgba(239,68,68,.08)",
                border: "1px solid rgba(239,68,68,.3)",
                borderRadius: 7, color: "#ef4444",
                fontWeight: 700, fontSize: 13, cursor: "pointer",
                fontFamily: "var(--sans)", letterSpacing: ".04em",
                transition: "background .15s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(239,68,68,.18)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(239,68,68,.08)")}
            >
              ✕ Deny
            </button>
          </div>
        )}

        {fault.status === "remediating" && (
          <div style={{ fontSize: 12, color: "#f97316", fontFamily: "var(--mono)", letterSpacing: ".04em" }}>
            ⟳ Remediation in progress — see Agent Activity for step-by-step detail
          </div>
        )}

        {fault.status === "resolved" && (
          <div style={{ fontSize: 12, color: "#10b981", fontFamily: "var(--mono)", letterSpacing: ".04em" }}>
            ✅ {fault.asset_id} returned to healthy state
          </div>
        )}

        {fault.status === "denied" && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" as const }}>
            <div style={{ fontSize: 12, color: "#ef4444", fontFamily: "var(--mono)", letterSpacing: ".04em" }}>
              ✕ Self-heal was denied — node remains faulted
            </div>
            <button
              onClick={async () => {
                await gateway.updateFaultStatus(fault.id, "resolved");
                onDecision(fault.id, "denied");
              }}
              style={{
                padding: "6px 14px",
                background: "rgba(107,114,128,.12)",
                border: "1px solid rgba(107,114,128,.3)",
                borderRadius: 6, color: "#9ca3af",
                fontWeight: 600, fontSize: 11, cursor: "pointer",
                fontFamily: "var(--sans)", letterSpacing: ".04em",
              }}
            >
              Dismiss Fault
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
