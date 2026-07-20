import { useEffect, useState } from "react";
import { gateway } from "../api/gateway";
import type { ActivityEvent, FaultEvent } from "../types";

// ── status metadata ───────────────────────────────────────────────────────────

const STATUS_META: Record<string, { label: string; color: string; pulse: boolean }> = {
  detected:          { label: "DETECTING",           color: "#f59e0b", pulse: true  },
  diagnosing:        { label: "DIAGNOSING",           color: "#3b82f6", pulse: true  },
  awaiting_approval: { label: "AWAITING APPROVAL",    color: "#8b5cf6", pulse: true  },
  remediating:       { label: "REMEDIATING",          color: "#f97316", pulse: true  },
  resolved:          { label: "RESOLVED",             color: "#10b981", pulse: false },
  denied:            { label: "DENIED",               color: "#ef4444", pulse: false },
};

// ── event report export (audit trail) ─────────────────────────────────────────

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function exportReport(fault: FaultEvent, activity: ActivityEvent[]) {
  const rows = activity
    .filter((a) => a.fault_event_id === fault.id)
    .map(
      (a) =>
        `<tr><td>${new Date(a.ts).toLocaleTimeString()}</td>` +
        `<td>${escapeHtml(a.step)}</td><td>${escapeHtml(a.message)}</td></tr>`
    )
    .join("\n");
  const steps = fault.remediation_step_labels
    .map((l, i) => `<li>${i + 1}. ${escapeHtml(l)}</li>`)
    .join("\n");
  const impact = fault.impact
    ? `<table class="kv">
        <tr><th>Action summary</th><td>${escapeHtml(fault.impact.summary)}</td></tr>
        <tr><th>Workload impact</th><td>${escapeHtml(fault.impact.workload_impact)}</td></tr>
        <tr><th>Service risk</th><td>${escapeHtml(fault.impact.service_risk)}</td></tr>
        <tr><th>Estimated duration</th><td>${escapeHtml(fault.impact.estimated_duration)}</td></tr>
      </table>`
    : "<p>—</p>";
  const html = `<!doctype html><html><head><meta charset="utf-8">
<title>NemoClaw Fault Event Report — ${escapeHtml(fault.id)}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: #111; margin: 32px; }
  h1 { font-size: 20px; } h2 { font-size: 14px; margin-top: 22px; text-transform: uppercase; letter-spacing: .08em; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  td, th { border: 1px solid #ccc; padding: 5px 8px; text-align: left; vertical-align: top; }
  table.kv th { width: 180px; background: #f5f5f5; }
  ul { list-style: none; padding: 0; font-size: 13px; }
  .muted { color: #666; font-size: 11px; }
</style></head><body>
<h1>NemoClaw Agent — Fault Event Report</h1>
<p class="muted">Generated ${new Date().toLocaleString()} · Fault ID ${escapeHtml(fault.id)}</p>
<h2>Fault</h2>
<table class="kv">
  <tr><th>Asset</th><td>${escapeHtml(fault.asset_id)}</td></tr>
  <tr><th>Scenario</th><td>${escapeHtml(fault.scenario_id)}</td></tr>
  <tr><th>Detected at</th><td>${new Date(fault.detected_at).toLocaleString()}</td></tr>
  <tr><th>Final status</th><td>${escapeHtml(fault.status)}</td></tr>
  <tr><th>Error signature</th><td>${escapeHtml(fault.error_signature ?? "—")}</td></tr>
  <tr><th>KB article</th><td>${escapeHtml(
    fault.kb_article_id
      ? `${fault.kb_article_id}${fault.kb_title ? " — " + fault.kb_title : ""}` +
        (fault.kb_score != null ? ` (confidence ${Math.round(fault.kb_score * 100)}%)` : "")
      : "—"
  )}</td></tr>
</table>
<h2>Gathered log evidence</h2>
<pre style="white-space:pre-wrap;font-size:11px;background:#f5f5f5;padding:10px;border:1px solid #ccc;border-radius:4px;">${escapeHtml(fault.log_extract ?? "—")}</pre>
<h2>Agent assessment</h2>
<p>${escapeHtml(fault.analysis ?? "—")}</p>
<h2>Impact assessment</h2>
${impact}
<h2>Remediation plan</h2>
<ul>${steps || "<li>—</li>"}</ul>
<h2>Full activity log</h2>
<table><tr><th>Time</th><th>Phase</th><th>Message</th></tr>${rows}</table>
</body></html>`;
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.write(html);
  w.document.close();
  w.focus();
  w.print();
}

