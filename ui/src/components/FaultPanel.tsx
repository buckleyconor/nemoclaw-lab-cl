import type { FaultEvent } from "../types";
import { gateway } from "../api/gateway";

const STATUS_COLOR: Record<string, string> = {
  detected:          "#f59e0b",
  diagnosing:        "#3b82f6",
  awaiting_approval: "#8b5cf6",
  remediating:       "#f97316",
  resolved:          "#10b981",
  denied:            "#ef4444",
};

interface Props {
  fault: FaultEvent;
  onDecision: (faultId: string, decision: "approved" | "denied") => void;
}

export function FaultPanel({ fault, onDecision }: Props) {
  async function decide(decision: "approved" | "denied") {
    await gateway.decide(fault.id, decision);
    onDecision(fault.id, decision);
  }

  const color = STATUS_COLOR[fault.status] ?? "#6b7280";
  const canDecide = fault.status === "awaiting_approval";

  return (
    <div style={{
      background: "var(--bg-card)",
      border: "1px solid var(--border)",
      borderLeft: `3px solid ${color}`,
      borderRadius: "0 8px 8px 0",
      padding: "14px 16px",
      marginBottom: 10,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontWeight: 600, color: "#fff", fontSize: 13 }}>{fault.asset_id}</span>
          <span style={{
            padding: "2px 8px",
            borderRadius: 4,
            background: color + "22",
            color,
            fontSize: 10,
            fontWeight: 700,
            textTransform: "uppercase",
            fontFamily: "var(--mono)",
            letterSpacing: ".05em",
          }}>
            {fault.status.replace(/_/g, " ")}
          </span>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--mono)" }}>
          {new Date(fault.detected_at).toLocaleTimeString()}
        </div>
      </div>

      {fault.log_extract && (
        <div style={{
          background: "#060a14",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "8px 10px",
          marginBottom: 10,
          fontFamily: "var(--mono)",
          fontSize: 11,
          color: "#f59e0b",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          maxHeight: 80,
          overflowY: "auto",
          lineHeight: 1.5,
        }}>
          {fault.log_extract}
        </div>
      )}

      {fault.kb_article_id && (
        <div style={{
          fontSize: 11,
          color: "var(--text-muted)",
          marginBottom: 12,
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}>
          <span style={{ color: "var(--accent)" }}>📚</span>
          KB article matched: <strong style={{ color: "var(--text)" }}>{fault.kb_article_id}</strong>
        </div>
      )}

      {canDecide && (
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => decide("approved")}
            style={{
              flex: 1,
              padding: "8px 12px",
              background: "rgba(16,185,129,.12)",
              border: "1px solid rgba(16,185,129,.35)",
              borderRadius: 6,
              color: "#10b981",
              fontWeight: 700,
              fontSize: 12,
              cursor: "pointer",
              fontFamily: "var(--sans)",
              letterSpacing: ".03em",
              transition: "background .15s",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = "rgba(16,185,129,.22)")}
            onMouseLeave={e => (e.currentTarget.style.background = "rgba(16,185,129,.12)")}
          >
            ✓ Approve
          </button>
          <button
            onClick={() => decide("denied")}
            style={{
              flex: 1,
              padding: "8px 12px",
              background: "rgba(239,68,68,.1)",
              border: "1px solid rgba(239,68,68,.3)",
              borderRadius: 6,
              color: "#ef4444",
              fontWeight: 700,
              fontSize: 12,
              cursor: "pointer",
              fontFamily: "var(--sans)",
              letterSpacing: ".03em",
              transition: "background .15s",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = "rgba(239,68,68,.2)")}
            onMouseLeave={e => (e.currentTarget.style.background = "rgba(239,68,68,.1)")}
          >
            ✕ Deny
          </button>
        </div>
      )}
    </div>
  );
}
