import type { FaultEvent } from "../types";
import { gateway } from "../api/gateway";

const STATUS_COLOR: Record<string, string> = {
  detected: "#f59e0b",
  diagnosing: "#3b82f6",
  awaiting_approval: "#8b5cf6",
  remediating: "#f97316",
  resolved: "#22c55e",
  denied: "#ef4444",
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
    <div
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        padding: 16,
        marginBottom: 12,
        borderLeft: `4px solid ${color}`,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <span style={{ fontWeight: 600 }}>{fault.asset_id}</span>
          <span
            style={{
              marginLeft: 8,
              padding: "2px 8px",
              borderRadius: 12,
              background: color + "22",
              color,
              fontSize: 11,
              fontWeight: 700,
              textTransform: "uppercase",
            }}
          >
            {fault.status.replace("_", " ")}
          </span>
        </div>
        <div style={{ fontSize: 11, color: "#9ca3af" }}>
          {new Date(fault.detected_at).toLocaleTimeString()}
        </div>
      </div>

      <div style={{ marginTop: 8, fontSize: 12, color: "#374151" }}>
        Scenario: <code>{fault.scenario_id}</code>
      </div>

      {fault.log_extract && (
        <pre
          style={{
            marginTop: 8,
            padding: 8,
            background: "#111827",
            color: "#f3f4f6",
            borderRadius: 4,
            fontSize: 11,
            overflowX: "auto",
            whiteSpace: "pre-wrap",
          }}
        >
          {fault.log_extract}
        </pre>
      )}

      {fault.kb_article_id && (
        <div style={{ marginTop: 6, fontSize: 12, color: "#6b7280" }}>
          KB: <strong>{fault.kb_article_id}</strong>
        </div>
      )}

      {canDecide && (
        <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
          <button
            onClick={() => decide("approved")}
            style={{
              padding: "8px 20px",
              background: "#16a34a",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              fontWeight: 700,
              cursor: "pointer",
              fontSize: 14,
            }}
          >
            ✓ Approve
          </button>
          <button
            onClick={() => decide("denied")}
            style={{
              padding: "8px 20px",
              background: "#fff",
              color: "#dc2626",
              border: "2px solid #dc2626",
              borderRadius: 6,
              fontWeight: 700,
              cursor: "pointer",
              fontSize: 14,
            }}
          >
            ✗ Deny
          </button>
        </div>
      )}
    </div>
  );
}
