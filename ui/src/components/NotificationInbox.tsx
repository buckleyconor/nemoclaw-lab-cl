import { useState } from "react";
import type { Notification } from "../types";
import { gateway } from "../api/gateway";

interface Props {
  notifications: Notification[];
  unreadCount: number;
  onRead: (id: string) => void;
}

export function NotificationInbox({ notifications, unreadCount, onRead }: Props) {
  const [open, setOpen] = useState(false);

  async function handleRead(id: string) {
    await gateway.markRead(id);
    onRead(id);
  }

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{ position: "relative", padding: "6px 12px", cursor: "pointer" }}
        aria-label="Notifications"
      >
        🔔
        {unreadCount > 0 && (
          <span
            style={{
              position: "absolute",
              top: 0,
              right: 0,
              background: "#ef4444",
              color: "#fff",
              borderRadius: "50%",
              fontSize: 10,
              width: 16,
              height: 16,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            right: 0,
            top: "100%",
            width: 320,
            background: "#fff",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            boxShadow: "0 4px 16px rgba(0,0,0,.12)",
            zIndex: 100,
            maxHeight: 400,
            overflowY: "auto",
          }}
        >
          {notifications.length === 0 && (
            <div style={{ padding: 16, color: "#6b7280" }}>No notifications</div>
          )}
          {notifications.map((n) => (
            <div
              key={n.id}
              style={{
                padding: "12px 16px",
                borderBottom: "1px solid #f3f4f6",
                background: n.read ? "#fff" : "#eff6ff",
                cursor: n.read ? "default" : "pointer",
              }}
              onClick={() => !n.read && handleRead(n.id)}
            >
              <div style={{ fontWeight: n.read ? 400 : 600 }}>{n.title}</div>
              <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>{n.body}</div>
              <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 4 }}>
                {new Date(n.ts).toLocaleTimeString()}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
