import { useCallback, useEffect, useState } from "react";
import { gateway } from "./api/gateway";
import { useSSE } from "./hooks/useSSE";
import { ActivityFeed } from "./components/ActivityFeed";
import { FleetGrid } from "./components/FleetGrid";
import { OperatorDashboard } from "./components/OperatorDashboard";
import { NotificationInbox } from "./components/NotificationInbox";
import "./App.css";
import type {
  ActivityEvent,
  AssetRecord,
  FaultEvent,
  Notification,
  PackInfo,
  SSEEvent,
} from "./types";

export default function App() {
  const [pack, setPack] = useState<PackInfo | null>(null);
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [faults, setFaults] = useState<FaultEvent[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [selectedFaultId, setSelectedFaultId] = useState<string | null>(null);

  useEffect(() => {
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
    });
  }, []);

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
    }
  }, []);

  useSSE(handleSSE);

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

  function handleAssetSelect(assetId: string) {
    const fault = faults.find((f) => f.asset_id === assetId && f.status !== "resolved");
    setSelectedFaultId(fault?.id ?? null);
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-left">
          <div className="app-status-dot" title="System live" />
          <span className="app-logo-bar">DELL × NVIDIA</span>
          <span style={{ color: "var(--border-strong)" }}>|</span>
          <span className="app-title">AI Infrastructure Sentinel</span>
          {pack && <span className="app-pack-label">{pack.name}</span>}
        </div>
        <NotificationInbox
          notifications={notifications}
          unreadCount={unreadCount}
          onRead={handleMarkRead}
        />
      </header>

      <div className="app-body">
        {/* Top row: Fleet Grid + Agent Activity */}
        <div className="top-row">
          <div>
            <div className="section-heading">{pack?.fleet_label ?? "Fleet Health"}</div>
            <FleetGrid assets={assets} pack={pack} onSelectAsset={handleAssetSelect} />
          </div>
          <ActivityFeed events={activity} />
        </div>

        {/* Operator Dashboard — full width, only when a fault is active */}
        {activeFault ? (
          <div>
            <div className="section-heading">Operator Dashboard</div>
            <OperatorDashboard
              fault={activeFault}
              activity={activity}
              onDecision={handleDecision}
            />
          </div>
        ) : hasDeniedFault && !hasActiveFault ? (
          <div style={{
            padding: "14px 16px", background: "var(--bg-card)",
            border: "1px solid rgba(239,68,68,.35)", borderRadius: 8,
            color: "var(--text-dim)", fontSize: 13,
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <span style={{ color: "#ef4444", fontSize: 16 }}>⚠</span>
            <span style={{ color: "#ef4444" }}>Cluster partially degraded</span>
            <span>— self-heal remediation denied. Click a faulted node to review and resolve.</span>
          </div>
        ) : (
          <div style={{
            padding: "14px 16px", background: "var(--bg-card)",
            border: "1px solid var(--border)", borderRadius: 8,
            color: "var(--text-dim)", fontSize: 13,
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <span style={{ color: "var(--healthy)", fontSize: 16 }}>✓</span>
            All systems healthy — no active faults
          </div>
        )}
      </div>
    </div>
  );
}
