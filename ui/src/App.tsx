import { useCallback, useEffect, useRef, useState } from "react";
import { gateway } from "./api/gateway";
import { useSSE } from "./hooks/useSSE";
import { ActivityFeed } from "./components/ActivityFeed";
import { FleetGrid } from "./components/FleetGrid";
import { OperatorDashboard } from "./components/OperatorDashboard";
import { NotificationInbox } from "./components/NotificationInbox";
import { TerminalPanel } from "./components/TerminalPanel";
import "./App.css";
import type {
  ActivityEvent,
  AssetRecord,
  FaultEvent,
  Notification,
  PackInfo,
  SSEEvent,
} from "./types";

// How long a resolved fault stays on the Operator Dashboard before the panel
// clears back to its idle state.
const RESOLVED_RETENTION_MS = 60_000;

export default function App() {
  const [pack, setPack] = useState<PackInfo | null>(null);
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [faults, setFaults] = useState<FaultEvent[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [selectedFaultId, setSelectedFaultId] = useState<string | null>(null);
  const [retainedFault, setRetainedFault] = useState<FaultEvent | null>(null);
  const retainTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Full REST snapshot. Runs on mount, and again whenever the SSE stream
  // reconnects after a drop — the stream only carries deltas, so anything
  // that happened while disconnected (most notably a backend restart from
  // an automated pack switch) is invisible without a refetch.
  const fetchSnapshot = useCallback(() => {
    Promise.all([
      gateway.getPack(),
      gateway.getAssets(),
      gateway.getFaults(),
      gateway.getNotifications(),
      gateway.getActivity(),
    ]).then(([p, a, f, n, act]) => {
      setPack(p as PackInfo);
      setAssets((a as { assets: AssetRecord[] }).assets);
      setFaults((f as { faults: FaultEvent[] }).faults);
      setNotifications((n as { notifications: Notification[] }).notifications);
      setUnreadCount((n as { unread_count: number }).unread_count);
      setActivity((act as { activity: ActivityEvent[] }).activity);
    }).catch(() => {
      // Backend still down mid-restart — the next reconnect retries.
    });
  }, []);

  useEffect(() => {
    fetchSnapshot();
  }, [fetchSnapshot]);

  const handleSSE = useCallback((evt: SSEEvent) => {
    if (evt.type === "asset") {
      setAssets((prev) =>
        prev.map((a) => (a.id === evt.data.id ? { ...a, ...evt.data } : a))
      );
    } else if (evt.type === "fault") {
      setFaults((prev) => {
        const idx = prev.findIndex((f) => f.id === evt.data.id);
        return idx >= 0
          ? prev.map((f) => (f.id === evt.data.id ? evt.data : f))
          : [evt.data, ...prev];
      });
      if (evt.data.status === "resolved") {
        // Keep the completed summary on the dashboard for a minute, then clear.
        setRetainedFault(evt.data);
        if (retainTimer.current) clearTimeout(retainTimer.current);
        retainTimer.current = setTimeout(() => setRetainedFault(null), RESOLVED_RETENTION_MS);
      } else if (evt.data.status === "detected" || evt.data.status === "diagnosing") {
        // A new investigation supersedes any retained summary.
        setRetainedFault(null);
        if (retainTimer.current) clearTimeout(retainTimer.current);
      }
    } else if (evt.type === "notification") {
      setNotifications((prev) => [evt.data, ...prev]);
      setUnreadCount((c) => c + 1);
    } else if (evt.type === "activity") {
      setActivity((prev) => [...prev, evt.data]);
    } else if (evt.type === "reset") {
      setFaults([]);
      setActivity([]);
      setNotifications([]);
      setUnreadCount(0);
      setSelectedFaultId(null);
      setRetainedFault(null);
      if (retainTimer.current) clearTimeout(retainTimer.current);
    }
  }, []);

  const sseConnected = useSSE(handleSSE, fetchSnapshot);

  function handleDecision(faultId: string, decision: "approved" | "denied") {
    setFaults((prev) =>
      prev.map((f) =>
        f.id === faultId
          ? { ...f, status: decision === "approved" ? "awaiting_approval" : "denied" }
          : f
      )
    );
    setSelectedFaultId(null); // clear manual selection after decision
  }

  function handleMarkRead(id: string) {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    );
    setUnreadCount((c) => Math.max(0, c - 1));
  }

  // If the user clicked a specific asset, show its most recent fault (even denied).
  // Otherwise: prefer awaiting_approval > any other active state.
  const activeFault = (() => {
    if (selectedFaultId) {
      return faults.find((f) => f.id === selectedFaultId) ?? null;
    }
    return (
      faults.find((f) => f.status === "awaiting_approval") ??
      faults.find((f) => f.status !== "resolved" && f.status !== "denied") ??
      null
    );
  })();

  const hasDeniedFault = faults.some((f) => f.status === "denied");
  const hasActiveFault = faults.some(
    (f) => f.status !== "resolved" && f.status !== "denied"
  );

  // Panel shows the live fault, else a recently-resolved one (60 s), else idle.
  const displayFault = activeFault ?? retainedFault;

  function handleAssetSelect(assetId: string) {
    const fault = faults.find((f) => f.asset_id === assetId && f.status !== "resolved");
    setSelectedFaultId(fault?.id ?? null);
  }

  // ── Persistent fleet status bar ─────────────────────────────────────────────
  // "Cluster" isn't right for every pack (a laptop fleet isn't a cluster) —
  // derive a noun from fleet_label ("Cluster Health" -> "Cluster", "Fleet
  // Health" -> "Fleet") rather than hardcoding one pack's wording.
  const fleetNoun = pack?.fleet_label?.replace(/\s+Health$/i, "") || "Fleet";
  const statusBar = hasActiveFault
    ? {
        border: "1px solid rgba(249,115,22,.45)",
        icon: "⚠",
        iconColor: "#f97316",
        title: `${fleetNoun} issue detected`,
        titleColor: "#f97316",
        body: "— Agent investigating, please see Operator Dashboard for issue and remediation",
      }
    : hasDeniedFault
    ? {
        border: "1px solid rgba(239,68,68,.35)",
        icon: "⚠",
        iconColor: "#ef4444",
        title: `${fleetNoun} partially degraded`,
        titleColor: "#ef4444",
        body: "— self-heal remediation denied. Click a faulted node to review and resolve.",
      }
    : {
        border: "1px solid var(--border)",
        icon: "✓",
        iconColor: "var(--healthy)",
        title: "All systems healthy",
        titleColor: "var(--text)",
        body: "— no active faults",
      };

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-left">
          <div
            className={`app-status-dot${sseConnected ? "" : " disconnected"}`}
            title={sseConnected ? "System live" : "Reconnecting to event stream…"}
          />
          <span className="app-logo-bar">DELL × NVIDIA</span>
          <span style={{ color: "var(--border-strong)" }}>|</span>
          <span className="app-title">{pack?.sentinel_name ?? "Infrastructure Agent"}</span>
          {pack && <span className="app-pack-label">{pack.name}</span>}
        </div>
        <NotificationInbox
          notifications={notifications}
          unreadCount={unreadCount}
          onRead={handleMarkRead}
        />
      </header>

      <div className="app-body">
        {/* Persistent status bar — always visible */}
        <div style={{
          padding: "11px 16px", background: "var(--bg-card)",
          border: statusBar.border, borderRadius: 8,
          color: "var(--text-dim)", fontSize: 13,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <span style={{ color: statusBar.iconColor, fontSize: 16 }}>{statusBar.icon}</span>
          <span style={{ color: statusBar.titleColor, fontWeight: 600 }}>{statusBar.title}</span>
          <span>{statusBar.body}</span>
        </div>

        {/* Top row: Fleet Grid + Agent Activity */}
        <div className="top-row">
          <div>
            <div className="section-heading">{pack?.fleet_label ?? "Fleet Health"}</div>
            <FleetGrid assets={assets} pack={pack} onSelectAsset={handleAssetSelect} idle={!hasActiveFault} />
          </div>
          <ActivityFeed events={activity} idle={!hasActiveFault} />
        </div>

        {/* Operator Dashboard — always visible; idle shell when nothing to show */}
        <div>
          <div className="section-heading">Operator Dashboard</div>
          {/* key forces a full remount per fault — belt-and-suspenders on top of
              OperatorDashboard's own fault.id effect, so decision-lock state can
              never leak from one fault into the next regardless of reconciliation
              timing. */}
          <OperatorDashboard
            key={displayFault?.id ?? "idle"}
            fault={displayFault}
            activity={activity}
            onDecision={handleDecision}
          />
        </div>

        {/* Embedded operator terminal (ADR-012) — renders its own heading;
            hidden entirely unless the Gateway reports the feature enabled. */}
        <TerminalPanel />
      </div>
    </div>
  );
}