// ── component ─────────────────────────────────────────────────────────────────

const cardStyle = {
  background: "#060a14",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: "12px 14px",
} as const;

const cardLabelStyle = {
  fontSize: 9, fontWeight: 700, letterSpacing: ".12em",
  color: "var(--text-dim)", marginBottom: 6, fontFamily: "var(--mono)",
} as const;

interface Props {
  fault: FaultEvent | null;
  activity: ActivityEvent[];
  onDecision: (faultId: string, decision: "approved" | "denied") => void;
}

export function OperatorDashboard({ fault, activity, onDecision }: Props) {
  const [deciding, setDeciding] = useState<string | null>(null);

  // A new fault (different id) invalidates any decision state left over from
  // the previous one. Without this, a successful decide() on fault #1 leaves
  // `deciding` permanently non-null (only the catch branch clears it), which
  // then incorrectly disables the Approve/Deny buttons for every fault after
  // the first — this component instance is reused across faults, it isn't
  // remounted per fault_event_id.
  useEffect(() => {
    setDeciding(null);
  }, [fault?.id]);

  // Staged population (dashboard reads top-to-bottom in the order the agent
  // works): 1) error signature, 2) KB match — both arrive as separate
  // diagnosis PATCHes from the agent harness. 3) agent assessment, then
  // 4) impact assessment, then 5) proposed remediation steps. The last three
  // all become available the moment the agent's proposal lands (analysis +
  // status change in one burst), so the impact and steps are revealed on
  // short client-side timers after the assessment — the operator sees them
  // populate in causal order instead of one simultaneous pop.
  const assessmentReady =
    fault !== null &&
    (fault.analysis !== null ||
      fault.status === "awaiting_approval" ||
      fault.status === "remediating" ||
      fault.status === "resolved" ||
      fault.status === "denied");
  const settled =
    fault !== null &&
    (fault.status === "remediating" ||
      fault.status === "resolved" ||
      fault.status === "denied");
  const [revealImpact, setRevealImpact] = useState(false);
  const [revealSteps, setRevealSteps] = useState(false);
  useEffect(() => {
    if (!assessmentReady) {
      setRevealImpact(false);
      setRevealSteps(false);
      return;
    }
    if (settled) {
      // Post-decision (or page reload mid-remediation): everything is old
      // news — show it all, no theatre.
      setRevealImpact(true);
      setRevealSteps(true);
      return;
    }
    const t1 = setTimeout(() => setRevealImpact(true), 1200);
    const t2 = setTimeout(() => setRevealSteps(true), 2600);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [fault?.id, assessmentReady, settled]);

  // Idle shell — the panel stays visible even with nothing to show (cleared
  // after the post-resolution retention window).
  if (!fault) {
    return (
      <div style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        borderTop: "3px solid var(--border-strong)",
        borderRadius: 10,
        padding: "22px 18px",
        display: "flex", alignItems: "center", gap: 10,
        color: "var(--text-dim)", fontSize: 13,
      }}>
        <span style={{ color: "var(--healthy)", fontSize: 15 }}>◉</span>
        No active fault — NemoClaw Agent is monitoring. Fault details, impact
        assessment and approval controls will appear here when an incident is detected.
      </div>
    );
  }

  const meta = STATUS_META[fault.status] ?? { label: fault.status.toUpperCase(), color: "#6b7280", pulse: false };
  const isDecided = deciding !== null && fault.status === "awaiting_approval";
  const canDecide = fault.status === "awaiting_approval" && deciding === null;
  const isActive = fault.status !== "resolved" && fault.status !== "denied";
  const exportable = !isActive;
  // Both impact assessment and remediation steps are already available on the
  // FaultEvent as soon as it's created (from the scenario's static content),
  // well before the agent has actually diagnosed anything. Gate their display
  // on the KB match *and* the staged-reveal timers above, so the operator sees
  // the dashboard fill in the causal order the agent works in: signature →
  // KB match → assessment → impact → remediation steps (last).
  const showImpact = fault.impact !== null && fault.kb_article_id !== null && revealImpact;
  const showSteps =
    fault.kb_article_id !== null &&
    fault.remediation_step_labels.length > 0 &&
    revealSteps;

  async function decide(decision: "approved" | "denied") {
    if (!fault) return;
    setDeciding(decision); // grey out immediately — the click always registers
    try {
      await gateway.decide(fault.id, decision);
      onDecision(fault.id, decision);
    } catch {
      setDeciding(null); // request failed — re-enable so the operator can retry
    }
  }

  const disabledStyle = {
    background: "rgba(107,114,128,.15)",
    border: "1px solid rgba(107,114,128,.3)",
    color: "#6b7280",
    cursor: "not-allowed",
    opacity: 0.65,
  } as const;

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
        {/* Exportable once the fault reaches a terminal decision — a denied
            fault's audit trail (who declined what, and why the agent asked)
            matters as much as a resolved one's. */}
        <button
          onClick={() => exportable && exportReport(fault, activity)}
          disabled={!exportable}
          title={
            exportable
              ? "Export a printable event report for the audit trail"
              : "Available once the operator has decided and any self-heal has completed"
          }
          style={
            exportable
              ? {
                  padding: "4px 10px",
                  background: "rgba(96,165,250,.1)",
                  border: "1px solid rgba(96,165,250,.35)",
                  borderRadius: 5, color: "#60a5fa",
                  fontWeight: 600, fontSize: 10, cursor: "pointer",
                  fontFamily: "var(--mono)", letterSpacing: ".06em",
                }
              : {
                  padding: "4px 10px",
                  background: "rgba(107,114,128,.08)",
                  border: "1px solid rgba(107,114,128,.25)",
                  borderRadius: 5, color: "#6b7280",
                  fontWeight: 600, fontSize: 10, cursor: "not-allowed",
                  fontFamily: "var(--mono)", letterSpacing: ".06em",
                }
          }
        >
          ⬇ EXPORT REPORT
        </button>
        <span style={{ fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--mono)" }}>
          {fault.asset_id}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-dim)", fontFamily: "var(--mono)" }}>
          {new Date(fault.detected_at).toLocaleTimeString()}
        </span>
      </div>

      {/* Body */}
      <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 14 }}>

        {/* Gathered log evidence — populates first (it's captured the moment
            the log bundle is retrieved, before the agent has extracted a
            signature or matched a KB article), so the operator can read the
            raw lines and check them against ERROR SIGNATURE below once it
            appears, rather than taking the extracted signature on faith. */}
        {fault.log_extract && (
          <div style={cardStyle}>
            <div style={cardLabelStyle}>GATHERED LOG EVIDENCE — {fault.asset_id}</div>
            <pre style={{
              margin: 0, fontSize: 11, lineHeight: 1.6, color: "#9fb3c8",
              fontFamily: "var(--mono)", whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}>
              {fault.log_extract}
            </pre>
          </div>
        )}

        {/* Top row: signature + KB side by side */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {/* Error signature */}
          <div style={cardStyle}>
            <div style={cardLabelStyle}>ERROR SIGNATURE</div>
            {fault.error_signature ? (
              <div style={{
                fontSize: 15, fontWeight: 700, color: "#f59e0b",
                fontFamily: "var(--mono)", letterSpacing: ".02em",
              }}>
                {fault.error_signature}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Extracting…</div>
            )}
          </div>

          {/* KB article */}
          <div style={cardStyle}>
            <div style={cardLabelStyle}>KNOWLEDGE BASE</div>
            {fault.kb_article_id ? (
              <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#60a5fa", fontFamily: "var(--mono)" }}>
                  {fault.kb_article_id}
                </div>
                {fault.kb_title && (
                  <div style={{ fontSize: 11, color: "var(--text)", marginTop: 3 }}>
                    {fault.kb_title}
                  </div>
                )}
                {fault.kb_score != null && (
                  <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 3 }}>
                    Confidence:{" "}
                    <span style={{ color: "var(--text)" }}>
                      {Math.round(fault.kb_score * 100)}%
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Searching…</div>
            )}
          </div>
        </div>

        {/* Agent assessment */}
        {fault.analysis ? (
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
              AGENT ASSESSMENT
            </div>
            <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
              {fault.analysis}
            </div>
          </div>
        ) : isActive ? (
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--mono)",
          }}>
            <span
              aria-label="working"
              style={{
                display: "block", width: 12, height: 12,
                borderRadius: "50%", flexShrink: 0,
                border: "2px solid rgba(139,92,246,.25)",
                borderTopColor: "#8b5cf6",
                animation: "spin .9s linear infinite",
              }}
            />
            Agent assessment pending…
          </div>
        ) : null}

        {/* Impact assessment — revealed after the agent assessment (staged order) */}
        {showImpact && fault.impact && (
          <div style={{ animation: "riseIn .35s ease-out" }}>
            <div style={cardLabelStyle}>IMPACT ASSESSMENT</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {[
                { label: "ACTION", value: fault.impact.summary },
                { label: "WORKLOAD IMPACT", value: fault.impact.workload_impact },
                { label: "SERVICE RISK", value: fault.impact.service_risk },
                { label: "ESTIMATED DURATION", value: fault.impact.estimated_duration },
              ].map(({ label, value }) => (
                <div key={label} style={{ ...cardStyle, padding: "9px 12px" }}>
                  <div style={{ ...cardLabelStyle, marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 11.5, color: "var(--text)", lineHeight: 1.5 }}>
                    {value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Remediation steps — the last section to populate (staged order) */}
        {showSteps && (
          <div style={{ animation: "riseIn .35s ease-out" }}>
            <div style={{ ...cardLabelStyle, marginBottom: 8 }}>
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

        {/* Approve / Deny — revealed together with the proposed steps so the
            decision controls never appear above/before the plan they approve */}
        {(canDecide || isDecided) && revealSteps && (
          <div style={{ display: "flex", gap: 10, marginTop: 2, flexWrap: "wrap" as const, animation: "riseIn .35s ease-out" }}>
            <button
              onClick={() => decide("approved")}
              disabled={!canDecide}
              style={{
                flex: 1, minWidth: 220, padding: "12px 16px",
                borderRadius: 7,
                fontWeight: 700, fontSize: 13.5,
                fontFamily: "var(--sans)", letterSpacing: ".04em",
                transition: "background .15s, box-shadow .15s",
                ...(canDecide
                  ? {
                      background: "#10b981",
                      border: "1px solid #34d399",
                      color: "#03130c",
                      cursor: "pointer",
                      boxShadow: "0 0 14px rgba(16,185,129,.35)",
                    }
                  : disabledStyle),
              }}
              onMouseEnter={(e) => { if (canDecide) e.currentTarget.style.background = "#34d399"; }}
              onMouseLeave={(e) => { if (canDecide) e.currentTarget.style.background = "#10b981"; }}
            >
              ✓ Approve Self-Heal
            </button>
            <button
              onClick={() => decide("denied")}
              disabled={!canDecide}
              style={{
                flex: 1, minWidth: 220, padding: "12px 16px",
                borderRadius: 7,
                fontWeight: 700, fontSize: 13.5,
                fontFamily: "var(--sans)", letterSpacing: ".04em",
                transition: "background .15s, box-shadow .15s",
                ...(canDecide
                  ? {
                      background: "#dc2626",
                      border: "1px solid #f87171",
                      color: "#fff",
                      cursor: "pointer",
                      boxShadow: "0 0 14px rgba(220,38,38,.3)",
                    }
                  : disabledStyle),
              }}
              onMouseEnter={(e) => { if (canDecide) e.currentTarget.style.background = "#ef4444"; }}
              onMouseLeave={(e) => { if (canDecide) e.currentTarget.style.background = "#dc2626"; }}
            >
              ✕ Deny, Manual Remediation
            </button>
            {isDecided && (
              <div style={{
                width: "100%", fontSize: 11, color: "var(--text-dim)",
                fontFamily: "var(--mono)", letterSpacing: ".04em",
              }}>
                ⟳ Decision submitted ({deciding}) — waiting for the system to confirm…
              </div>
            )}
          </div>
        )}

        {fault.status === "remediating" && (
          <div style={{ fontSize: 12, color: "#f97316", fontFamily: "var(--mono)", letterSpacing: ".04em" }}>
            ⟳ Remediation in progress — see Agent Activity for step-by-step detail
          </div>
        )}

        {fault.status === "resolved" && (
          <div style={{ fontSize: 12, color: "#10b981", fontFamily: "var(--mono)", letterSpacing: ".04em" }}>
            ✅ {fault.asset_id} returned to healthy state — this summary clears in one
            minute (use Export Report to keep an audit copy)
          </div>
        )}

        {fault.status === "denied" && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" as const }}>
            <div style={{ fontSize: 12, color: "#ef4444", fontFamily: "var(--mono)", letterSpacing: ".04em" }}>
              ✕ Self-heal was denied — manual remediation required, node remains faulted
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
