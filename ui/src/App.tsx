import { useCallback, useEffect, useState } from "react";
import { gateway } from "./api/gateway";
import { useSSE } from "./hooks/useSSE";
import { ActivityFeed } from "./components/ActivityFeed";
import { FleetGrid } from "./components/FleetGrid";
import { FaultPanel } from "./components/FaultPanel";
import { NotificationInbox } from "./components/NotificationInbox";
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

  // Initial data load
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

  // SSE live updates
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
  }

  function handleMarkRead(id: string) {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    );
    setUnreadCount((c) => Math.max(0, c - 1));
  }

  const activeFaults = faults.filter(
    (f) => f.status !== "resolved" && f.status !== "denied"
  );

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      {/* Header */}
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 24,
          paddingBottom: 16,
          borderBottom: "2px solid #e5e7eb",
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>
            NemoClaw Infrastructure Sentinel
          </h1>
          {pack && (
            <div style={{ color: "#6b7280", fontSize: 13, marginTop: 2 }}>
              Pack: {pack.name}
            </div>
          )}
        </div>
        <NotificationInbox
          notifications={notifications}
          unreadCount={unreadCount}
          onRead={handleMarkRead}
        />
      </header>

      {/* Fleet health grid */}
      <FleetGrid assets={assets} pack={pack} onSelectAsset={() => {}} />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 340px",
          gap: 24,
          marginTop: 24,
        }}
      >
        {/* Active fault events with Approve/Deny */}
        <section>
          <h3 style={{ marginTop: 0 }}>
            Active Faults{" "}
            {activeFaults.length > 0 && (
              <span
                style={{
                  background: "#ef4444",
                  color: "#fff",
                  borderRadius: 12,
                  padding: "2px 8px",
                  fontSize: 12,
                }}
              >
                {activeFaults.length}
              </span>
            )}
          </h3>
          {activeFaults.length === 0 && (
            <div style={{ color: "#6b7280", fontSize: 13 }}>All systems healthy</div>
          )}
          {activeFaults.map((f) => (
            <FaultPanel key={f.id} fault={f} onDecision={handleDecision} />
          ))}
        </section>

        {/* Agent activity feed */}
        <ActivityFeed events={activity} />
      </div>
    </div>
  );
}
